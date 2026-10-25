"""Run on the file owner's computer. No inbound listener or privilege escalation."""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from aiortc import RTCPeerConnection, RTCSessionDescription
from dotenv import load_dotenv
from .config import rtc_config
from .files import FileAccess
from .rpc import RpcPeer

log = logging.getLogger('file-agent')


async def connect(client,device,token,files,ice_servers,signal_path='/signal/'):
    pc = RTCPeerConnection(rtc_config(ice_servers))
    peer = RpcPeer(pc,pc.createDataChannel('files-v1'),files.call)
    try:
        async with asyncio.timeout(60):
            await pc.setLocalDescription(await pc.createOffer())
            response = await client.post(signal_path+device,
                headers={'Authorization':'Bearer '+token},
                json={'type':'offer','sdp':pc.localDescription.sdp})
            response.raise_for_status()
            answer = response.json()
            await pc.setRemoteDescription(RTCSessionDescription(sdp=answer['sdp'],type='answer'))
            await asyncio.wait_for(peer.ready.wait(),25)
        return peer
    except BaseException:
        await peer.close()
        raise


def validate_settings(settings):
    import re
    url = urlsplit(settings.get('server',''))
    if url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        raise ValueError('服务地址应为独立 MCP 服务的根地址，不含用户信息、路径或参数')
    if url.scheme!='https' and not (url.scheme=='http' and url.hostname in ('127.0.0.1','localhost','::1')):
        raise ValueError('远程信令必须使用 HTTPS；HTTP 仅允许本机测试')
    if not url.hostname or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',settings.get('device_id','')):
        raise ValueError('服务地址或设备 ID 无效')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',settings.get('token_env','')):
        raise ValueError('请配置设备 Token 环境变量名')
    rtc_config(settings.get('ice_servers',[]))
    return FileAccess(settings.get('roots',{}),settings.get('root_identities'))


async def run(settings):
    files = validate_settings(settings)
    delay = 2
    async with httpx.AsyncClient(base_url=settings['server'].rstrip('/'),timeout=40,follow_redirects=False) as client:
        while True:
            peer = None
            try:
                token = os.getenv(settings['token_env'],'')
                if len(token)<32:raise ValueError('设备 Token 环境变量未设置或不足 32 字符')
                peer = await connect(client,settings['device_id'],token,files,settings.get('ice_servers',[]))
                log.info('设备已连接；授权目录：%s',', '.join(files.roots))
                delay = 2
                while not peer.closed.is_set():
                    await asyncio.sleep(15)
                    await peer.call('ping',{},timeout=8)
            except asyncio.CancelledError:
                raise
            except Exception:
                # No secrets, SDP, file contents or absolute paths in logs.
                log.warning('连接失败或断开；%s 秒后重试。请检查设备 Token、服务地址和 ICE 网络。',delay)
            finally:
                if peer:await peer.close()
            await asyncio.sleep(delay)
            delay = min(delay*2,30)


def authorize(config_path,server,device,token_env,allow):
    roots = {}
    if allow:
        for item in allow:
            alias,separator,path = item.partition('=')
            if not separator:raise ValueError('--allow 格式为 别名=本地目录')
            roots[alias] = path
    else:
        import tkinter as tk
        from tkinter import filedialog
        window = tk.Tk();window.withdraw()
        try:path = filedialog.askdirectory(title='授权此文件 MCP 读取的目录（仅限所选目录）')
        finally:window.destroy()
        if not path:raise ValueError('未选择目录，未保存授权')
        roots['files'] = path
    settings = {'server':server,'device_id':device,'token_env':token_env,
                'roots':{alias:str(Path(path).expanduser().resolve(strict=True)) for alias,path in roots.items()},'ice_servers':[]}
    files=validate_settings(settings)
    settings['root_identities']=files.identities
    config_path.parent.mkdir(parents=True,exist_ok=True)
    # Only explicit local CLI authorization writes this configuration.
    config_path.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding='utf-8')
    try:config_path.chmod(0o600)
    except OSError:pass


def main():
    parser = argparse.ArgumentParser(description='P2P 只读文件代理；首次使用 --authorize 保存授权目录')
    parser.add_argument('--config',type=Path,default=Path('file_mcp/agent.local.json'))
    parser.add_argument('--env-file',type=Path,default=Path('file_mcp/agent.env'))
    parser.add_argument('--authorize',action='store_true')
    parser.add_argument('--server')
    parser.add_argument('--device',default='my-pc')
    parser.add_argument('--token-env',default='FILE_DEVICE_TOKEN')
    parser.add_argument('--allow',action='append',help='本地明确授权，例如 docs=E:\\Documents，可重复')
    args = parser.parse_args()
    load_dotenv(args.env_file)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    if args.authorize:
        if not args.server:parser.error('--authorize 需要 --server')
        authorize(args.config,args.server,args.device,args.token_env,args.allow)
        print('已保存只读授权；再次运行代理时自动使用此配置。')
        return
    if not args.config.is_file():parser.error('没有本地授权配置；请先运行 --authorize')
    settings = json.loads(args.config.read_text(encoding='utf-8'))
    try:asyncio.run(run(settings))
    except KeyboardInterrupt:pass


if __name__=='__main__':main()
