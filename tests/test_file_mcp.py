import asyncio
import json
import pytest


def test_only_authorized_roots_and_bounded_reads(tmp_path):
    from file_mcp.files import FileAccess
    root=tmp_path/'allowed';root.mkdir()
    (root/'note.txt').write_text('hello world',encoding='utf-8')
    (tmp_path/'secret.txt').write_text('secret')
    files=FileAccess({'docs':str(root)})
    assert files.call('list_roots',{})=={'roots':[{'id':'docs','access':'read-only'}]}
    assert files.call('list_directory',{'root_id':'docs'})['entries'][0]['name']=='note.txt'
    assert files.call('file_info',{'root_id':'docs','path':'note.txt'})['size']==11
    result=files.call('read_text',{'root_id':'docs','path':'note.txt','max_bytes':5})
    assert result['text']=='hello' and result['next_offset']==5 and result['truncated']
    assert files.call('read_text',{'root_id':'docs','path':'note.txt','offset':5})['text']==' world'
    for path in ['../secret.txt','/secret.txt','C:\\secret.txt','note.txt:secret','sub/../../secret.txt']:
        with pytest.raises(ValueError):files.call('file_info',{'root_id':'docs','path':path})
    with pytest.raises(ValueError):files.call('write_file',{})
    with pytest.raises(ValueError):files.call('read_text',{'root_id':'other','path':'note.txt'})
    with pytest.raises(ValueError):FileAccess({})
    with pytest.raises(ValueError):files.call('read_text',{'root_id':'docs','path':'note.txt','max_bytes':999999})
    (root/'binary.dat').write_bytes(b'\x00secret')
    with pytest.raises(ValueError):files.call('read_text',{'root_id':'docs','path':'binary.dat'})
    page=files.call('list_directory',{'root_id':'docs','limit':1})
    assert page['truncated'] and page['next_offset']==1


def test_symlinks_cannot_leave_roots(tmp_path):
    from file_mcp.files import FileAccess
    root=tmp_path/'allowed';root.mkdir()
    outside=tmp_path/'outside';outside.mkdir();(outside/'secret').write_text('secret')
    try:(root/'link').symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('symlink creation unavailable')
    with pytest.raises(ValueError):FileAccess({'docs':str(root)}).call('read_text',{'root_id':'docs','path':'link/secret'})


def test_real_webrtc_channel_and_authenticated_mcp(tmp_path):
    asyncio.run(_roundtrip(tmp_path))


async def _roundtrip(tmp_path):
    import httpx
    from file_mcp.files import FileAccess
    from file_mcp.agent import connect
    from file_mcp.server import create_app
    from file_mcp.config import ServerConfig
    (tmp_path/'hello.txt').write_text('p2p file result',encoding='utf-8')
    config=ServerConfig(mcp_token='m'*32,devices={'test-pc':'d'*32},ice_servers=[])
    app=create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            assert (await client.post('/signal/test-pc',json={})).status_code==401
            assert (await client.post('/mcp',json={})).status_code==401
            assert (await client.post('/signal/test-pc',headers={'Authorization':'Bearer '+'m'*32},json={})).status_code==401
            assert (await client.post('/mcp',headers={'Authorization':'Bearer '+'d'*32},json={})).status_code==401
            agent=await connect(client,'test-pc','d'*32,FileAccess({'docs':str(tmp_path)}),[])
            try:
                for _ in range(100):
                    if app.state.hub.online('test-pc'):break
                    await asyncio.sleep(.1)
                assert app.state.hub.online('test-pc')
                results=await asyncio.gather(*(app.state.hub.call('test-pc','read_text',{'root_id':'docs','path':'hello.txt','offset':i,'max_bytes':1}) for i in range(5)))
                assert ''.join(r['text'] for r in results)=='p2p f'
                headers={'Authorization':'Bearer '+'m'*32,'Accept':'application/json, text/event-stream'}
                response=await client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'read_text','arguments':{'device_id':'test-pc','root_id':'docs','path':'hello.txt'}}})
                assert response.status_code==200,response.text
                assert 'p2p file result' in response.text
                with pytest.raises(ValueError):
                    await app.state.hub.call('test-pc','read_text',{'root_id':'docs','path':'../bad'})
                error=await client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'read_text','arguments':{'device_id':'test-pc','root_id':'docs','path':'../bad'}}})
                assert error.json()['result']['isError'] is True
            finally:await agent.close()
            for _ in range(30):
                if not app.state.hub.online('test-pc'):break
                await asyncio.sleep(.1)
            with pytest.raises(ValueError):await app.state.hub.call('test-pc','list_roots',{})
            # A fresh authenticated offer replaces the closed session.
            again=await connect(client,'test-pc','d'*32,FileAccess({'docs':str(tmp_path)}),[])
            try:
                assert (await app.state.hub.call('test-pc','list_roots',{}))['roots'][0]['id']=='docs'
            finally:await again.close()


def test_local_authorization_persists_only_explicit_roots(tmp_path):
    from file_mcp.agent import authorize,validate_settings
    root=tmp_path/'docs';root.mkdir()
    config=tmp_path/'agent.json'
    authorize(config,'https://files.example.com','pc','FILE_DEVICE_TOKEN',[f'docs={root}'])
    data=json.loads(config.read_text(encoding='utf-8'))
    assert data['roots']=={'docs':str(root.resolve())}
    assert 'api_key' not in data and 'token' not in data
    assert validate_settings(data).call('list_roots',{})['roots'][0]['id']=='docs'
    for url in ['http://remote.example.com','https://files.example.com/path','https://user:secret@example.com','file:///etc']:
        with pytest.raises(ValueError):validate_settings({**data,'server':url})
    root.rename(tmp_path/'old_docs');root.mkdir()
    with pytest.raises(ValueError,match='授权根目录'):validate_settings(data)


def test_mcp_and_device_credentials_are_isolated():
    from file_mcp.config import ServerConfig
    for token,devices in [('short',{'pc':'d'*32}),('m'*32,{'pc':'m'*32}),('m'*32,{'pc':'d'*32,'other':'d'*32})]:
        with pytest.raises(ValueError):ServerConfig(token,devices)


@pytest.mark.parametrize('method',['read_text','file_info','list_directory'])
def test_swapped_directory_after_validation_cannot_escape(tmp_path,monkeypatch,method):
    from file_mcp.files import FileAccess
    root=tmp_path/'allowed';root.mkdir();(root/'sub').mkdir();(root/'sub'/'secret.txt').write_text('allowed')
    outside=tmp_path/'outside';outside.mkdir();(outside/'secret.txt').write_text('PRIVATE')
    link=tmp_path/'link'
    try:link.symlink_to(outside,target_is_directory=True)
    except OSError:pytest.skip('symlink creation unavailable')
    link.unlink()
    files=FileAccess({'docs':str(root)})
    original=files.resolve
    def swap(root_id,path=''):
        target=original(root_id,path)
        (root/'sub').rename(root/'original')
        (root/'sub').symlink_to(outside,target_is_directory=True)
        return target
    monkeypatch.setattr(files,'resolve',swap)
    with pytest.raises((ValueError,OSError)):
        files.call(method,{'root_id':'docs','path':'sub' if method=='list_directory' else 'sub/secret.txt'})


def test_replacing_authorized_root_requires_reauthorization(tmp_path):
    from file_mcp.files import FileAccess
    root=tmp_path/'allowed';root.mkdir();(root/'note.txt').write_text('allowed')
    outside=tmp_path/'outside';outside.mkdir();(outside/'note.txt').write_text('PRIVATE')
    files=FileAccess({'docs':str(root)})
    root.rename(tmp_path/'old_root');outside.rename(root)
    with pytest.raises(ValueError,match='授权根目录'):
        files.call('read_text',{'root_id':'docs','path':'note.txt'})
