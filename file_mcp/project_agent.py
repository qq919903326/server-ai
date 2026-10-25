"""Paired desktop helper: project paths are explicitly managed in AI Studio."""
import asyncio
import json
import logging
import threading
from pathlib import Path
import httpx
from .agent import connect,validate_settings
from .files import FileAccess


class ProjectFiles:
    def __init__(self, cache):
        self.cache=Path(cache)
        self.lock=threading.RLock()
        self.access={}
        self.saved=json.loads(self.cache.read_text(encoding='utf-8')) if self.cache.exists() else {}

    def sync(self, projects):
        with self.lock:
            access,saved,statuses={},{},[]
            for project in projects:
                id,path=project['id'],project['path']
                previous=self.saved.get(id,{})
                try:
                    identities=previous.get('identities') if previous.get('path')==path else None
                    files=FileAccess({id:path},identities)
                    access[id]=files
                    saved[id]={'path':path,'identities':files.identities}
                    statuses.append({'id':id,'ready':True,'error':''})
                except Exception:
                    # Preserve identity after a failed check; never auto-authorize a replacement.
                    saved[id]=previous if previous else {'path':path}
                    statuses.append({'id':id,'ready':False,'error':'目录不存在、没有读取权限或目录已被替换；请检查路径，必要时移除后重新添加。'})
            self.access,self.saved=access,saved
            self.cache.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.cache.with_suffix('.tmp')
            temporary.write_text(json.dumps(saved,ensure_ascii=False),encoding='utf-8')
            temporary.replace(self.cache)
            return statuses

    def call(self, method, arguments):
        with self.lock:
            if method=='list_roots':return {'roots':[{'id':id,'access':'read-only'} for id in self.access]}
            files=self.access.get(arguments.get('root_id'))
            if not files:raise ValueError('项目未添加、已移除或本地目录不可访问')
            return files.call(method,arguments)


async def run(config, cache):
    # Same HTTPS and device validation as the explicit-directory agent.
    from urllib.parse import urlsplit
    url=urlsplit(config['server'])
    if url.scheme!='https' and not (url.scheme=='http' and url.hostname in ('127.0.0.1','localhost','::1')):
        raise ValueError('远程助手必须连接 HTTPS 服务')
    if url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('助手连接配置无效')
    files=ProjectFiles(cache)
    headers={'Authorization':'Bearer '+config['token']}
    delay=2
    async with httpx.AsyncClient(base_url=config['server'].rstrip('/'),headers=headers,timeout=40,follow_redirects=False) as client:
        while True:
            peer=None
            try:
                response=await client.get('/agent/projects');response.raise_for_status()
                payload=response.json()
                statuses=await asyncio.to_thread(files.sync,payload['projects'])
                await client.post('/agent/status',json={'projects':statuses})
                peer=await connect(client,config['device_id'],config['token'],files,payload.get('ice_servers',[]),'/agent/signal/')
                logging.info('本地助手已连接。请在控制台“本地项目库”添加项目路径。')
                delay=2
                counter=0
                while not peer.closed.is_set():
                    await asyncio.sleep(3)
                    response=await client.get('/agent/projects');response.raise_for_status()
                    statuses=await asyncio.to_thread(files.sync,response.json()['projects'])
                    status=await client.post('/agent/status',json={'projects':statuses});status.raise_for_status()
                    counter+=1
                    if counter%5==0:await peer.call('ping',{},timeout=8)
            except asyncio.CancelledError:raise
            except Exception:logging.warning('助手连接中断，%s 秒后自动重试。',delay)
            finally:
                if peer:await peer.close()
            await asyncio.sleep(delay)
            delay=min(delay*2,30)


def main():
    import argparse
    parser=argparse.ArgumentParser(description='AI Studio 本地项目助手')
    parser.add_argument('--config',type=Path,default=Path('connection.local.json'))
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    config=json.loads(args.config.read_text(encoding='utf-8'))
    print('此助手信任配对的 AI Studio：你在该控制台添加的项目目录会被只读访问。关闭此窗口停止访问。')
    try:asyncio.run(run(config,args.config.with_name('project-roots.local.json')))
    except KeyboardInterrupt:pass


if __name__=='__main__':main()
