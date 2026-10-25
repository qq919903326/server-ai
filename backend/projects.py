"""Project library and one-time desktop helper pairing; no local disk reads here."""
import asyncio
import hashlib
import json
import os
import secrets
import time
import re
from pathlib import Path,PureWindowsPath,PurePosixPath
from types import SimpleNamespace
from uuid import uuid4
from fastapi import APIRouter,HTTPException,Request,WebSocket
from fastapi.responses import Response
from pydantic import BaseModel,Field,model_validator
from file_mcp.server import PeerHub
from backend.desktop_turn import DesktopTurnTunnel,turn_config


class ProjectInput(BaseModel):
    path: str=Field(min_length=1,max_length=2048)
    device_id: str=''

    @model_validator(mode='after')
    def check_path(self):
        self.path=self.path.strip()
        if any(ord(c)<32 for c in self.path) or not (PureWindowsPath(self.path).is_absolute() or PurePosixPath(self.path).is_absolute()) or '..' in self.path.replace('\\','/').split('/'):
            raise ValueError('请输入本地项目目录的绝对路径')
        return self


class BrowserSessionInput(BaseModel):
    device_id: str=Field(default='',max_length=64)
    token: str=Field(default='',max_length=256)


class BrowserProjectInput(BaseModel):
    name: str=Field(min_length=1,max_length=255)
    device_id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    project_id: str=Field(default='',max_length=64)

    @model_validator(mode='after')
    def check_name(self):
        if self.name in ('.','..') or any(ord(c)<32 or c in '/\\' for c in self.name):
            raise ValueError('项目目录名称无效')
        return self


class ProjectService:
    def __init__(self, store):
        self.store=store
        config=SimpleNamespace(devices={d['id']:'paired' for d in store.all('devices')},ice_servers=json.loads(os.getenv('FILE_ICE_SERVERS_JSON','[]')))
        self.hub=PeerHub(config)
        self.turn_tunnel=DesktopTurnTunnel(self.authenticated_device)

    def authenticated_device(self, request):
        token=request.headers.get('authorization','').removeprefix('Bearer ')
        digest=hashlib.sha256(token.encode()).hexdigest()
        for device in self.store.all('devices'):
            if secrets.compare_digest(device['token_hash'],digest) and device.get('expires_at',float('inf'))>time.time():return device
        raise HTTPException(401,'本地助手未配对或配对已撤销')

    def project_tool_config(self, project):
        return {'id':'project-'+project['id'],'name':'文件库 · '+project['name'],'enabled':True,
                'service_type':'project','project_id':project['id'],'transport':'http',
                'allowed_tools':['list_directory','file_info','read_text'],'url':''}

    async def call(self, id, method, arguments):
        project=self.store.get('projects',id)
        if not project:raise ValueError('项目已移除')
        device=self.store.get('devices',project['device_id'])
        if not device or device.get('expires_at',float('inf'))<=time.time():raise ValueError('设备配对已失效')
        if device.get('kind')=='desktop' and (not self.hub.online(device['id']) or not device.get('project_status',{}).get(id,{}).get('ready')):
            raise ValueError('桌面目录尚未就绪或设备离线')
        # Device and root are always server-bound, never accepted from model arguments.
        return await self.hub.call(project['device_id'],method,{**arguments,'root_id':id})

    def router(self):
        router=APIRouter()

        @router.get('/api/projects')
        async def projects():
            result=[]
            for project in self.store.all('projects'):
                device=self.store.get('devices',project['device_id']) or {}
                state=device.get('project_status',{}).get(project['id'],{})
                result.append({**project,'online':self.hub.online(project['device_id']) and device.get('expires_at',float('inf'))>time.time(),
                               'ready':state.get('ready',False),'error':state.get('error','')})
            return result

        @router.get('/api/projects/assistant-status')
        async def status():
            return {'devices':[{'id':d['id'],'kind':d.get('kind','legacy'),'online':self.hub.online(d['id']) and d.get('expires_at',float('inf'))>time.time()} for d in self.store.all('devices')]}

        @router.post('/api/projects/browser-session')
        async def browser_session(body: BrowserSessionInput):
            if body.device_id:
                device=self.store.get('devices',body.device_id)
                if not device or device.get('kind')!='browser' or not secrets.compare_digest(device['token_hash'],hashlib.sha256(body.token.encode()).hexdigest()):
                    raise HTTPException(401,'浏览器配对已失效，请重新授权项目目录')
                return {'device_id':body.device_id,'token':body.token,'ice_servers':self.hub.config.ice_servers}
            if len(self.store.all('devices'))>=32:raise HTTPException(409,'配对数量已达上限，请移除不再使用的助手或浏览器')
            id='web-'+uuid4().hex[:12]
            token=secrets.token_urlsafe(40)
            self.store.put('devices',id,{'kind':'browser','token_hash':hashlib.sha256(token.encode()).hexdigest()})
            self.hub.config.devices[id]='paired'
            self.hub.locks[id]=asyncio.Lock()
            return {'device_id':id,'token':token,'ice_servers':self.hub.config.ice_servers}

        @router.post('/api/projects/browser')
        async def browser_project(body: BrowserProjectInput):
            device=self.store.get('devices',body.device_id)
            if not device or device.get('kind')!='browser':raise HTTPException(409,'浏览器尚未配对，请重新选择项目目录')
            if body.project_id:
                if not self.store.get('projects',body.project_id):raise HTTPException(404,'项目已被移除')
                id=body.project_id
                current=self.store.get('projects',id)
                if current.get('source')=='desktop':raise HTTPException(409,'桌面目录必须在本机重新授权')
            else:
                if len(self.store.all('projects'))>=100:raise HTTPException(409,'项目数量已达 100 个')
                id='p'+uuid4().hex[:16]
            project=self.store.put('projects',id,{'name':body.name,'path':body.name,'device_id':body.device_id,'source':'browser'})
            self.store.put('mcp','project-'+id,self.project_tool_config(project))
            # Reset stale ready flags when the directory is re-authorized.
            states={**device.get('project_status',{})}
            states.pop(id,None)
            self.store.put('devices',device['id'],{**device,'project_status':states})
            return project

        @router.post('/api/projects/assistant-download')
        async def download(request: Request):
            # Remote pairing endpoints must be protected by the administrator's APP_TOKEN.
            if not os.getenv('APP_TOKEN') and request.client.host not in ('127.0.0.1','::1','testclient'):
                raise HTTPException(403,'部署到服务器后请先配置 APP_TOKEN，再下载本地助手')
            server=str(request.base_url).rstrip('/')
            if len(self.store.all('devices'))>=32:raise HTTPException(409,'助手数量已达上限，请先移除旧助手')
            root=Path(__file__).resolve().parent.parent
            template=Path(os.getenv('AI_ASSISTANT_EXE',str(root/'assistant_dist/AIStudioAssistant.exe')))
            try:binary=template.read_bytes()
            except OSError:raise HTTPException(503,'Windows 助手尚未构建：请先构建 AIStudioAssistant.exe') from None
            from file_mcp.desktop_bootstrap import build_download
            id='pair-'+uuid4().hex
            ticket=secrets.token_urlsafe(40)
            expires=time.time()+600
            config={'version':1,'server':server,'pairing_id':id,'ticket':ticket,'expires_at':expires}
            binary=build_download(binary,config)
            with self.store.lock:
                for old in self.store.all('desktop_tickets'):
                    if old['expires_at']<=time.time():self.store.delete('desktop_tickets',old['id'])
                if len(self.store.all('desktop_tickets'))>=100:raise HTTPException(429,'待配对下载过多，请稍后重试')
                self.store.put('desktop_tickets',id,{'ticket_hash':hashlib.sha256(ticket.encode()).hexdigest(),'expires_at':expires})
            return Response(binary,media_type='application/vnd.microsoft.portable-executable',headers={'Content-Disposition':'attachment; filename="AIStudioAssistant.exe"','Cache-Control':'no-store'})

        @router.post('/agent/desktop-claim')
        async def claim(request: Request):
            data=await bounded_json(request)
            if set(data)!={'pairing_id','ticket'} or not all(isinstance(v,str) and len(v)<=256 for v in data.values()):raise HTTPException(400,'配对格式错误')
            # One SQLite write transaction: competing workers cannot redeem the same ticket.
            with self.store.lock,self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT body FROM records WHERE kind=? AND id=?',('desktop_tickets',data['pairing_id'])).fetchone()
                record=json.loads(row[0]) if row else None
                if not record or record['expires_at']<=time.time() or not secrets.compare_digest(record['ticket_hash'],hashlib.sha256(data['ticket'].encode()).hexdigest()):raise HTTPException(401,'配对已过期或已使用，请重新下载助手')
                if db.execute('SELECT count(*) FROM records WHERE kind=?',('devices',)).fetchone()[0]>=32:raise HTTPException(409,'设备数量已达上限')
                id='pc-'+uuid4().hex[:16];token=secrets.token_urlsafe(40);expires=time.time()+30*86400
                device={'id':id,'kind':'desktop','token_hash':hashlib.sha256(token.encode()).hexdigest(),'expires_at':expires,'project_status':{}}
                db.execute('DELETE FROM records WHERE kind=? AND id=?',('desktop_tickets',data['pairing_id']))
                db.execute('INSERT INTO records VALUES (?,?,?,?)',('devices',id,json.dumps(device),str(time.time())))
            self.hub.config.devices[id]='paired';self.hub.locks[id]=asyncio.Lock()
            return {'device_id':id,'token':token,'expires_at':expires,'kind':'desktop'}

        @router.post('/agent/desktop-roots')
        async def desktop_roots(request: Request):
            self.authenticated_device(request)
            data=await bounded_json(request)
            if set(data)!={'roots'} or not isinstance(data['roots'],list) or len(data['roots'])>100:raise HTTPException(400,'目录列表无效（最多 100 个）')
            roots={}
            for item in data['roots']:
                if not isinstance(item,dict) or set(item)!={'id','name'} or not isinstance(item['id'],str) or not re.fullmatch(r'p[0-9a-f]{16}',item['id']) or not isinstance(item['name'],str) or not 1<=len(item['name'])<=255 or item['name'] in ('.','..') or any(ord(c)<32 or c in '/\\' for c in item['name']) or item['id'] in roots:raise HTTPException(400,'目录只能包含有效 id 和名称，不能包含路径')
                roots[item['id']]=item['name']
            with self.store.lock:
                device=self.authenticated_device(request)
                if device.get('kind')!='desktop':raise HTTPException(403,'仅桌面设备可登记目录')
                existing={p['id']:p for p in self.store.all('projects')}
                if any(self.store.get('desktop_revoked_roots',id) for id in roots):raise HTTPException(409,'目录已撤销，请在本机重新选择文件夹')
                if any(id in existing and existing[id]['device_id']!=device['id'] for id in roots):raise HTTPException(409,'目录 ID 已由其他设备使用')
                if len([p for p in existing.values() if p['device_id']!=device['id']])+len(roots)>100:raise HTTPException(409,'项目数量已达 100 个')
                for id,p in existing.items():
                    if p['device_id']==device['id'] and id not in roots:
                        self.store.delete('projects',id);self.store.delete('mcp','project-'+id)
                for id,name in roots.items():
                    project=self.store.put('projects',id,{'name':name,'device_id':device['id'],'source':'desktop'})
                    self.store.put('mcp','project-'+id,self.project_tool_config(project))
                states={id:s for id,s in device.get('project_status',{}).items() if id in roots}
                self.store.put('devices',device['id'],{**device,'project_status':states})
            return {'ok':True,'projects':[{'id':id,'name':name} for id,name in roots.items()]}

        @router.post('/api/projects')
        async def add(body: ProjectInput):
            devices=self.store.all('devices')
            device=body.device_id or (devices[0]['id'] if len(devices)==1 else '')
            if not device or not self.store.get('devices',device):raise HTTPException(409,'请先在这台电脑下载并启动本地助手，再添加项目')
            if self.store.get('devices',device).get('kind')=='desktop':raise HTTPException(409,'请在桌面助手中选择文件夹并授权')
            if len(self.store.all('projects'))>=100:raise HTTPException(409,'项目数量已达 100 个')
            for project in self.store.all('projects'):
                if project['device_id']==device and project['path']==body.path:return project
            id='p'+uuid4().hex[:16]
            name=body.path.replace('\\','/').rstrip('/').rsplit('/',1)[-1] or body.path
            project=self.store.put('projects',id,{'name':name,'path':body.path,'device_id':device})
            self.store.put('mcp','project-'+id,self.project_tool_config(project))
            return project

        @router.delete('/api/projects/{id}')
        async def remove(id: str):
            with self.store.lock:
                project=self.store.get('projects',id)
                if project and project.get('source')=='desktop':self.store.put('desktop_revoked_roots',id,{'device_id':project['device_id']})
                self.store.delete('projects',id);self.store.delete('mcp','project-'+id)
            return {'ok':True}

        @router.delete('/api/projects/assistants/{id}')
        async def revoke(id: str):
            # Invalidate credentials before the first await so in-flight requests fail revalidation.
            self.store.delete('devices',id)
            self.hub.config.devices.pop(id,None)
            peer=self.hub.peers.pop(id,None)
            if peer:await peer.close()
            pc=self.hub.connections.pop(id,None)
            if pc:await pc.close()
            return {'ok':True}

        @router.get('/agent/projects')
        async def assigned(request: Request):
            device=self.authenticated_device(request)
            result={'projects':[({'id':p['id'],'name':p['name']} if device.get('kind')=='desktop' else {'id':p['id'],'path':p['path']}) for p in self.store.all('projects') if p['device_id']==device['id']], 'ice_servers':self.hub.config.ice_servers}
            if device.get('kind')=='desktop':result['revoked_ids']=[r['id'] for r in self.store.all('desktop_revoked_roots') if r['device_id']==device['id']]
            turn=turn_config(device)
            if turn:
                result['turn_tunnel']=turn['turn_tunnel']
                # aiortc selects the first TURN server; prefer the reachable tunnel.
                result['ice_servers']=[turn['ice_server'],*result['ice_servers']]
            return result

        @router.websocket('/agent/desktop-turn')
        async def desktop_turn(websocket: WebSocket):
            await self.turn_tunnel.handle(websocket)

        @router.post('/agent/status')
        async def report(request: Request):
            device=self.authenticated_device(request)
            data=await bounded_json(request)
            device=self.authenticated_device(request)
            assigned={p['id'] for p in self.store.all('projects') if p['device_id']==device['id']}
            states={}
            for item in data.get('projects',[])[:100]:
                if isinstance(item,dict) and item.get('id') in assigned:
                    states[item['id']]={'ready':item.get('ready') is True,'error':str(item.get('error',''))[:200]}
            self.store.put('devices',device['id'],{**device,'project_status':states})
            return {'ok':True}

        @router.post('/agent/signal/{id}')
        async def signal(id: str,request: Request):
            device=self.authenticated_device(request)
            if device['id']!=id:raise HTTPException(403,'设备不匹配')
            data=await bounded_json(request)
            self.authenticated_device(request)
            if data.get('type')!='offer' or not isinstance(data.get('sdp'),str):raise HTTPException(400,'信令格式错误')
            try:
                result=await self.hub.offer(id,data['sdp'])
                self.authenticated_device(request)
                return result
            except Exception:
                if not self.store.get('devices',id):
                    peer=self.hub.peers.pop(id,None)
                    if peer:await peer.close()
                    pc=self.hub.connections.pop(id,None)
                    if pc:await pc.close()
                    raise HTTPException(401,'助手配对已撤销') from None
                raise HTTPException(400,'无法建立 P2P 连接，请检查网络或 ICE 配置') from None
        return router


async def bounded_json(request):
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>65536:raise HTTPException(413,'请求过大')
    try:
        value=json.loads(body)
        if not isinstance(value,dict):raise ValueError()
        return value
    except ValueError:raise HTTPException(400,'请求格式错误') from None
