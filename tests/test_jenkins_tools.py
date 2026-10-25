import asyncio
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from jsonschema import ValidationError

from backend.jenkins_tools import JenkinsSession, job_path, normalize_jenkins_url
from backend.mcp_client import ToolHub, discover
from backend.schemas import MCPConfig
from backend.store import Store


@pytest.fixture
def jenkins():
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, content=b'', headers=None):
            self.send_response(status)
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            path = urlsplit(self.path).path
            calls.append(('GET', self.path))
            if self.headers.get('Authorization') != 'Basic ' + base64.b64encode(b'operator:private-password').decode():
                return self.respond(401, b'private-password')
            data = {'jobs': [{'name': 'demo'}, {'name': 'next'}]}
            if path == '/jenkins/whoAmI/api/json':
                data = {'authenticated': True, 'name': 'operator'}
            elif path == '/jenkins/api/json':
                if 'jobs[' not in parse_qs(urlsplit(self.path).query).get('tree', [''])[0]:
                    data = {'mode': 'NORMAL'}
            elif path == '/jenkins/crumbIssuer/api/json':
                return self.respond(200, json.dumps({'crumbRequestField': 'Jenkins-Crumb', 'crumb': 'csrf'}).encode(),
                    {'Set-Cookie': 'JSESSIONID=session; Path=/jenkins'})
            elif path.endswith('/logText/progressiveText'):
                start = int(parse_qs(urlsplit(self.path).query)['start'][0])
                return self.respond(200, b'hello world'[start:], {'X-More-Data': 'true'})
            elif path.endswith('/config.xml'):
                return self.respond(200, b'<project/>')
            elif path == '/jenkins/queue/item/17/api/json':
                data = {'id': 17, 'executable': {'number': 3}}
            return self.respond(200, json.dumps(data).encode())

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            calls.append(('POST', self.path, body))
            if self.headers.get('Jenkins-Crumb') != 'csrf' or 'JSESSIONID=session' not in self.headers.get('Cookie', ''):
                return self.respond(403)
            if self.path.endswith('/stop'):
                return self.respond(302, headers={'Location': '/jenkins/job/demo/3/'})
            return self.respond(201, headers={'Location': '/jenkins/queue/item/17/'})

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/jenkins', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('value,expected', [
    ('127.0.0.1:8080', 'http://127.0.0.1:8080'),
    (' https://ci.example/jenkins/ ', 'https://ci.example/jenkins'),
    ('[::1]:8080/jenkins', 'http://[::1]:8080/jenkins'),
])
def test_urls(value, expected):
    assert normalize_jenkins_url(value) == expected


@pytest.mark.parametrize('value', ['ftp://ci', 'http://user:secret@ci', 'ci:99999',
    'http://ci?token=secret', 'http://ci/#fragment', 'http://ci/../admin', 'http://ci\\evil', ''])
def test_invalid_urls(value):
    with pytest.raises(ValueError):
        normalize_jenkins_url(value)


def test_folder_paths():
    assert job_path('团队/release #1') == 'job/%E5%9B%A2%E9%98%9F/job/release%20%231'
    for value in ('../admin', 'a//b', '/demo', 'a/./b'):
        with pytest.raises(ValueError):
            job_path(value)


def test_real_http_discovery_and_agent_tools(jenkins, tmp_path):
    url, calls = jenkins
    cfg = MCPConfig(name='CI', service_type='jenkins', url=url, username='operator',
        password='private-password', enabled=True).model_dump()
    store = Store(tmp_path)
    store.put('mcp', 'ci', cfg)

    async def run():
        tools = await discover(store, 'ci')
        assert {tool['name'] for tool in tools} == set(cfg['allowed_tools'])
        async with ToolHub(store, ['ci']) as hub:
            assert 'private-password' not in json.dumps(hub.tools)
            async def call(name, args):
                alias = next(alias for alias, route in hub.routes.items() if route[1] == name)
                return json.loads(json.loads(await hub.call(alias, args))['content'][0]['text'])
            assert (await call('list_jobs', {'limit': 1}))['next_offset'] == 1
            result = await call('trigger_build', {'job': 'folder/demo', 'parameters': {'branch': 'main', 'flag': True}})
            assert result['accepted'] and result['queue_id'] == 17
            assert (await call('queue_info', {'queue_id': 17}))['executable']['number'] == 3
            log = await call('build_log', {'job': 'demo', 'build': 3, 'max_bytes': 5})
            assert log['text'] == 'hello' and log['next_start'] == 5 and log['truncated']
            assert (await call('build_log', {'job': 'demo', 'build': 3, 'start': 5}))['text'] == ' world'
            assert (await call('stop_build', {'job': 'demo', 'build': 3}))['requested']
            assert (await call('get_job_config', {'job': 'demo'}))['xml'] == '<project/>'
            assert (await call('update_job_config', {'job': 'demo', 'xml': '<project/>'}))['updated']
            assert (await call('create_job', {'name': 'new', 'folder': 'team', 'xml': '<project/>'}))['job'] == 'team/new'
            assert (await call('set_job_enabled', {'job': 'demo', 'enabled': False}))['enabled'] is False
            with pytest.raises(ValidationError):
                await call('trigger_build', {'job': 'demo', 'unknown': True})
        store.put('mcp', 'ci', {**cfg, 'allowed_tools': ['list_jobs']})
        async with ToolHub(store, ['ci']) as hub:
            assert len(hub.routes) == 1
            with pytest.raises(ValueError, match='未授权'):
                await hub.call('trigger_build', {'job': 'demo'})
    asyncio.run(run())
    posts = [call for call in calls if call[0] == 'POST']
    assert posts[0][1] == '/jenkins/job/folder/job/demo/buildWithParameters'
    assert parse_qs(posts[0][2].decode()) == {'branch': ['main'], 'flag': ['true']}


def test_failed_auth_is_sanitized(jenkins):
    url, calls = jenkins
    session = JenkinsSession({'url': url, 'username': 'operator', 'password': 'wrong-secret'})
    with pytest.raises(ValueError) as error:
        asyncio.run(session.test())
    assert 'wrong-secret' not in str(error.value)
    assert 'private-password' not in str(error.value)


@pytest.mark.parametrize('destination', ['https://evil.example/', '/jenkins/login', '/jenkins/securityRealm/commenceLogin'])
def test_post_redirect_does_not_claim_success_or_forward_auth(destination):
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path.endswith('crumbIssuer/api/json'):
            return httpx.Response(404)
        return httpx.Response(302, headers={'Location': destination})
    async def run():
        session = JenkinsSession({'url': 'https://ci.example/jenkins', 'username': 'u', 'password': 'p'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                await session.trigger_build(client, 'demo')
    asyncio.run(run())
    assert len(calls) == 2


def test_token_without_crumb_and_server_log_offset():
    posts = []
    def handler(request):
        if request.url.path.endswith('crumbIssuer/api/json'):
            return httpx.Response(404)
        if request.url.path.endswith('progressiveText'):
            return httpx.Response(200, content=b'visible text', headers={'X-Text-Size': '150'})
        posts.append(request)
        return httpx.Response(201, headers={'Location': '/queue/item/9/'})
    async def run():
        session = JenkinsSession({'url': 'https://ci.example', 'username': 'u', 'password': 'p'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert (await session.trigger_build(client, 'demo'))['queue_id'] == 9
            assert (await session.build_log(client, 'demo', 1))['next_start'] == 150
    asyncio.run(run())
    assert len(posts) == 1


def test_anonymous_identity_fails_connection_test(monkeypatch):
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def connection(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={'authenticated': False}))) as client:
            yield client
    monkeypatch.setattr(JenkinsSession, 'client', connection)
    session = JenkinsSession({'url': 'https://ci.example', 'username': 'u', 'password': 'p'})
    with pytest.raises(ValueError, match='登录身份'):
        asyncio.run(session.test())
