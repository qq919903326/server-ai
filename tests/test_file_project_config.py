from test_api import client


def test_project_path_is_required_and_preserved_for_file_mcp(client):
    config={'name':'project','url':'https://files.example.com/mcp','service_type':'file_p2p','device_id':'my-pc'}
    assert client.put('/api/mcp/project',json=config).status_code==422
    config['project_path']='E:\\Projects\\my-app'
    assert client.put('/api/mcp/project',json=config).status_code==200
    saved=next(item for item in client.get('/api/mcp').json() if item['id']=='project')
    assert saved['project_path']==config['project_path']
    assert saved['device_id']=='my-pc'
    for path in ['relative/project','E:\\Projects\nmalicious','../project']:
        assert client.put('/api/mcp/project',json={**config,'project_path':path}).status_code==422
