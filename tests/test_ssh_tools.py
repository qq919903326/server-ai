import asyncio
import socket
import stat
import threading
import time
from types import SimpleNamespace

import paramiko
import pytest

from backend.ssh_tools import MAX_OUTPUT, SSHSession


@pytest.fixture
def ssh_server():
    files = {'/notes.txt': b'hello remote'}
    commands, transports, workers = [], [], []
    key = paramiko.RSAKey.generate(2048)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(0.1)
    stopped = threading.Event()

    class Handle(paramiko.SFTPHandle):
        def read(self, offset, length):
            return files[self.path][offset:offset + length]

        def write(self, offset, data):
            old = files[self.path]
            files[self.path] = old[:offset] + data + old[offset + len(data):]
            return paramiko.SFTP_OK

    class SFTP(paramiko.SFTPServerInterface):
        def open(self, path, flags, attr):
            import os
            if flags & os.O_CREAT:
                files.setdefault(path, b'')
            if path not in files:
                return paramiko.SFTP_NO_SUCH_FILE
            if flags & os.O_TRUNC:
                files[path] = b''
            handle = Handle(flags)
            handle.path = path
            return handle

        def list_folder(self, path):
            result = []
            for name, data in files.items():
                item = paramiko.SFTPAttributes()
                item.filename, item.st_mode = name.lstrip('/'), stat.S_IFREG | 0o644
                item.st_size, item.st_mtime = len(data), 1
                result.append(item)
            return result

    class Server(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ('operator', 'ssh-secret') else paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return 'password'

        def check_channel_request(self, kind, channel_id):
            return paramiko.OPEN_SUCCEEDED if kind == 'session' else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

        def check_channel_exec_request(self, channel, command):
            commands.append(command.decode())
            def respond():
                time.sleep(0.05)
                try:
                    channel.sendall(b'remote output\n')
                    channel.sendall_stderr(b'remote error\n')
                    channel.send_exit_status(7)
                    channel.close()
                except (OSError, EOFError):
                    pass
            worker = threading.Thread(target=respond, daemon=True)
            workers.append(worker)
            worker.start()
            return True

    def serve():
        while not stopped.is_set():
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            transport = paramiko.Transport(connection)
            transports.append(transport)
            transport.add_server_key(key)
            transport.set_subsystem_handler('sftp', paramiko.SFTPServer, SFTP)
            try:
                transport.start_server(server=Server())
            except (paramiko.SSHException, EOFError, OSError):
                transport.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    yield SimpleNamespace(port=listener.getsockname()[1], files=files, commands=commands, key=key)
    stopped.set()
    listener.close()
    for transport in transports:
        transport.close()
    worker.join(5)
    for worker in workers:
        worker.join(5)


def test_ssh_password_command_and_sftp_over_real_protocol(ssh_server, tmp_path):
    session = SSHSession({'host': '127.0.0.1', 'port': ssh_server.port,
        'username': 'operator', 'password': 'ssh-secret'}, tmp_path)
    async def run():
        await session.test()
        result = await session.call_tool('exec', {'command': 'inspect', 'cwd': "/srv/app's folder"})
        import json
        output = json.loads(result.content[0].text)
        assert output['stdout'] == 'remote output\n'
        assert output['stderr'] == 'remote error\n'
        assert output['exit_code'] == 7
        assert output['timed_out'] is False
        assert ssh_server.commands == ['cd -- \'/srv/app\'"\'"\'s folder\' && inspect']
        read = json.loads((await session.call_tool('read_file', {'path': '/notes.txt', 'max_bytes': 5})).content[0].text)
        assert read['text'] == 'hello'
        assert read['next_offset'] == 5
        await session.call_tool('write_file', {'path': '/notes.txt', 'content': '新的内容'})
        assert ssh_server.files['/notes.txt'] == '新的内容'.encode()
        listing = json.loads((await session.call_tool('list_directory', {'path': '/'})).content[0].text)
        assert listing['entries'][0]['name'] == 'notes.txt'
        assert listing['next_offset'] is None
    asyncio.run(run())
    keys = paramiko.HostKeys(str(tmp_path / 'ssh_known_hosts'))
    assert keys.lookup(f'[127.0.0.1]:{ssh_server.port}') is not None


def test_ssh_authentication_failure_is_sanitized(ssh_server, tmp_path):
    session = SSHSession({'host': '127.0.0.1', 'port': ssh_server.port,
        'username': 'operator', 'password': 'wrong-secret'}, tmp_path)
    with pytest.raises(ValueError, match='认证失败') as error:
        asyncio.run(session.test())
    assert 'wrong-secret' not in str(error.value)


def test_ssh_rejects_changed_host_key(ssh_server, tmp_path):
    keys = paramiko.HostKeys()
    keys.add(f'[127.0.0.1]:{ssh_server.port}', 'ssh-rsa', paramiko.RSAKey.generate(2048))
    keys.save(str(tmp_path / 'ssh_known_hosts'))
    session = SSHSession({'host': '127.0.0.1', 'port': ssh_server.port,
        'username': 'operator', 'password': 'ssh-secret'}, tmp_path)
    with pytest.raises(ValueError, match='主机密钥'):
        asyncio.run(session.test())


@pytest.mark.parametrize('error,code', [
    (paramiko.AuthenticationException('secret-password'), 'ssh_authentication_failed'),
    (paramiko.BadAuthenticationType('secret-password', ['publickey']), 'ssh_password_login_unavailable'),
    (socket.gaierror('secret-password'), 'ssh_dns_failed'),
    (TimeoutError('secret-password'), 'ssh_timeout'),
    (paramiko.SSHException('secret-password'), 'ssh_protocol_error'),
    (paramiko.ssh_exception.NoValidConnectionsError({('127.0.0.1', 22): ConnectionRefusedError(111, 'secret-password')}), 'ssh_connection_refused'),
])
def test_ssh_connection_errors_keep_specific_safe_reason(monkeypatch, tmp_path, error, code):
    from backend.tool_errors import ToolExecutionError
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(paramiko.SSHClient, 'connect', fail)
    session = SSHSession({'host': '127.0.0.1', 'username': 'operator', 'password': 'secret-password'}, tmp_path)
    with pytest.raises(ToolExecutionError) as result:
        asyncio.run(session.test())
    assert result.value.code == code
    assert 'secret-password' not in str(result.value)


def test_ssh_missing_path_is_not_reported_as_connection_failure(ssh_server, tmp_path):
    from backend.tool_errors import ToolExecutionError
    session = SSHSession({'host': '127.0.0.1', 'port': ssh_server.port,
        'username': 'operator', 'password': 'ssh-secret'}, tmp_path)
    with pytest.raises(ToolExecutionError) as result:
        asyncio.run(session.call_tool('read_file', {'path': '/missing.txt'}))
    assert result.value.code == 'ssh_path_not_found'
    assert '已连接' in str(result.value)


def test_agent_receives_specific_ssh_error_instead_of_generic_failure():
    import json
    from backend.agent import agent_events
    from backend.tool_errors import ToolExecutionError
    messages = [{'role': 'user', 'content': '查看服务器'}]
    async def model(messages, tools):
        if messages[-1]['role'] == 'tool':
            result = json.loads(messages[-1]['content'])
            assert result == {'code': 'ssh_host_key_mismatch', 'error': 'SSH 主机公钥缓存不一致'}
            return {'role': 'assistant', 'content': result['error']}
        return {'role': 'assistant', 'tool_calls': [{'id': 'call', 'function': {'name': 'ssh_exec', 'arguments': '{}'}}]}
    async def call(*args):
        raise ToolExecutionError('ssh_host_key_mismatch', 'SSH 主机公钥缓存不一致')
    async def run():
        return [item async for item in agent_events(messages, [], model, call)]
    events = asyncio.run(run())
    assert next(data for event, data in events if event == 'answer')['text'] == 'SSH 主机公钥缓存不一致'


@pytest.mark.parametrize('finished', [True, False])
def test_ssh_output_limit_timeout_and_cleanup(monkeypatch, tmp_path, finished):
    from contextlib import contextmanager
    class Channel:
        closed = False
        count = 0
        def recv_ready(self): return self.count < 10
        def recv_stderr_ready(self): return False
        def recv_stderr(self, size): return b''
        def recv(self, size):
            self.count += 1
            return b'x' * size
        def exit_status_ready(self): return finished
        def recv_exit_status(self): return 0
        def close(self): self.closed = True
    channel = Channel()
    stream = SimpleNamespace(channel=channel, close=lambda: None)
    @contextmanager
    def connect(self):
        yield SimpleNamespace(exec_command=lambda *args, **kwargs: (stream, stream, stream))
    monkeypatch.setattr(SSHSession, 'connect', connect)
    result = SSHSession({}, tmp_path).exec('command', timeout=0.01)
    assert len(result['stdout']) <= MAX_OUTPUT
    assert result['timed_out'] is not finished
    assert result['remote_process_may_be_running'] is not finished
    assert channel.closed
    if finished:
        assert result['truncated']
