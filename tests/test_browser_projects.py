from test_api import client


def test_browser_projects_pair_without_download_or_manual_device_id(client):
    session=client.post('/api/projects/browser-session',json={}).json()
    assert session['device_id'] and session['token']
    repeated=client.post('/api/projects/browser-session',json=session)
    assert repeated.status_code==200
    project=client.post('/api/projects/browser',json={'name':'my-project','device_id':session['device_id']})
    assert project.status_code==200,project.text
    assert project.json()['source']=='browser'
    assert project.json()['path']=='my-project'
    assert client.post('/api/projects/browser-session',json={**session,'token':'wrong'}).status_code==401
    client.delete('/api/projects/'+project.json()['id'])
    client.delete('/api/projects/assistants/'+session['device_id'])
