"""Windows GUI helper. Only a local folder picker can create an authorization."""
import argparse
import asyncio
import base64
import hashlib
import importlib
import json
import os
from pathlib import Path
import queue
import secrets
import sys
import threading
import time

from file_mcp.desktop_bootstrap import read_bootstrap, has_bootstrap
from file_mcp.files import FileAccess


class LocalState:
    """Per-origin/per-download state; credentials are protected for the Windows user."""
    def __init__(self, config, directory=None):
        key=hashlib.sha256((config['server']+'\0'+config['pairing_id']).encode()).hexdigest()
        self.path=Path(directory or Path(os.environ['LOCALAPPDATA'])/'AIStudioAssistant')/(key+'.json')
        self.lock=threading.RLock()

    def load(self):
        with self.lock:
            if not self.path.exists():return {'roots':{}}
            state=json.loads(self.path.read_text(encoding='utf-8'))
            if 'credential' in state:
                import win32crypt
                state['device']=json.loads(win32crypt.CryptUnprotectData(base64.b64decode(state.pop('credential')),None,None,None,0)[1])
            return state

    def save(self, state):
        with self.lock:
            state={**state}
            if 'device' in state:
                import win32crypt
                raw=json.dumps(state.pop('device')).encode()
                state['credential']=base64.b64encode(win32crypt.CryptProtectData(raw,'AI Studio device',None,None,None,0)).decode()
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temp=self.path.with_name(self.path.name+'.'+secrets.token_hex(8)+'.tmp')
            try:
                with temp.open('x',encoding='utf-8') as stream:
                    json.dump(state,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
                os.replace(temp,self.path)
            finally:
                temp.unlink(missing_ok=True)


class DesktopFiles:
    def __init__(self, state, save):
        self.lock=threading.RLock()
        self.state=state
        self.save=save
        self.roots=state.setdefault('roots',{})
        self.access={}
        self.dirty=True  # Also finish removals interrupted by process exit.
        self.generation=0
        for id,item in self.roots.items():
            try:self.access[id]=FileAccess({id:item['path']},item['identities'])
            except Exception:pass  # Never capture a new identity on restart.

    def persist(self):self.save(self.state)

    def authorize(self, path):
        if not path:return None
        id='p'+secrets.token_hex(8)
        files=FileAccess({id:path})
        name=files.roots[id].name or '本地目录'
        with self.lock:
            if len(self.roots)>=100:raise ValueError('最多授权 100 个目录')
            self.roots[id]={'name':name,'path':str(files.roots[id]),'identities':files.identities,'registered':False}
            self.access[id]=files
            self.generation+=1;self.dirty=True
            try:self.persist()
            except Exception:
                self.roots.pop(id,None);self.access.pop(id,None)
                raise
        return id

    def revoke(self, id):
        with self.lock:
            # Remove the read capability before touching disk or the network.
            self.access.pop(id,None);self.roots.pop(id,None)
            self.generation+=1;self.dirty=True
            self.persist()

    def reconcile(self, projects, revoked_ids=()):
        assigned={p['id'] for p in projects}
        with self.lock:
            removed=[id for id,r in self.roots.items() if id in revoked_ids or (r.get('registered') and id not in assigned)]
            for id in removed:
                self.access.pop(id,None);self.roots.pop(id,None)
            if removed:
                self.generation+=1;self.dirty=True;self.persist()
        # Deliberately ignore remote path, name, and unknown IDs.

    def snapshot(self):
        with self.lock:
            return self.generation,[{'id':id,'name':r['name']} for id,r in self.roots.items()]

    def acknowledge(self, generation, roots):
        with self.lock:
            for root in roots:
                if root['id'] in self.roots:self.roots[root['id']]['registered']=True
            if self.generation==generation:self.dirty=False
            self.persist()

    def statuses(self):
        with self.lock:
            for id,r in self.roots.items():
                try:self.access[id]=FileAccess({id:r['path']},r['identities'])
                except Exception:self.access.pop(id,None)
            return [{'id':id,'ready':id in self.access,'error':'' if id in self.access else '目录不可访问或身份已改变，请移除后重新授权'} for id in self.roots]

    def display(self):
        with self.lock:return [(id,r['name'],r['path']) for id,r in self.roots.items()]

    def call(self, method, arguments):
        with self.lock:
            if method=='list_roots':return {'roots':[{'id':id,'access':'read-only'} for id in self.access]}
            files=self.access.get(arguments.get('root_id'))
            if not files:raise ValueError('目录未在本机授权或授权已撤销')
            return files.call(method,arguments)


async def synchronize(client, files):
    response=await client.get('/agent/projects');response.raise_for_status()
    payload=response.json()
    files.reconcile(payload['projects'],payload.get('revoked_ids',()))
    if files.dirty:
        generation,roots=files.snapshot()
        response=await client.post('/agent/desktop-roots',json={'roots':roots});response.raise_for_status()
        files.acknowledge(generation,roots)
    response=await client.post('/agent/status',json={'projects':files.statuses()});response.raise_for_status()
    return payload


async def run(config, state, files, notify):
    import httpx
    from file_mcp.agent import connect
    from file_mcp.desktop_turn import desktop_turn_tunnel, credentials_expire_at
    delay=2
    async with httpx.AsyncClient(base_url=config['server'],timeout=40,follow_redirects=False) as client:
        while True:
            peer=None
            try:
                with files.lock:device=state.get('device')
                if not device:
                    response=await client.post('/agent/desktop-claim',json={k:config[k] for k in ('pairing_id','ticket')})
                    response.raise_for_status();device=response.json()
                    with files.lock:
                        state['device']=device;files.persist()
                if device['expires_at']<=time.time():
                    notify('配对已到期，请从控制台重新下载助手');return
                client.headers['Authorization']='Bearer '+device['token']
                payload=await synchronize(client,files)
                notify('正在建立 P2P 连接')
                async with desktop_turn_tunnel(config['server'],device['token'],payload.get('turn_tunnel')):
                    renew_at=credentials_expire_at(payload.get('ice_servers',[])) if payload.get('turn_tunnel') else float('inf')
                    peer=await connect(client,device['device_id'],device['token'],files,payload.get('ice_servers',[]),'/agent/signal/')
                    try:
                        notify('P2P 已连接 · 仅限已授权目录');delay=2;counter=0
                        while not peer.closed.is_set() and time.time()<renew_at:
                            await asyncio.sleep(3)
                            await synchronize(client,files)
                            counter+=1
                            if counter%5==0:await peer.call('ping',{},timeout=8)
                    finally:
                        await peer.close();peer=None
            except asyncio.CancelledError:raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401,403):
                    notify('配对已失效或过期，请重新下载助手');return
                notify('连接中断，正在重试')
            except Exception:notify('连接中断，正在重试')
            finally:
                if peer:await peer.close()
            await asyncio.sleep(delay);delay=min(delay*2,30)


def self_test(report_path=None):
    result={'ok':True,'modules':{},'bootstrap':'absent'}
    for name in ('aiortc','av','tkinter','win32file','win32crypt','websockets.asyncio.client','file_mcp.desktop_turn','file_mcp.agent','file_mcp.rpc','file_mcp.files','file_mcp.safe_fs','file_mcp.desktop_bootstrap','file_mcp.desktop_assistant'):
        try:importlib.import_module(name);result['modules'][name]='ok'
        except Exception:result['modules'][name]='failed';result['ok']=False
    if getattr(sys,'frozen',False):
        try:
            if has_bootstrap(sys.executable):read_bootstrap(sys.executable);result['bootstrap']='valid'
        except Exception:result['bootstrap']='invalid';result['ok']=False
    text=json.dumps(result,ensure_ascii=False)
    if report_path:Path(report_path).write_text(text,encoding='utf-8')
    if sys.stdout:print(text)
    return 0 if result['ok'] else 1


def main():
    parser=argparse.ArgumentParser(description='AI Studio 本地只读目录助手')
    parser.add_argument('--self-test',action='store_true')
    parser.add_argument('--self-test-report',type=Path)
    args=parser.parse_args()
    if args.self_test or args.self_test_report:return self_test(args.self_test_report)
    import tkinter as tk
    from tkinter import filedialog,messagebox
    window=tk.Tk();window.title('AI Studio 本地助手');window.geometry('680x400')
    try:
        if not getattr(sys,'frozen',False):raise ValueError('请使用从控制台下载的 Windows 助手')
        config=read_bootstrap(sys.executable)
        storage=LocalState(config);state=storage.load();files=DesktopFiles(state,storage.save)
    except Exception:
        messagebox.showerror('助手无法启动','配对配置或本机状态无效，请重新下载助手。');window.destroy();return 1
    tk.Label(window,text='目标服务器：'+config['server'],wraplength=650).pack(pady=8)
    if config['server'].startswith('http:'):
        tk.Label(window,text='HTTP 兼容入口：配对与信令传输不加密。选择文件夹即明确授权此服务器只读访问。',wraplength=650,fg='#a34a00').pack()
    else:tk.Label(window,text='请选择文件夹，明确授权此服务器只读访问。').pack()
    status=tk.StringVar(value='正在连接');tk.Label(window,textvariable=status).pack(pady=6)
    listing=tk.Listbox(window);listing.pack(fill='both',expand=True,padx=12)
    events=queue.Queue();loop=asyncio.new_event_loop();task=None
    def worker():
        nonlocal task
        asyncio.set_event_loop(loop)
        task=loop.create_task(run(config,state,files,events.put))
        try:loop.run_until_complete(task)
        except asyncio.CancelledError:pass
        finally:
            loop.run_until_complete(loop.shutdown_asyncgens());loop.run_until_complete(loop.shutdown_default_executor());loop.close()
    thread=threading.Thread(target=worker,daemon=True);thread.start()
    rows=[];closing=False
    def refresh():
        nonlocal rows
        if closing:return
        while not events.empty():status.set(events.get_nowait())
        current=files.display()
        if current!=rows:
            rows=current;listing.delete(0,tk.END)
            for _,name,path in rows:listing.insert(tk.END,name+' — '+path)
        window.after(300,refresh)
    def choose():
        path=filedialog.askdirectory(parent=window,title='选择文件夹并授权此服务器只读访问')
        if path:
            try:files.authorize(path)
            except Exception:messagebox.showerror('授权失败','目录不可访问、身份校验失败或本机状态无法保存。')
    def remove():
        selected=listing.curselection()
        if selected:
            try:files.revoke(rows[selected[0]][0])
            except Exception:messagebox.showerror('保存失败','本次内存授权已撤销，但状态保存失败，请检查本机磁盘权限。')
    def quit_app():
        nonlocal closing
        if closing:return
        closing=True
        with files.lock:files.access.clear()
        if not loop.is_closed():
            def cancel():
                if task:task.cancel()
            try:loop.call_soon_threadsafe(cancel)
            except RuntimeError:pass  # Worker may have just finished and closed its loop.
        def wait():
            if thread.is_alive():window.after(100,wait)
            else:window.destroy()
        wait()
    buttons=tk.Frame(window);buttons.pack(pady=12)
    tk.Button(buttons,text='选择文件夹并授权',command=choose).pack(side='left',padx=5)
    tk.Button(buttons,text='停止授权/移除',command=remove).pack(side='left',padx=5)
    tk.Button(buttons,text='退出',command=quit_app).pack(side='left',padx=5)
    window.protocol('WM_DELETE_WINDOW',quit_app);refresh();window.mainloop();return 0


if __name__=='__main__':raise SystemExit(main())
