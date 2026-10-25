import json
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from test_api import client

@pytest.fixture
def model_provider():
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path,self.headers.get('Authorization')))
            if self.headers.get('Authorization')=='Bearer rejected-secret':
                self.send_response(401);self.end_headers();self.wfile.write(b'{"error":"rejected-secret"}');return
            body=json.dumps({'object':'list','data':[{'id':'model-b','object':'model','created':0,'owned_by':'test'},{'id':'model-a','object':'model','created':0,'owned_by':'test'},{'id':'model-b','object':'model','created':0,'owned_by':'test'}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:yield f'http://127.0.0.1:{server.server_port}/v1',requests
    finally:server.shutdown();server.server_close()

@pytest.mark.parametrize('provider',['deepseek','mimo','openai'])
def test_catalog_queries_key_scoped_models_without_saving_new_key(client,model_provider,provider):
    url,requests=model_provider
    result=client.post('/api/models/new-catalog/catalog',json={'provider':provider,'base_url':url,'api_key':'test-secret'})
    assert result.status_code==200
    assert result.json()['models']==['model-a','model-b']
    assert requests==[('/v1/models','Bearer test-secret')]
    assert 'test-secret' not in result.text
    assert all(m['id']!='new-catalog' for m in client.get('/api/models').json())
    config={'name':'test','provider':provider,'base_url':url,'model':'model-a','api_key':'test-secret'}
    client.put('/api/models/new-catalog',json=config)
    saved=next(m for m in client.get('/api/models').json() if m['id']=='new-catalog')
    assert saved['available_models']==['model-a','model-b']
    from backend.runtime import model_config
    import backend.app as module
    assert model_config(module.store,'new-catalog::model-b')['model']=='model-b'
    # Discovering an unsaved draft must not invalidate the saved connection.
    assert client.post('/api/models/new-catalog/catalog',json={'api_key':'draft-key'}).status_code==200
    assert model_config(module.store,'new-catalog::model-b')['model']=='model-b'
    with pytest.raises(ValueError):model_config(module.store,'new-catalog::not-listed')
    config['api_key']='different-key';client.put('/api/models/new-catalog',json=config)
    saved=next(m for m in client.get('/api/models').json() if m['id']=='new-catalog')
    assert saved['available_models']==[]
    with pytest.raises(ValueError):model_config(module.store,'new-catalog::model-b')

def test_catalog_uses_saved_key_and_does_not_leak_provider_errors(client,model_provider):
    url,requests=model_provider
    client.put('/api/models/test-catalog',json={'name':'test','provider':'custom','base_url':url,'model':'test','api_key':'test-secret'})
    assert client.post('/api/models/test-catalog/catalog',json={}).status_code==200
    error=client.post('/api/models/test-catalog/catalog',json={'api_key':'rejected-secret'})
    assert error.status_code==400
    assert 'rejected-secret' not in error.text
    assert client.post('/api/models/not-configured/catalog',json={'provider':'mimo','base_url':url}).status_code==400

def test_catalog_never_reuses_saved_key_for_changed_endpoint(client,model_provider):
    url,requests=model_provider
    client.put('/api/models/secret-boundary',json={'name':'test','provider':'custom','base_url':url,'model':'test','api_key':'test-secret'})
    response=client.post('/api/models/secret-boundary/catalog',json={'base_url':url+'/other'})
    assert response.status_code==400
    assert requests==[]
