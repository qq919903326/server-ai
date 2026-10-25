import asyncio
import hashlib
import pytest
from test_api import client


def legacy_pair(service):
    """Existing pre-EXE installations retain the path-based protocol."""
    token='legacy-device-token-for-tests-only-123456789'
    id='legacy-test'
    service.store.put('devices',id,{'token_hash':hashlib.sha256(token.encode()).hexdigest()})
    service.hub.config.devices[id]='paired'
    service.hub.locks[id]=asyncio.Lock()
    return {'device_id':id,'token':token}


def test_legacy_assistant_and_path_only_project_crud(client):
    assert client.post('/api/projects',json={'path':'E:\\Code\\app'}).status_code==409
    import backend.app as module
    config=legacy_pair(module.projects_service)
    auth={'Authorization':'Bearer '+config['token']}
    assert client.get('/agent/projects').status_code==401
    assert client.get('/agent/projects',headers=auth).status_code==200
    result=client.post('/api/projects',json={'path':'E:\\Code\\app','device_id':config['device_id']})
    assert result.status_code==200,result.text
    project=result.json()
    assert project['name']=='app'
    assert client.get('/agent/projects',headers=auth).json()['projects'][0]['path']=='E:\\Code\\app'
    assert 'token' not in client.get('/api/projects').text
    assert client.delete('/api/projects/'+project['id']).status_code==200
    assert client.get('/agent/projects',headers=auth).json()['projects']==[]
    # Remote consumers cannot bypass project management through workflow/MCP APIs.
    for url in ['/api/mcp','/api/workflows','/api/projects']:
        assert client.get(url,headers={'Host':'remote.example.com','Authorization':''}).status_code==401


def test_project_agent_sync_and_read_removal(tmp_path):
    from file_mcp.project_agent import ProjectFiles
    root=tmp_path/'project';root.mkdir();(root/'hello.txt').write_text('project data')
    files=ProjectFiles(tmp_path/'roots.json')
    assert files.sync([{'id':'p1','path':str(root)}])[0]['ready']
    assert files.call('read_text',{'root_id':'p1','path':'hello.txt'})['text']=='project data'
    files.sync([])
    import pytest
    with pytest.raises(ValueError):files.call('read_text',{'root_id':'p1','path':'hello.txt'})
    files.sync([{'id':'p1','path':str(root)}])
    root.rename(tmp_path/'old');root.mkdir()
    restarted=ProjectFiles(tmp_path/'roots.json')
    assert not restarted.sync([{'id':'p1','path':str(root)}])[0]['ready']


def test_project_tools_over_real_paired_connection(tmp_path):
    asyncio.run(_project_roundtrip(tmp_path))


async def _project_roundtrip(tmp_path):
    import httpx
    from fastapi import FastAPI
    from backend.projects import ProjectService
    from backend.store import Store
    from backend.mcp_client import ToolHub
    from file_mcp.project_agent import ProjectFiles
    from file_mcp.agent import connect
    root=tmp_path/'source';root.mkdir();(root/'readme.txt').write_text('local project through P2P')
    store=Store(tmp_path/'db');service=ProjectService(store);store.projects_service=service
    app=FastAPI();app.include_router(service.router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
        config=legacy_pair(service)
        client.headers['Authorization']='Bearer '+config['token']
        project=(await client.post('/api/projects',json={'path':str(root)})).json()
        files=ProjectFiles(tmp_path/'cache.json')
        assigned=(await client.get('/agent/projects')).json()['projects'];files.sync(assigned)
        peer=await connect(client,config['device_id'],config['token'],files,[],'/agent/signal/')
        try:
            async with ToolHub(store,['project-'+project['id']]) as tools:
                read=next(t for t in tools.tools if 'read_text' in t['function']['description'])
                assert 'root_id' not in read['function']['parameters']['properties']
                result=await tools.call(read['function']['name'],{'path':'readme.txt'})
                assert 'local project through P2P' in result
                await client.delete('/api/projects/'+project['id'])
                import pytest
                with pytest.raises(ValueError):await tools.call(read['function']['name'],{'path':'readme.txt'})
        finally:
            await peer.close();await service.hub.close()


def test_revocation_during_status_upload_cannot_restore_device(tmp_path):
    asyncio.run(_revocation(tmp_path))


async def _revocation(tmp_path):
    import httpx
    from fastapi import FastAPI
    from backend.projects import ProjectService
    from backend.store import Store
    service=ProjectService(Store(tmp_path/'db'));app=FastAPI();app.include_router(service.router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
        config=legacy_pair(service)
        started,release=asyncio.Event(),asyncio.Event()
        async def body():
            yield b'{"projects":'
            started.set();await release.wait()
            yield b'[]}'
        upload=asyncio.create_task(client.post('/agent/status',content=body(),headers={'Authorization':'Bearer '+config['token']}))
        await started.wait()
        await client.delete('/api/projects/assistants/'+config['device_id'])
        release.set()
        assert (await upload).status_code==401
        assert service.store.all('devices')==[]
        await service.hub.close()


def test_revocation_during_signal_closes_late_connection(tmp_path):
    asyncio.run(_signal_revocation(tmp_path))


async def _signal_revocation(tmp_path):
    import httpx
    from fastapi import FastAPI
    from backend.projects import ProjectService
    from backend.store import Store
    service=ProjectService(Store(tmp_path/'db'));app=FastAPI();app.include_router(service.router())
    started,release=asyncio.Event(),asyncio.Event()
    class Connection:
        closed=False
        async def close(self):self.closed=True
    connection=Connection()
    async def offer(device,sdp):
        started.set();await release.wait()
        service.hub.connections[device]=connection
        return {'type':'answer','sdp':'test'}
    service.hub.offer=offer
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
        config=legacy_pair(service)
        signal=asyncio.create_task(client.post('/agent/signal/'+config['device_id'],json={'type':'offer','sdp':'test'},headers={'Authorization':'Bearer '+config['token']}))
        await started.wait()
        await client.delete('/api/projects/assistants/'+config['device_id'])
        release.set()
        assert (await signal).status_code==401
        assert connection.closed and not service.hub.connections
        await service.hub.close()
