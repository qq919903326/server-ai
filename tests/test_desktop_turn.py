"""Local synthetic TURN/TCP + authenticated WS; no coturn or public network."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import hmac
import socket
import struct
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from aioice import Connection, stun, turn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect

from backend import desktop_turn as backend_turn
from backend.projects import ProjectService
from backend.store import Store
from file_mcp import desktop_turn as desktop


TUNNEL = {'path': '/agent/desktop-turn', 'server_host': '127.0.0.1', 'server_port': 3478}


def make_app(tmp_path):
    service = ProjectService(Store(tmp_path / 'db'))
    for id, kind, expiry in [('pc-test', 'desktop', time.time() + 172800),
                             ('web-test', 'browser', time.time() + 100),
                             ('old-test', 'desktop', time.time() - 1)]:
        service.store.put('devices', id, {'kind': kind, 'expires_at': expiry,
            'token_hash': hashlib.sha256((id + '-token').encode()).hexdigest()})
    app = FastAPI()
    app.include_router(service.router())
    return app, service


def auth(id='pc-test'):
    return {'Authorization': 'Bearer ' + id + '-token'}


def test_advertisement_secret_expiry_and_browser_isolation(tmp_path, monkeypatch):
    monkeypatch.delenv('FILE_DESKTOP_TURN_SECRET', raising=False)
    app, service = make_app(tmp_path)
    with TestClient(app) as client:
        assert 'turn_tunnel' not in client.get('/agent/projects', headers=auth()).json()
        monkeypatch.setenv('FILE_DESKTOP_TURN_SECRET', 'test-turn-secret')
        result = client.get('/agent/projects', headers=auth()).json()
        assert result['turn_tunnel'] == TUNNEL
        ice = result['ice_servers'][0]
        assert ice['urls'] == 'turn:127.0.0.1:3478?transport=tcp'
        expiry, id = ice['username'].split(':')
        assert id == 'pc-test' and 86395 <= int(expiry) - time.time() <= 86400
        assert ice['credential'] == base64.b64encode(hmac.new(b'test-turn-secret', ice['username'].encode(), hashlib.sha1).digest()).decode()
        assert 'test-turn-secret' not in str(result)
        browser = client.get('/agent/projects', headers=auth('web-test')).json()
        assert 'turn_tunnel' not in browser
        assert browser['ice_servers'] == service.hub.config.ice_servers
        device = service.store.get('devices', 'pc-test')
        device['expires_at'] = time.time() + 600
        service.store.put('devices', 'pc-test', device)
        assert int(client.get('/agent/projects', headers=auth()).json()['ice_servers'][0]['username'].split(':')[0]) == int(device['expires_at'])


def test_ws_explicit_auth_kind_expiry_disabled(tmp_path, monkeypatch):
    app, service = make_app(tmp_path)
    with TestClient(app) as client:
        monkeypatch.setenv('FILE_DESKTOP_TURN_SECRET', 'test-secret')
        for headers in ({}, {'Authorization': 'Bearer admin'}, auth('web-test'), auth('old-test')):
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect('/agent/desktop-turn', headers=headers):
                    pass
            assert exc.value.code == 1008
        monkeypatch.delenv('FILE_DESKTOP_TURN_SECRET')
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/agent/desktop-turn', headers=auth()):
                pass
    assert not service.turn_tunnel.connections


@asynccontextmanager
async def running_app(app):
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='critical', ws='websockets', ws_max_size=backend_turn.MAX_FRAME))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(.01)
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


@pytest.mark.parametrize('revocation', ['delete', 'expire'])
def test_backend_stream_limits_revocation_cleanup(tmp_path, monkeypatch, revocation):
    monkeypatch.setenv('FILE_DESKTOP_TURN_SECRET', 'test-secret')
    monkeypatch.setattr(backend_turn, 'RECHECK_SECONDS', .03)
    app, service = make_app(tmp_path)

    async def scenario():
        opened = set()
        async def echo(reader, writer):
            opened.add(writer)
            try:
                while data := await reader.read(8192):
                    if data == b'EOF':
                        return
                    writer.write(data)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                opened.remove(writer)
        tcp = await asyncio.start_server(echo, '127.0.0.1', 0)
        original = asyncio.open_connection
        async def fixed(host, port, **kwargs):
            assert (host, port) == ('127.0.0.1', 3478)
            return await original('127.0.0.1', tcp.sockets[0].getsockname()[1], **kwargs)
        # Patch only the backend's dial, not WebSocket client networking.
        monkeypatch.setattr(backend_turn, 'asyncio', SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio) if name != 'open_connection'}, open_connection=fixed))
        try:
            async with running_app(app) as origin:
                url = origin.replace('http:', 'ws:') + '/agent/desktop-turn?host=evil&port=123'
                async with connect(url, additional_headers=auth(), proxy=None) as ws:
                    await ws.send(b'\x00TURN' * 20000)  # Oversized WS message.
                    with pytest.raises(Exception):
                        await ws.recv()
                    assert ws.close_code == 1009
                async with connect(url, additional_headers=auth(), proxy=None) as ws:
                    await ws.send('not binary')
                    with pytest.raises(Exception):
                        await ws.recv()
                    assert ws.close_code == 1003
                async with connect(url, additional_headers=auth(), proxy=None) as ws:
                    await ws.send(b'x' * backend_turn.MAX_FRAME)
                    received = b''
                    while len(received) < backend_turn.MAX_FRAME:
                        received += await asyncio.wait_for(ws.recv(), 2)
                    assert received == b'x' * backend_turn.MAX_FRAME
                    await ws.send(b'EOF')
                    with pytest.raises(Exception):
                        await ws.recv()
                    assert ws.close_code == 1000
                sockets = [await connect(url, additional_headers=auth(), proxy=None) for _ in range(4)]
                try:
                    with pytest.raises(Exception):
                        await connect(url, additional_headers=auth(), proxy=None)
                    await sockets[0].send(b'turn bytes')
                    assert await sockets[0].recv() == b'turn bytes'
                    if revocation == 'delete':
                        service.store.delete('devices', 'pc-test')
                    else:
                        device = service.store.get('devices', 'pc-test')
                        service.store.put('devices', 'pc-test', {**device, 'expires_at': time.time() - 1})
                    for ws in sockets:
                        with pytest.raises(Exception):
                            await asyncio.wait_for(ws.recv(), 2)
                        assert ws.close_code == 1008
                finally:
                    await asyncio.gather(*(ws.close() for ws in sockets))
                async with asyncio.timeout(2):
                    while opened or service.turn_tunnel.connections:
                        await asyncio.sleep(.01)
        finally:
            tcp.close()
            await tcp.wait_closed()
    asyncio.run(scenario())


class FakeWebSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []
        self.local_address = ('127.0.0.1', 11111)
        self.gate = asyncio.Event()
        self.gate.set()
        self.closed = False

    async def send(self, data):
        await self.gate.wait()
        self.sent.append(data)

    async def recv(self):
        return await self.incoming.get()

    async def close(self):
        self.closed = True


def test_stream_fragmentation_backpressure_close_and_overflow():
    async def scenario():
        class Protocol(turn.TurnClientTcpProtocol):
            def __init__(self):
                super().__init__(('127.0.0.1', 3478), 'user', 'password', 600, 500)
                self.messages = []
                self.lost = []
                self.flow = []
            def datagram_received(self, data, addr):
                self.messages.append((data, addr))
            def connection_lost(self, error):
                self.lost.append(error)
            def pause_writing(self):
                self.flow.append('pause')
            def resume_writing(self):
                self.flow.append('resume')
        ws, protocol = FakeWebSocket(), Protocol()
        transport = desktop.WebSocketStreamTransport(ws, protocol, ('127.0.0.1', 3478))
        frame = bytes(stun.Message(stun.Method.BINDING, stun.Class.REQUEST))
        for data in (frame[:3], frame[3:] + frame):
            await ws.incoming.put(data)
        transport.write(b'x' * (desktop.MAX_FRAME + 1))
        async with asyncio.timeout(2):
            while len(protocol.messages) != 2 or len(ws.sent) != 2:
                await asyncio.sleep(0)
        assert b''.join(ws.sent) == b'x' * (desktop.MAX_FRAME + 1)
        assert all(addr == ('127.0.0.1', 3478) for _, addr in protocol.messages)
        ws.gate.clear()
        transport.set_write_buffer_limits(high=100, low=10)
        transport.write(b'a' * 200)
        assert protocol.flow == ['pause']
        ws.gate.set()
        async with asyncio.timeout(2):
            while protocol.flow != ['pause', 'resume']:
                await asyncio.sleep(0)
        transport.close();transport.close()
        await transport.wait_closed()
        assert len(protocol.lost) == 1 and ws.closed and transport.get_write_buffer_size() == 0
        ws, protocol = FakeWebSocket(), Protocol()
        transport = desktop.WebSocketStreamTransport(ws, protocol, ('127.0.0.1', 3478))
        transport.write(b'x' * (desktop.MAX_BUFFER + 1))
        await asyncio.wait_for(transport.wait_closed(), 2)
        assert ws.closed and len(protocol.lost) == 1
        ws, protocol = FakeWebSocket(), Protocol()
        transport = desktop.WebSocketStreamTransport(ws, protocol, ('127.0.0.1', 3478))
        protocol.refresh_task = asyncio.create_task(asyncio.sleep(3600))
        await ws.incoming.put('unexpected text frame')
        await asyncio.wait_for(transport.wait_closed(), 2)
        assert protocol.refresh_task.cancelled()
        assert isinstance(protocol.lost[0], ValueError)
    asyncio.run(scenario())


def test_real_aioice_allocate_challenge_refresh_and_channel_data(tmp_path, monkeypatch):
    """The WS carries raw TURN, including standard coturn REST authentication."""
    monkeypatch.setenv('FILE_DESKTOP_TURN_SECRET', 'test-secret')
    app, service = make_app(tmp_path)

    async def scenario():
        seen = []
        writers = set()
        async def fake_turn(reader, writer):
            writers.add(writer)
            try:
                while True:
                    header = await reader.readexactly(4)
                    kind, length = struct.unpack('!HH', header)
                    if kind >= 0x4000:
                        body = await reader.readexactly(length + stun.padding_length(length))
                        writer.write(header + body)  # Echo ChannelData, still library-framed.
                        await writer.drain()
                        continue
                    raw = header + await reader.readexactly(16 + length)
                    request = stun.parse_message(raw)
                    seen.append(request.message_method)
                    key = None
                    if 'USERNAME' not in request.attributes:
                        response = stun.Message(request.message_method, stun.Class.ERROR, transaction_id=request.transaction_id)
                        response.attributes.update({'ERROR-CODE': (401, 'Unauthorized'), 'NONCE': b'fake-nonce', 'REALM': 'test-realm'})
                    else:
                        username = request.attributes['USERNAME']
                        password = base64.b64encode(hmac.new(b'test-secret', username.encode(), hashlib.sha1).digest()).decode()
                        key = turn.make_integrity_key(username, 'test-realm', password)
                        stun.parse_message(raw, integrity_key=key)
                        response = stun.Message(request.message_method, stun.Class.RESPONSE, transaction_id=request.transaction_id)
                        if request.message_method == stun.Method.ALLOCATE:
                            response.attributes.update({'LIFETIME': 1, 'XOR-RELAYED-ADDRESS': ('172.19.183.106', 49160), 'XOR-MAPPED-ADDRESS': ('127.0.0.1', 12345)})
                        elif request.message_method == stun.Method.REFRESH:
                            response.attributes['LIFETIME'] = request.attributes['LIFETIME']
                    if key:
                        response.add_message_integrity(key)
                    data = bytes(response)
                    # Split STUN responses across arbitrary WS/TCP chunks.
                    writer.write(data[:7]);await writer.drain();await asyncio.sleep(.01)
                    writer.write(data[7:]);await writer.drain()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close();await writer.wait_closed();writers.remove(writer)
        tcp = await asyncio.start_server(fake_turn, '127.0.0.1', 0)
        original_dial = asyncio.open_connection
        async def fixed(host, port, **kwargs):
            assert (host, port) == ('127.0.0.1', 3478)
            return await original_dial(host, tcp.sockets[0].getsockname()[1], **kwargs)
        monkeypatch.setattr(backend_turn, 'asyncio', SimpleNamespace(**{name: getattr(asyncio, name) for name in dir(asyncio) if name != 'open_connection'}, open_connection=fixed))
        original_endpoint = turn.create_turn_endpoint
        class Receiver(asyncio.DatagramProtocol):
            def __init__(self):
                self.received = asyncio.Queue()
                self.lost = asyncio.Event()
            def datagram_received(self, data, addr):
                self.received.put_nowait((data, addr))
            def connection_lost(self, exc):
                self.lost.set()
        try:
            async with running_app(app) as origin:
                async with httpx.AsyncClient(base_url=origin, headers=auth()) as client:
                    payload = (await client.get('/agent/projects')).json()
                async with desktop.desktop_turn_tunnel(origin, 'pc-test-token', payload['turn_tunnel']):
                    ice = payload['ice_servers'][0]
                    relay, receiver = await turn.create_turn_endpoint(Receiver, ('127.0.0.1', 3478),
                        ice['username'], ice['credential'], lifetime=600, channel_refresh_time=500, transport='tcp')
                    assert relay.get_extra_info('sockname') == ('172.19.183.106', 49160)
                    relay.sendto(b'payload carried by TURN', ('172.19.183.106', 23456))
                    assert await asyncio.wait_for(receiver.received.get(), 2) == (b'payload carried by TURN', ('172.19.183.106', 23456))
                    async with asyncio.timeout(2):
                        while stun.Method.REFRESH not in seen:
                            await asyncio.sleep(.03)
                    relay.close()
                    await asyncio.wait_for(receiver.lost.wait(), 3)
                    connection = Connection(ice_controlling=True,
                        turn_server=('127.0.0.1', 3478), turn_username=ice['username'],
                        turn_password=ice['credential'], turn_transport='tcp')
                    try:
                        await connection.gather_candidates()
                        assert any(candidate.type == 'relay' and candidate.host == '172.19.183.106'
                                   for candidate in connection.local_candidates)
                    finally:
                        await connection.close()
                assert turn.create_turn_endpoint is original_endpoint
                assert not relay.inner.refresh_task or relay.inner.refresh_task.done()
                async with asyncio.timeout(2):
                    while writers or service.turn_tunnel.connections:
                        await asyncio.sleep(.01)
        finally:
            tcp.close();await tcp.wait_closed()
    asyncio.run(scenario())


def test_context_isolation_arguments_and_cancelled_allocation(monkeypatch):
    async def scenario():
        calls = []
        async def original(*args, **kwargs):
            calls.append((args, kwargs))
            return 'original'
        original.__signature__ = __import__('inspect').signature(turn.create_turn_endpoint)
        monkeypatch.setattr(turn, 'create_turn_endpoint', original)
        unrelated_go = asyncio.Event()
        async def unrelated():
            await unrelated_go.wait()
            return await turn.create_turn_endpoint(lambda: None, ('127.0.0.1', 3478), 'other', 'private', transport='tcp')
        unrelated_task = asyncio.create_task(unrelated())
        sockets = []
        async def fake_connect(*args, **kwargs):
            assert kwargs['proxy'] is None
            assert kwargs['additional_headers'] == {'Authorization': 'Bearer token'}
            ws = FakeWebSocket();sockets.append(ws)
            return ws
        monkeypatch.setattr(desktop, 'connect', fake_connect)
        async with desktop.desktop_turn_tunnel('https://example.test:8000', 'token', TUNNEL):
            unrelated_go.set()
            assert await unrelated_task == 'original'
            assert await turn.create_turn_endpoint(lambda: None, ('other', 3478), 'u', 'p', transport='tcp') == 'original'
            assert await turn.create_turn_endpoint(lambda: None, ('127.0.0.1', 3478), 'u', 'p', transport='udp') == 'original'
            with pytest.raises(asyncio.TimeoutError):
                async with asyncio.timeout(.03):
                    await turn.create_turn_endpoint(asyncio.DatagramProtocol, ('127.0.0.1', 3478), 'u', 'p', transport='tcp')
            assert sockets[0].closed
        assert turn.create_turn_endpoint is original and len(calls) == 3
        with pytest.raises(ValueError):
            async with desktop.desktop_turn_tunnel('https://example.test', 'token', {**TUNNEL, 'server_host': 'evil'}):
                pass
    asyncio.run(scenario())


def test_lost_stream_releases_pending_library_transactions():
    async def scenario():
        ws = FakeWebSocket()
        protocol = desktop.TunnelTurnClientTcpProtocol(('127.0.0.1', 3478), 'user', 'password', 600, 500)
        stream = desktop.WebSocketStreamTransport(ws, protocol, ('127.0.0.1', 3478))
        allocation = asyncio.create_task(protocol.connect())
        async with asyncio.timeout(2):
            while not protocol.transactions:
                await asyncio.sleep(0)
        stream.close()
        await stream.wait_closed()
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(allocation, 1)
        assert not protocol.transactions
    asyncio.run(scenario())
