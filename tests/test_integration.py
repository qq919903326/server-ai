import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from backend.store import Store
from backend.mcp_client import ToolHub, discover


def test_remote_mcp_discovery_allowlist_and_call(tmp_path, remote_mcp):
    store = Store(tmp_path)
    store.put('mcp', 'demo', {'name':'demo','transport':'http','url':remote_mcp['url'],'enabled':True,'allowed_tools':['add']})
    async def run():
        tools = await discover(store, 'demo')
        assert {t['name'] for t in tools} == {'add', 'other'}
        async with ToolHub(store, ['demo']) as hub:
            assert len(hub.tools) == 1
            alias = hub.tools[0]['function']['name']
            result = json.loads(await hub.call(alias, {'a': 2, 'b': 3}))
            assert result['isError'] is False
            assert '5' in json.dumps(result)
            with pytest.raises(ValueError, match='未授权'):
                await hub.call('other', {})
    asyncio.run(run())


@pytest.mark.parametrize('provider', ['custom', 'mimo'])
@pytest.mark.parametrize('streaming', [True, False])
def test_model_stream_keeps_chunks_and_tool_arguments(provider, streaming):
    from backend.agent import completion_stream, completion
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(data)
            self.send_response(200)
            if not data.get('stream'):
                self.send_header('Content-Type','application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'id':'test','object':'chat.completion','created':1,'model':'test','choices':[{'index':0,'message':{'role':'assistant','content':'你好'},'finish_reason':'stop'}]}).encode())
                return
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for delta in [{'content':'你'}, {'content':'好'}]:
                value = {'id':'test','object':'chat.completion.chunk','created':1,'model':'test', 'choices':[{'index':0,'delta':delta,'finish_reason':None}]}
                self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    async def run():
        config = {'api_key':'test-only', 'provider':provider, 'model':'test', 'base_url':f'http://127.0.0.1:{server.server_port}/v1'}
        if not streaming:
            return await completion(config, [{'role':'user','content':'你好'}], [])
        return [item async for item in completion_stream(config, [{'role':'user','content':'你好'}], [])]
    try:
        events = asyncio.run(run())
        if streaming:
            assert [data['text'] for event, data in events if event == 'delta'] == ['你','好']
            assert events[-1] == ('message', {'role':'assistant','content':'你好'})
            assert requests[0]['stream'] is True
        else:
            assert events['content'] == '你好'
        if provider == 'mimo':
            assert requests[0]['thinking'] == {'type':'disabled'}
        else:
            assert 'thinking' not in requests[0]
    finally:
        server.shutdown()
        server.server_close()


def test_mcp_error_result_is_failure():
    from mcp.types import CallToolResult, TextContent
    class Session:
        async def call_tool(self, name, args):
            return CallToolResult(isError=True, content=[TextContent(type='text', text='private tool error')])
    hub = ToolHub(None, [])
    hub.routes['broken'] = (Session(), 'broken', {'type':'object'}, 'demo')
    with pytest.raises(ValueError, match='工具执行失败'):
        asyncio.run(hub.call('broken', {}))


def test_workflow_retries_tool_failure_and_stops_when_exhausted(tmp_path, monkeypatch):
    import backend.runtime as runtime
    from backend.schemas import WorkflowConfig, WorkflowRun
    store = Store(tmp_path)
    attempts = []
    class Hub:
        def __init__(self, *args):
            self.routes = {'alias': (None, 'tool', {}, 'server')}
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def call(self, alias, args):
            attempts.append(1)
            if len(attempts) <= 1:
                raise ValueError('工具执行失败')
            return 'recovered'
    monkeypatch.setattr(runtime, 'ToolHub', Hub)
    config = WorkflowConfig(name='retry', steps=[{'id':'tool', 'type':'tool', 'tool_name':'tool', 'server_id':'server', 'retries':1, 'next':'final'}, {'id':'final','type':'template','prompt':'done {{previous}}'}]).model_dump()
    result = asyncio.run(runtime.execute_workflow(store, config, WorkflowRun()))
    assert result['status'] == 'completed'
    assert result['output'] == 'done recovered'
    assert result['trace'][0]['attempts'] == 2
    attempts.clear()
    config['steps'][0]['retries'] = 0
    result = asyncio.run(runtime.execute_workflow(store, config, WorkflowRun()))
    assert result['status'] == 'failed'
    assert [entry['step'] for entry in result['trace']] == ['tool']
