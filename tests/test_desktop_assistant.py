import asyncio
import copy
import hashlib
import json
import struct
import sys
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.projects import ProjectService
from backend.store import Store
from file_mcp.desktop_bootstrap import build_download, read_bootstrap, MAGIC, MAX_JSON, TRAILER_SIZE
from file_mcp.desktop_assistant import DesktopFiles, LocalState, synchronize, self_test
from test_api import client


def config():
    return {'server':'http://203.0.113.10:8000','pairing_id':'pair-test','ticket':'a'*40,'expires_at':time.time()+600,'version':1}


def unpack(tmp_path, binary):
    exe=tmp_path/'download.exe';exe.write_bytes(binary)
    return read_bootstrap(exe)


def test_bootstrap_bounded_and_corruption(tmp_path):
    cfg=config();binary=build_download(b'MZ-template',cfg)
    assert unpack(tmp_path,binary)==cfg
    for damaged in (binary[:-1],binary[:-TRAILER_SIZE-1]+b'X'+binary[-TRAILER_SIZE:],
                    b'MZ'+struct.pack('>I',MAX_JSON+1)+b'0'*32+MAGIC,
                    b'MZ'+struct.pack('>I',0)+b'0'*32+MAGIC):
        with pytest.raises(ValueError):unpack(tmp_path,damaged)
    with pytest.raises(ValueError):build_download(b'MZ',{**cfg,'version':2})
    with pytest.raises(ValueError):build_download(b'MZ',{**cfg,'path':'C:/secret'})
    with pytest.raises(ValueError):build_download(b'MZ',{**cfg,'server':'http://'+('x'*MAX_JSON)+'.test'})


@pytest.fixture
def api(tmp_path,monkeypatch):
    template=tmp_path/'template.exe';template.write_bytes(b'MZ-test-template')
    monkeypatch.setenv('AI_ASSISTANT_EXE',str(template))
    monkeypatch.setenv('APP_TOKEN','admin-test-token')
    service=ProjectService(Store(tmp_path/'db'))
    app=FastAPI();app.include_router(service.router())
    with TestClient(app,base_url='http://203.0.113.10:8000') as client:
        yield client,service


def pair(api,tmp_path):
    client,service=api
    download=client.post('/api/projects/assistant-download')
    assert download.status_code==200,download.text
    assert download.headers['content-type']=='application/vnd.microsoft.portable-executable'
    assert 'AIStudioAssistant.exe' in download.headers['content-disposition']
    cfg=unpack(tmp_path,download.content)
    assert cfg['server']=='http://203.0.113.10:8000'
    stored=service.store.get('desktop_tickets',cfg['pairing_id'])
    assert 'ticket' not in stored and stored['ticket_hash']==hashlib.sha256(cfg['ticket'].encode()).hexdigest()
    response=client.post('/agent/desktop-claim',json={k:cfg[k] for k in ('pairing_id','ticket')})
    assert response.status_code==200,response.text
    return cfg,response.json()


def auth(device):return {'Authorization':'Bearer '+device['token']}


def test_ticket_replay_expiration_and_isolation(api,tmp_path):
    c,s=api
    cfg,d=pair(api,tmp_path)
    body={k:cfg[k] for k in ('pairing_id','ticket')}
    assert c.post('/agent/desktop-claim',json=body).status_code==401
    assert d['kind']=='desktop' and d['token']!='admin-test-token'
    assert c.get('/agent/projects',headers={'Authorization':'Bearer admin-test-token'}).status_code==401
    assert c.get('/agent/projects',headers=auth(d)).status_code==200
    assert d['token'] not in json.dumps(s.store.all('devices'))
    download=c.post('/api/projects/assistant-download')
    cfg=unpack(tmp_path,download.content)
    record=s.store.get('desktop_tickets',cfg['pairing_id'])
    s.store.put('desktop_tickets',cfg['pairing_id'],{**record,'expires_at':time.time()-1})
    assert c.post('/agent/desktop-claim',json={k:cfg[k] for k in ('pairing_id','ticket')}).status_code==401
    record=s.store.get('devices',d['device_id'])
    s.store.put('devices',d['device_id'],{**record,'expires_at':time.time()-1})
    assert c.get('/agent/projects',headers=auth(d)).status_code==401
    assert c.post('/agent/desktop-claim',content=b'x'*65537).status_code==413


def test_claim_simultaneous_once(api,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    c,s=api
    cfg=unpack(tmp_path,c.post('/api/projects/assistant-download').content)
    body={k:cfg[k] for k in ('pairing_id','ticket')}
    with ThreadPoolExecutor(2) as pool:
        codes=list(pool.map(lambda _:c.post('/agent/desktop-claim',json=body).status_code,range(2)))
    assert sorted(codes)==[200,401]
    assert len(s.store.all('devices'))==1


def test_roots_strict_idempotent_revoked_and_tools(api,tmp_path):
    c,s=api;_,d=pair(api,tmp_path);_,other=pair(api,tmp_path)
    root={'id':'p'+'1'*16,'name':'目录'}
    for body in ({'roots':[{**root,'path':'C:/secret'}]}, {'roots':[root],'path':'x'},
                 {'roots':[{**root,'id':'../x'}]}, {'roots':[root]*101}, {'roots':[root,root]}):
        assert c.post('/agent/desktop-roots',headers=auth(d),json=body).status_code==400
    for _ in range(2):assert c.post('/agent/desktop-roots',headers=auth(d),json={'roots':[root]}).status_code==200
    assert len(s.store.all('projects'))==1
    assert 'path' not in s.store.all('projects')[0]
    assigned=c.get('/agent/projects',headers=auth(d)).json()
    assert assigned['projects']==[root] and 'ice_servers' in assigned
    assert c.post('/agent/desktop-roots',headers=auth(other),json={'roots':[root]}).status_code==409
    assert c.post('/api/projects',json={'device_id':d['device_id'],'path':'C:/secret'}).status_code==409
    seen=[]
    async def call(device,method,args):seen.append((device,args));return {}
    s.hub.call=call
    with pytest.raises(ValueError):asyncio.run(s.call(root['id'],'read_text',{}))
    c.post('/agent/status',headers=auth(d),json={'projects':[{'id':root['id'],'ready':True}]})
    s.hub.online=lambda id:True
    asyncio.run(s.call(root['id'],'read_text',{'root_id':'injected','path':'test'}))
    assert seen==[(d['device_id'],{'root_id':root['id'],'path':'test'})]
    c.delete('/api/projects/'+root['id'])
    with pytest.raises(ValueError):asyncio.run(s.call(root['id'],'read_text',{}))
    assert c.post('/agent/desktop-roots',headers=auth(d),json={'roots':[root]}).status_code==409
    assert root['id'] in c.get('/agent/projects',headers=auth(d)).json()['revoked_ids']
    assert c.get('/api/projects/assistant-status').json()['devices'][0]['kind']=='desktop'
    browser=c.post('/api/projects/browser-session',json={}).json()
    assert c.post('/agent/desktop-roots',headers=auth(browser),json={'roots':[]}).status_code==403


def test_missing_template_503(api,monkeypatch,tmp_path):
    monkeypatch.setenv('AI_ASSISTANT_EXE',str(tmp_path/'absent.exe'))
    response=api[0].post('/api/projects/assistant-download')
    assert response.status_code==503 and '尚未构建' in response.text
    assert api[1].store.all('desktop_tickets')==[]


def test_local_only_authorization_revoke_and_identity(tmp_path):
    root=tmp_path/'selected';root.mkdir();(root/'hello.txt').write_text('local')
    saved=[];files=DesktopFiles({'roots':{}},lambda s:saved.append(copy.deepcopy(s)))
    assert files.authorize('') is None and not saved
    files.reconcile([{'id':'remote','path':str(root),'name':'attack'}])
    with pytest.raises(ValueError):files.call('read_text',{'root_id':'remote','path':'hello.txt'})
    id=files.authorize(str(root))
    assert files.call('read_text',{'root_id':id,'path':'hello.txt'})['text']=='local'
    files.reconcile([{'id':id,'path':str(tmp_path),'name':'attack'}])
    with pytest.raises(ValueError):files.call('read_text',{'root_id':id,'path':'../hello.txt'})
    generation,roots=files.snapshot();files.acknowledge(generation,roots)
    files.reconcile([])
    assert not files.display()
    with pytest.raises(ValueError):files.call('read_text',{'root_id':id,'path':'hello.txt'})
    id=files.authorize(str(root));state=copy.deepcopy(files.state)
    root.rename(tmp_path/'old');root.mkdir()
    restarted=DesktopFiles(state,lambda s:None)
    assert not restarted.statuses()[0]['ready']
    files.revoke(id)
    with pytest.raises(ValueError):files.call('list_directory',{'root_id':id})


def test_poll_does_not_resurrect_web_deletion(tmp_path):
    root=tmp_path/'selected';root.mkdir()
    files=DesktopFiles({'roots':{}},lambda s:None);id=files.authorize(str(root))
    posts=[];projects=[];revoked=[]
    def handler(request):
        if request.method=='GET':return httpx.Response(200,json={'projects':projects,'ice_servers':[],'revoked_ids':revoked})
        body=json.loads(request.content)
        if request.url.path.endswith('desktop-roots'):
            posts.append(body);projects[:]=body['roots']
        return httpx.Response(200,json={'ok':True})
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url='http://test') as c:
            await synchronize(c,files);await synchronize(c,files)
            assert len(posts)==1
            projects.clear();revoked.append(id)
            await synchronize(c,files);await synchronize(c,files)
            assert posts[-1]=={'roots':[]} and not files.display()
    asyncio.run(scenario())


def test_dpapi_isolated_atomic_state(tmp_path,monkeypatch):
    fake=SimpleNamespace(CryptProtectData=lambda b,*a:b'protected:'+b,
                         CryptUnprotectData=lambda b,*a:('',b.removeprefix(b'protected:')))
    monkeypatch.setitem(sys.modules,'win32crypt',fake)
    a=LocalState(config(),tmp_path)
    b=LocalState({**config(),'pairing_id':'other'},tmp_path)
    state={'roots':{},'device':{'token':'private-device-token'}}
    a.save(state)
    assert 'private-device-token' not in a.path.read_text()
    assert a.load()==state and b.load()=={'roots':{}}
    assert not list(tmp_path.glob('*.tmp'))


def test_self_test_no_gui_or_network(tmp_path,monkeypatch):
    import file_mcp.desktop_assistant as module
    imported=[]
    monkeypatch.setattr(module.importlib,'import_module',lambda n:imported.append(n))
    report=tmp_path/'report.json'
    assert self_test(report)==0
    assert {'aiortc','av','tkinter','win32crypt','win32file'}<=set(imported)
    assert 'ticket' not in report.read_text() and 'token' not in report.read_text()


def test_frozen_self_test_checks_overlay_without_secret_output(tmp_path,monkeypatch):
    import file_mcp.desktop_assistant as module
    exe=tmp_path/'assistant.exe';exe.write_bytes(build_download(b'MZ-test',config()))
    monkeypatch.setattr(module.importlib,'import_module',lambda n:None)
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(exe))
    report=tmp_path/'report.json'
    assert self_test(report)==0
    assert json.loads(report.read_text())['bootstrap']=='valid'
    binary=bytearray(exe.read_bytes());binary[-TRAILER_SIZE+4]^=1;exe.write_bytes(binary)
    assert self_test(report)==1
    assert json.loads(report.read_text())['bootstrap']=='invalid'
    assert 'ticket' not in report.read_text() and 'token' not in report.read_text()


def test_download_app_auth_and_device_cannot_manage_models(client,tmp_path,monkeypatch):
    template=tmp_path/'template.exe';template.write_bytes(b'MZ-test')
    monkeypatch.setenv('AI_ASSISTANT_EXE',str(template))
    url='/api/projects/assistant-download'
    assert client.post(url,headers={'Authorization':''}).status_code==401
    cfg=unpack(tmp_path,client.post(url,headers={'Host':'203.0.113.10:8000'}).content)
    assert cfg['server']=='http://203.0.113.10:8000'
    d=client.post('/agent/desktop-claim',json={k:cfg[k] for k in ('pairing_id','ticket')}).json()
    for path in ('/api/models','/api/mcp',url):
        method=client.post if path==url else client.get
        assert method(path,headers=auth(d)).status_code==401


def test_connection_cancellation_closes_peer_without_network(tmp_path,monkeypatch):
    import file_mcp.desktop_assistant as module
    import file_mcp.agent as agent
    state={'roots':{}};files=DesktopFiles(state,lambda s:None)
    async def scenario():
        connected=asyncio.Event();closed=[];paths=[]
        class Peer:
            def __init__(self):self.closed=asyncio.Event()
            async def close(self):closed.append(True);self.closed.set()
        async def connect(c,device,token,access,ice,signal):
            assert signal=='/agent/signal/' and access is files
            assert device=='desktop-test' and token=='device-secret'
            connected.set();return Peer()
        def handler(request):
            paths.append(request.url.path)
            if request.url.path.endswith('desktop-claim'):
                return httpx.Response(200,json={'device_id':'desktop-test','token':'device-secret','kind':'desktop','expires_at':time.time()+100})
            assert request.headers['authorization']=='Bearer device-secret'
            if request.method=='GET':return httpx.Response(200,json={'projects':[],'ice_servers':[]})
            return httpx.Response(200,json={'ok':True})
        original=httpx.AsyncClient
        monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
        monkeypatch.setattr(agent,'connect',connect)
        task=asyncio.create_task(module.run(config(),state,files,lambda s:None))
        await asyncio.wait_for(connected.wait(),3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert closed==[True]
        assert paths==['/agent/desktop-claim','/agent/projects','/agent/desktop-roots','/agent/status']
    asyncio.run(scenario())


def test_revocation_blocks_before_persist_failure(tmp_path):
    root=tmp_path/'selected';root.mkdir()
    files=DesktopFiles({'roots':{}},lambda s:None)
    id=files.authorize(str(root))
    def fail(state):raise OSError('disk full')
    files.save=fail
    with pytest.raises(OSError):files.revoke(id)
    with pytest.raises(ValueError):files.call('list_directory',{'root_id':id})
