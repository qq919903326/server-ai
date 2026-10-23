import importlib
import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_DATA_DIR', str(tmp_path))
    monkeypatch.delenv('APP_TOKEN', raising=False)
    import backend.app as module
    importlib.reload(module)
    for key in ('APP_TOKEN', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'MIMO_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(module.app) as c:
        yield c


def test_config_masks_and_preserves_secret(client):
    models = client.get('/api/models').json()
    model = models[0]
    model['api_key'] = 'secret-for-test'
    assert client.put('/api/models/' + model['id'], json=model).status_code == 200
    data = client.get('/api/models').json()
    assert 'secret-for-test' not in json.dumps(data)
    assert data[0]['has_key'] is True
    assert client.put('/api/models/' + model['id'], json=data[0]).status_code == 200
    assert client.get('/api/models').json()[0]['has_key'] is True


def test_skill_rejects_traversal(client):
    assert client.put('/api/skills/invalid.name', json={'content': 'x'}).status_code == 422
    assert client.put('/api/skills/report', json={'content': '# 报告\n保持简洁', 'enabled': True}).status_code == 200
    assert any(s['id'] == 'report' for s in client.get('/api/skills').json())


def test_workflow_rejects_unknown_target_and_cycles(client):
    for steps in [[{'id': 'a', 'type': 'prompt', 'next': 'missing'}],
                  [{'id': 'a', 'type': 'prompt', 'next': 'a'}]]:
        response = client.put('/api/workflows/bad', json={'name': 'bad', 'steps': steps})
        assert response.status_code == 422


def test_missing_key_is_saved_as_failure(client):
    response = client.post('/api/chat', json={'model_id': 'deepseek', 'message': '你好'})
    assert response.status_code == 200
    assert 'event: error' in response.text
    runs = client.get('/api/runs').json()
    assert runs[0]['status'] == 'failed'
    sessions = client.get('/api/sessions').json()
    assert sessions[0]['title'] == '你好'
    assert client.get('/api/sessions/' + sessions[0]['id']).json()['messages'][0]['content'] == '你好'


def test_conditional_workflow_executes_selected_path(client):
    workflow = {'name': '分支', 'steps': [
        {'id': 'check', 'type': 'condition', 'contains': 'yes', 'next': 'good', 'otherwise': 'bad'},
        {'id': 'good', 'type': 'template', 'prompt': '通过：{{input}}'},
        {'id': 'bad', 'type': 'template', 'prompt': '拒绝：{{input}}'}]}
    assert client.put('/api/workflows/branch', json=workflow).status_code == 200
    result = client.post('/api/workflows/branch/run', json={'input': 'yes'}).json()
    assert result['status'] == 'completed'
    assert result['output'] == '通过：yes'
    assert [s['step'] for s in result['trace']] == ['check', 'good']


def test_agent_calls_tool_and_obeys_round_limit():
    import asyncio
    from backend.agent import agent_events
    calls = []
    async def model(messages, tools):
        if messages[-1]['role'] == 'tool':
            return {'role': 'assistant', 'content': '完成'}
        return {'role': 'assistant', 'content': None, 'tool_calls': [
            {'id': '1', 'type': 'function', 'function': {'name': 'clock', 'arguments': '{}'}}]}
    async def tool(name, args):
        calls.append(name)
        return '12:00'
    async def collect(limit):
        return [e async for e in agent_events([{'role': 'user', 'content': '时间'}], [], model, tool, limit)]
    events = asyncio.run(collect(3))
    assert events[-1] == ('answer', {'text': '完成'})
    assert calls == ['clock']
    with pytest.raises(ValueError, match='轮数'):
        asyncio.run(collect(1))


def test_deleted_default_model_does_not_return_after_restart(client):
    import backend.app as module
    assert client.delete('/api/models/deepseek').status_code == 200
    module.seed()
    assert all(m['id'] != 'deepseek' for m in client.get('/api/models').json())


def test_mimo_preset_and_one_time_upgrade(client):
    import backend.app as module
    mimo = next(m for m in client.get('/api/models').json() if m['id'] == 'mimo')
    assert mimo['provider'] == 'mimo'
    assert mimo['base_url'] == 'https://api.xiaomimimo.com/v1'
    assert mimo['model'] == 'mimo-v2.6-flash'
    assert mimo['key_env'] == 'MIMO_API_KEY'
    mimo['model'] = 'my-custom-mimo'
    assert client.put('/api/models/mimo', json=mimo).status_code == 200
    module.seed()
    assert module.store.get('models','mimo')['model'] == 'my-custom-mimo'
    client.delete('/api/models/mimo')
    module.seed()
    assert module.store.get('models','mimo') is None
    module.store.delete('meta','mimo-preset-v1')
    module.seed()
    assert module.store.get('models','mimo')['provider'] == 'mimo'


def test_auth_protects_api_and_rejects_foreign_origin(client, monkeypatch):
    monkeypatch.setenv('APP_TOKEN', 'test-access-token')
    assert client.get('/api/models').status_code == 401
    headers = {'Authorization': 'Bearer test-access-token'}
    assert client.get('/api/models', headers=headers).status_code == 200
    assert client.put('/api/skills/x', headers={**headers, 'Origin': 'https://untrusted.example'}, json={'content': 'x'}).status_code == 403


def test_invalid_config_does_not_echo_secret(client):
    secret = 'sensitive-key-' * 400
    result = client.put('/api/models/invalid', json={'name':'test','provider':'openai','model':'test','base_url':'https://api.openai.com/v1','api_key':secret})
    assert result.status_code == 422
    assert secret not in result.text


def test_mcp_accepts_only_remote_http_configuration(client):
    assert client.get('/api/mcp').json() == []
    result = client.put('/api/mcp/local', json={'name':'local','transport':'stdio','command':'python','args':[]})
    assert result.status_code == 422
    assert client.put('/api/mcp/remote', json={'name':'remote','transport':'http','url':'https://example.com/mcp'}).status_code == 200


def test_chat_stream_with_real_provider_http_and_mcp(client, remote_mcp):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    requests = []
    class Provider(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            if body['messages'][-1]['role'] == 'tool':
                deltas = [{'content':'计算结果：'}, {'content':'42'}]
            else:
                name = body['tools'][0]['function']['name']
                deltas = [{'tool_calls':[{'index':0,'id':'call_demo','type':'function','function':{'name':name,'arguments':'{"a":23,'}}]},
                          {'tool_calls':[{'index':0,'function':{'arguments':'"b":19}'}}]}]
            self.send_response(200)
            self.send_header('Content-Type','text/event-stream')
            self.end_headers()
            for delta in deltas:
                chunk = {'id':'test','object':'chat.completion.chunk','created':1,'model':'test', 'choices':[{'index':0,'delta':delta,'finish_reason':None}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1',0), Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        cfg = {'name':'test', 'provider':'custom', 'model':'test', 'base_url':f'http://127.0.0.1:{server.server_port}/v1', 'api_key':'test-only'}
        assert client.put('/api/models/test-model',json=cfg).status_code == 200
        mcp = {'name':'remote-test','transport':'http','url':remote_mcp['url'],'enabled':True,'allowed_tools':['add']}
        client.put('/api/mcp/remote-test',json=mcp)
        response = client.post('/api/chat',json={'model_id':'test-model','message':'计算 23 + 19','skill_ids':['concise'],'mcp_ids':['remote-test']})
        assert 'event: tool_start' in response.text
        assert 'event: delta' in response.text
        assert 'event: answer' in response.text
        run = client.get('/api/runs').json()[0]
        assert run['status'] == 'completed'
        assert run['output'] == '计算结果：42'
        assert '简明助手' in requests[0]['messages'][0]['content']
        assert requests[1]['messages'][-1]['role'] == 'tool'
        session = client.get('/api/sessions/' + run['session_id']).json()
        assert session['messages'][-1]['content'] == '计算结果：42'
    finally:
        server.shutdown()
        server.server_close()
