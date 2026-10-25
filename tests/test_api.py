import importlib
import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('AI_API_CONFIG', '')
    monkeypatch.setattr('dotenv.load_dotenv', lambda *args, **kwargs: False)
    monkeypatch.setenv('AI_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('STORE_SESSIONS','false')
    monkeypatch.delenv('APP_TOKEN', raising=False)
    import backend.app as module
    importlib.reload(module)
    for key in ('APP_TOKEN', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY', 'MIMO_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('APP_TOKEN', 'test-access-token')
    with TestClient(module.app, headers={'Authorization':'Bearer test-access-token'}) as c:
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


def test_jenkins_config_secrets_discovery_and_workflow(client, monkeypatch):
    from contextlib import asynccontextmanager
    import httpx
    from backend.app import store
    from backend.jenkins_tools import JenkinsSession
    def handler(request):
        if request.url.path.endswith('whoAmI/api/json'):
            return httpx.Response(200, json={'authenticated': True})
        return httpx.Response(200, json={'mode': 'NORMAL'})
    @asynccontextmanager
    async def connection(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
            yield session
    monkeypatch.setattr(JenkinsSession, 'client', connection)
    body = {'name': 'Jenkins', 'service_type': 'jenkins', 'url': 'ci.example:8080/jenkins',
        'username': 'operator', 'password': 'private-ci-password', 'enabled': True}
    response = client.put('/api/mcp/ci', json=body)
    assert response.status_code == 200
    assert response.json()['url'] == 'http://ci.example:8080/jenkins'
    assert 'private-ci-password' not in response.text
    public = next(item for item in client.get('/api/mcp').json() if item['id'] == 'ci')
    assert public['has_password'] and 'password' not in public
    assert client.put('/api/mcp/ci', json=public).status_code == 200
    assert store.get('mcp', 'ci')['password'] == 'private-ci-password'
    discovery = client.post('/api/mcp/ci/test')
    assert discovery.status_code == 200
    assert 'trigger_build' in [tool['name'] for tool in discovery.json()['tools']]
    workflow = {'name': 'CI status', 'steps': [{'id': 'status', 'type': 'tool',
        'server_id': 'ci', 'tool_name': 'server_info', 'arguments': {}}]}
    assert client.put('/api/workflows/ci-status', json=workflow).status_code == 200
    result = client.post('/api/workflows/ci-status/run', json={}).json()
    assert result['status'] == 'completed' and 'NORMAL' in result['output']
    assert 'private-ci-password' not in json.dumps(result)
    for url in ('ftp://ci.example', 'http://operator:private-ci-password@ci.example', 'ci.example:99999'):
        assert client.put('/api/mcp/ci-invalid', json={**body, 'url': url}).status_code == 422
    public['clear_password'] = True
    assert client.put('/api/mcp/ci', json=public).json()['has_password'] is False


@pytest.mark.parametrize('engine,url', [('mysql','mysql://db.example:3306/app'),
    ('postgres','postgresql://db.example:5432/app?sslmode=require&schema=reports')])
def test_direct_database_config_and_tools(client, monkeypatch, engine, url):
    import asyncio
    from backend.app import store
    from backend.mcp_client import ToolHub
    from database_mcp.database import Database
    calls = []
    def query(db, sql, max_rows=100):
        calls.append((db.config, sql))
        return {'columns': ['value'], 'rows': [[1]], 'row_count': 1, 'truncated': False}
    monkeypatch.setattr(Database, 'query', query)
    body = {'name': engine, 'service_type': 'database', 'database_engine': engine,
        'url': url, 'username': 'reader', 'password': 'private-db-password', 'enabled': True}
    response = client.put('/api/mcp/direct-db', json=body)
    assert response.status_code == 200
    assert 'private-db-password' not in response.text
    assert response.json()['has_password'] is True
    public = next(m for m in client.get('/api/mcp').json() if m['id'] == 'direct-db')
    assert 'password' not in public
    assert client.put('/api/mcp/direct-db', json=public).status_code == 200
    assert store.get('mcp', 'direct-db')['password'] == 'private-db-password'
    tested = client.post('/api/mcp/direct-db/test')
    assert tested.status_code == 200
    assert len(tested.json()['tools']) == 3
    async def use_tool():
        async with ToolHub(store, ['direct-db']) as hub:
            alias = next(a for a, route in hub.routes.items() if route[1] == 'query')
            assert 'private-db-password' not in json.dumps(hub.tools)
            assert 'value' in await hub.call(alias, {'sql': 'SELECT 1'})
            with pytest.raises(ValueError):
                await hub.call('unapproved', {})
    asyncio.run(use_tool())
    assert calls[-1][0].engine == engine
    assert calls[-1][0].password == 'private-db-password'
    workflow = {'name': '数据库查询', 'steps': [{'id': 'query', 'type': 'tool',
        'server_id': 'direct-db', 'tool_name': 'query', 'arguments': {'sql': 'SELECT 1'}}]}
    assert client.put('/api/workflows/db-query', json=workflow).status_code == 200
    run = client.post('/api/workflows/db-query/run', json={}).json()
    assert run['status'] == 'completed'
    assert 'value' in run['output']
    assert 'private-db-password' not in json.dumps(run)
    public['clear_password'] = True
    assert client.put('/api/mcp/direct-db', json=public).json()['has_password'] is False


def test_database_connection_errors_hide_credentials(client, monkeypatch):
    from database_mcp.database import Database
    def fail(*args, **kwargs):
        raise RuntimeError('private-db-password')
    monkeypatch.setattr(Database, 'query', fail)
    body = {'name': 'db', 'service_type': 'database', 'url': 'mysql://db/app',
        'username': 'reader', 'password': 'private-db-password'}
    assert client.put('/api/mcp/db-error', json=body).status_code == 200
    response = client.post('/api/mcp/db-error/test')
    assert response.status_code == 400
    assert 'private-db-password' not in response.text
    assert '数据库连接失败' in response.text
    for url in ('https://db/app', 'mysql://db', 'mysql://reader:secret@db/app', 'mysql://db:99999/app'):
        assert client.put('/api/mcp/invalid-db', json={**body, 'url': url}).status_code == 422


def test_ssh_config_password_and_workflow_tool_routing(client, monkeypatch):
    from backend.app import store
    from backend.ssh_tools import SSHSession
    calls = []
    def command(session, command, cwd=None, timeout=60):
        calls.append((session.cfg, command, cwd))
        return {'stdout': 'Linux test-host', 'stderr': '', 'exit_code': 0}
    monkeypatch.setattr(SSHSession, 'exec', command)
    monkeypatch.setattr(SSHSession, 'check_connection', lambda session: None)
    body = {'name': 'SSH 测试服务器', 'service_type': 'ssh', 'host': '127.0.0.1',
        'port': 2222, 'username': 'operator', 'password': 'private-ssh-secret', 'enabled': True}
    response = client.put('/api/mcp/ssh-test', json=body)
    assert response.status_code == 200
    saved = response.json()
    assert saved['allowed_tools'] == ['exec', 'list_directory', 'read_file', 'write_file']
    assert saved['has_password'] and 'password' not in saved
    assert client.put('/api/mcp/ssh-test', json=saved).status_code == 200
    assert store.get('mcp', 'ssh-test')['password'] == 'private-ssh-secret'
    assert 'private-ssh-secret' not in client.get('/api/mcp').text
    assert len(client.post('/api/mcp/ssh-test/test').json()['tools']) == 4
    workflow = {'name': 'SSH 操作', 'steps': [{'id': 'inspect', 'type': 'tool',
        'server_id': 'ssh-test', 'tool_name': 'exec', 'arguments': {'command': 'uname -a'}}]}
    assert client.put('/api/workflows/ssh-operation', json=workflow).status_code == 200
    run = client.post('/api/workflows/ssh-operation/run', json={}).json()
    assert run['status'] == 'completed'
    assert 'Linux test-host' in run['output']
    assert calls[-1][0]['password'] == 'private-ssh-secret'
    assert 'private-ssh-secret' not in json.dumps(run)
    async def stream(cfg, messages, tools):
        assert 'private-ssh-secret' not in json.dumps(tools)
        if messages[-1]['role'] == 'tool':
            assert 'Linux test-host' in messages[-1]['content']
            yield 'message', {'role': 'assistant', 'content': 'SSH 操作已完成'}
        else:
            name = next(t['function']['name'] for t in tools if '] exec:' in t['function']['description'])
            yield 'message', {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'ssh-call', 'type': 'function', 'function': {
                    'name': name, 'arguments': json.dumps({'command': 'uname -a'})}}]}
    monkeypatch.setattr('backend.app.model_config', lambda *args: {})
    monkeypatch.setattr('backend.app.completion_stream', stream)
    chat = client.post('/api/chat', json={'model_id': 'test', 'message': '查看服务器系统', 'mcp_ids': ['ssh-test']})
    assert 'event: tool_end' in chat.text
    assert 'SSH 操作已完成' in chat.text
    assert 'private-ssh-secret' not in chat.text
    saved['allowed_tools'] = ['read_file']
    assert client.put('/api/mcp/ssh-test', json=saved).status_code == 200
    assert client.post('/api/workflows/ssh-operation/run', json={}).json()['status'] == 'failed'
    saved['clear_password'] = True
    assert client.put('/api/mcp/ssh-test', json=saved).json()['has_password'] is False


def test_ssh_validation_and_connection_error(client, monkeypatch):
    from backend.ssh_tools import SSHSession
    body = {'name': 'SSH', 'service_type': 'ssh', 'host': '192.168.1.10',
        'username': 'operator', 'password': 'ssh-secret'}
    for extra in ({'host': ''}, {'host': 'http://host'}, {'host': 'user@host'},
                  {'port': 0}, {'port': 65536}, {'username': ' '}):
        response = client.put('/api/mcp/invalid-ssh', json={**body, **extra})
        assert response.status_code == 422
        assert 'ssh-secret' not in response.text
    assert client.put('/api/mcp/ssh-fail', json=body).status_code == 200
    def fail(session): raise RuntimeError('ssh-secret')
    monkeypatch.setattr(SSHSession, 'check_connection', fail)
    response = client.post('/api/mcp/ssh-fail/test')
    assert response.status_code == 400
    assert 'SSH 连接失败' in response.text
    assert 'ssh-secret' not in response.text
    from backend.tool_errors import ToolExecutionError
    def host_key_failure(session):
        raise ToolExecutionError('ssh_host_key_mismatch', 'SSH 主机公钥与缓存不一致，请核对 known_hosts。')
    monkeypatch.setattr(SSHSession, 'check_connection', host_key_failure)
    response = client.post('/api/mcp/ssh-fail/test')
    assert response.status_code == 400
    assert response.json()['detail'] == 'SSH 主机公钥与缓存不一致，请核对 known_hosts。'


def test_workflow_rejects_unknown_target_and_cycles(client):
    for steps in [[{'id': 'a', 'type': 'prompt', 'next': 'missing'}],
                  [{'id': 'a', 'type': 'prompt', 'next': 'a'}]]:
        response = client.put('/api/workflows/bad', json={'name': 'bad', 'steps': steps})
        assert response.status_code == 422


def test_missing_key_is_saved_as_failure(client):
    client.put('/api/settings/session-storage',json={'enabled':True})
    response = client.post('/api/chat', json={'model_id': 'deepseek', 'message': '你好','store_session':True})
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


def test_workflow_graph_layout_entry_and_snapshot(client):
    workflow = {'name':'图形流程','entry_id':'branch','steps':[
        {'id':'unused','type':'template','prompt':'不应执行'},
        {'id':'branch','label':'条件判断','position':{'x':120,'y':80},'type':'condition','contains':'yes','next':'good','otherwise':'bad'},
        {'id':'good','type':'template','prompt':'通过 {{input}}'},
        {'id':'bad','type':'template','prompt':'拒绝 {{input}}'}]}
    saved = client.put('/api/workflows/graph',json=workflow)
    assert saved.status_code == 200
    assert saved.json()['steps'][1]['position'] == {'x':120,'y':80}
    result = client.post('/api/workflows/graph/run',json={'input':'yes'}).json()
    assert result['output'] == '通过 yes'
    assert [s['step'] for s in result['trace']] == ['branch','good']
    assert result['trace'][0]['next_step'] == 'good'
    assert result['workflow_snapshot']['entry_id'] == 'branch'
    assert result['current_step'] is None
    workflow['entry_id'] = 'missing'
    assert client.put('/api/workflows/graph',json=workflow).status_code == 422


def test_workflow_stream_includes_running_nodes_and_final_graph(client):
    client.put('/api/workflows/stream',json={'name':'流式流程','steps':[{'id':'one','type':'template','prompt':'完成 {{input}}','next':'two'},{'id':'two','type':'template','prompt':'{{previous}}!'}]})
    response = client.post('/api/workflows/stream/stream',json={'input':'测试'})
    assert response.status_code == 200
    snapshots = [json.loads(block.split('data: ',1)[1]) for block in response.text.split('\n\n') if block.startswith('event: snapshot')]
    assert any(any(t['status']=='running' for t in r['trace']) for r in snapshots)
    assert snapshots[-1]['status'] == 'completed'
    assert snapshots[-1]['output'] == '完成 测试!'
    assert 'event: done' in response.text


def test_saved_execution_graph_does_not_change_after_edit(client):
    config={'name':'旧版图','steps':[{'id':'original','type':'template','prompt':'旧结果'}]}
    client.put('/api/workflows/history-graph',json=config)
    run=client.post('/api/workflows/history-graph/run',json={'input':''}).json()
    client.put('/api/workflows/history-graph',json={'name':'新版图','steps':[{'id':'new','type':'template','prompt':'新结果'}]})
    history=next(r for r in client.get('/api/runs').json() if r['id']==run['id'])
    assert history['workflow_snapshot']['steps'][0]['id']=='original'
    assert history['output']=='旧结果'


def test_cancelled_workflow_marks_active_node_cancelled(tmp_path,monkeypatch):
    import asyncio
    import backend.runtime as runtime
    from backend.store import Store
    from backend.schemas import WorkflowConfig,WorkflowRun
    store=Store(tmp_path)
    async def check():
        started=asyncio.Event()
        async def slow_agent(*args):
            started.set()
            await asyncio.sleep(60)
        monkeypatch.setattr(runtime,'run_agent',slow_agent)
        workflow=WorkflowConfig(name='取消图',steps=[{'id':'waiting','type':'prompt'}]).model_dump()
        task=asyncio.create_task(runtime.execute_workflow(store,workflow,WorkflowRun()))
        await asyncio.wait_for(started.wait(),timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(check())
    run=store.all('runs')[0]
    assert run['status']=='cancelled'
    assert run['current_step'] is None
    assert run['trace'][0]['status']=='cancelled'


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
    assert client.get('/api/models', headers={'Authorization':''}).status_code == 401
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
    client.put('/api/settings/session-storage',json={'enabled':True})
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
        response = client.post('/api/chat',json={'model_id':'test-model','message':'计算 23 + 19','skill_ids':['concise'],'mcp_ids':['remote-test'],'store_session':True})
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


def test_access_requires_server_and_client_token(client, monkeypatch):
    for path in ['/api/models', '/api/mcp', '/api/settings/session-storage']:
        assert client.get(path, headers={'Authorization':''}).status_code == 401
        assert client.get(path, headers={'Authorization':'Bearer wrong'}).status_code == 401
    assert client.post('/api/chat', headers={'Authorization':''}, json={}).status_code == 401
    monkeypatch.delenv('APP_TOKEN')
    assert client.get('/api/models').status_code == 503
    assert client.get('/api/health', headers={'Authorization':''}).status_code == 200
