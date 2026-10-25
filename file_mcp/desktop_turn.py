"""Desktop-only WebSocket stream transport for aioice's real TURN protocol."""
import asyncio
from contextlib import asynccontextmanager
from contextvars import ContextVar
import inspect
from urllib.parse import urlsplit, urlunsplit

from aioice import turn
from websockets.asyncio.client import connect

MAX_FRAME = 64 * 1024
MAX_BUFFER = 1024 * 1024
_binding = ContextVar('desktop_turn_binding', default=None)
_users = 0
_original = None


class TunnelTurnClientTcpProtocol(turn.TurnClientTcpProtocol):
    """Wake outstanding library transactions when the underlying stream dies."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stream_lost = asyncio.Event()

    def connection_lost(self, exc):
        self.stream_lost.set()
        super().connection_lost(exc)

    async def request(self, message):
        # Keep aioice's transaction/authentication implementation. Cancelling
        # its request coroutine also releases its retry timers and dictionary.
        if self.stream_lost.is_set():
            raise ConnectionError('TURN stream closed')
        request = asyncio.create_task(super().request(message))
        lost = asyncio.create_task(self.stream_lost.wait())
        try:
            done, _ = await asyncio.wait((request, lost), return_when=asyncio.FIRST_COMPLETED)
            if request in done:
                return request.result()
            raise ConnectionError('TURN stream closed')
        finally:
            request.cancel()
            lost.cancel()
            await asyncio.gather(request, lost, return_exceptions=True)


class WebSocketStreamTransport(asyncio.Transport):
    """Ordered binary stream; TURN framing/padding belongs entirely to aioice."""
    def __init__(self, websocket, protocol, server_addr):
        super().__init__()
        self.websocket = websocket
        self.protocol = protocol
        self.server_addr = server_addr
        self.queue = asyncio.Queue(maxsize=32)
        self.buffer_size = 0
        self.high, self.low = 256 * 1024, 64 * 1024
        self.closing = False
        self.started = False
        self.paused = False
        self.readable = asyncio.Event()
        self.readable.set()
        self.closed = asyncio.Event()
        protocol.connection_made(self)
        self.task = asyncio.create_task(self._run())

    def get_extra_info(self, name, default=None):
        if name == 'peername':
            return self.server_addr
        if name == 'sockname':
            return self.websocket.local_address
        return default

    def is_closing(self):
        return self.closing

    def get_write_buffer_size(self):
        return self.buffer_size

    def get_write_buffer_limits(self):
        return self.low, self.high

    def set_write_buffer_limits(self, high=None, low=None):
        high = 256 * 1024 if high is None else high
        low = high // 4 if low is None else low
        if not 0 <= low <= high <= MAX_BUFFER:
            raise ValueError('Invalid stream buffer limits')
        self.high, self.low = high, low

    def pause_reading(self):
        self.readable.clear()

    def resume_reading(self):
        self.readable.set()

    def write(self, data):
        if self.closing or not data:
            return
        # asyncio.Protocol.write is synchronous. Fail closed if the producer
        # ignores pause_writing, instead of accumulating unbounded data/tasks.
        chunks = (len(data) + MAX_FRAME - 1) // MAX_FRAME
        if self.buffer_size + len(data) > MAX_BUFFER or self.queue.qsize() + chunks > self.queue.maxsize:
            self.abort()
            return
        for offset in range(0, len(data), MAX_FRAME):
            self.queue.put_nowait(bytes(data[offset:offset + MAX_FRAME]))
        self.buffer_size += len(data)
        if self.buffer_size > self.high and not self.paused:
            self.paused = True
            self.protocol.pause_writing()

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def close(self):
        if not self.closing:
            self.closing = True
            if self.started:
                self.task.cancel()

    def abort(self):
        self.close()

    async def wait_closed(self):
        await self.closed.wait()

    async def _run(self):
        self.started = True
        tasks = []
        error = None
        try:
            if self.closing:
                raise asyncio.CancelledError
            async def send():
                while True:
                    data = await self.queue.get()
                    await self.websocket.send(data)
                    self.buffer_size -= len(data)
                    if self.paused and self.buffer_size <= self.low:
                        self.paused = False
                        self.protocol.resume_writing()

            async def receive():
                while True:
                    await self.readable.wait()
                    data = await self.websocket.recv()
                    if not isinstance(data, bytes) or len(data) > MAX_FRAME:
                        raise ValueError('Invalid TURN binary stream frame')
                    self.protocol.data_received(data)

            tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            error = exc
        finally:
            self.closing = True
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await self.websocket.close()
            except Exception as exc:
                if error is None:
                    error = exc
            finally:
                refresh = getattr(self.protocol, 'refresh_task', None)
                if refresh:
                    refresh.cancel()
                    await asyncio.gather(refresh, return_exceptions=True)
                while not self.queue.empty():
                    self.queue.get_nowait()
                self.buffer_size = 0
                try:
                    self.protocol.connection_lost(error)
                finally:
                    self.closed.set()


class TunnelTurnTransport(turn.TurnTransport):
    """Keep aioice allocation deletion, but bound shutdown after a lost stream."""
    def __init__(self, inner):
        super().__init__(inner)
        self.inner = inner
        self.close_task = None

    def close(self):
        if self.close_task is None:
            self.close_task = asyncio.create_task(self._delete())

    async def _delete(self):
        try:
            if not self.inner.transport.is_closing():
                async with asyncio.timeout(2):
                    await self.inner.delete()
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            self.inner.transport.close()
            await self.inner.transport.wait_closed()


async def _endpoint(*args, **kwargs):
    binding = _binding.get()
    original = _original
    if binding is None or not binding.active:
        return await original(*args, **kwargs)
    call = inspect.signature(original).bind(*args, **kwargs)
    call.apply_defaults()
    options = call.arguments
    if (options['server_addr'] != ('127.0.0.1', 3478)
            or options['transport'] != 'tcp' or options['ssl']):
        return await original(*args, **kwargs)
    websocket = await connect(binding.url, additional_headers={'Authorization': 'Bearer ' + binding.token},
                              proxy=None, max_size=MAX_FRAME, max_queue=4,
                              write_limit=MAX_FRAME, compression=None, open_timeout=5, close_timeout=2)
    stream = None
    try:
        inner = TunnelTurnClientTcpProtocol(options['server_addr'], username=options['username'],
            password=options['password'], lifetime=options['lifetime'], channel_refresh_time=options['channel_refresh_time'])
        stream = WebSocketStreamTransport(websocket, inner, options['server_addr'])
        relay = TunnelTurnTransport(inner)
        binding.relays.append(relay)
        protocol = options['protocol_factory']()
        await relay._connect(protocol)
        return relay, protocol
    except BaseException:
        if stream:
            stream.close()
            await stream.wait_closed()
        else:
            await websocket.close()
        raise


class TunnelBinding:
    def __init__(self, server, token, config):
        if config != {'path': '/agent/desktop-turn', 'server_host': '127.0.0.1', 'server_port': 3478}:
            raise ValueError('Unsupported desktop TURN tunnel')
        url = urlsplit(server)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
            raise ValueError('Invalid desktop server origin')
        self.url = urlunsplit(('wss' if url.scheme == 'https' else 'ws', url.netloc, config['path'], '', ''))
        self.token = token
        self.active = True
        self.relays = []


@asynccontextmanager
async def desktop_turn_tunnel(server, token, config):
    """Install only while helper connections exist; unrelated contexts delegate."""
    global _users, _original
    if config is None:
        yield
        return
    binding = TunnelBinding(server, token, config)
    if not _users:
        _original = turn.create_turn_endpoint
        turn.create_turn_endpoint = _endpoint
    _users += 1
    context = _binding.set(binding)
    try:
        yield
    finally:
        binding.active = False
        _binding.reset(context)
        for relay in binding.relays:
            relay.close()
        await asyncio.gather(*(r.close_task for r in binding.relays), return_exceptions=True)
        _users -= 1
        if not _users:
            turn.create_turn_endpoint = _original
        binding.token = ''


def credentials_expire_at(ice_servers):
    """Renew via ordinary reconnect/poll, before REST credentials expire."""
    for server in ice_servers:
        urls = server.get('urls', [])
        if isinstance(urls, str):
            urls = [urls]
        if 'turn:127.0.0.1:3478?transport=tcp' in urls:
            return int(server['username'].split(':', 1)[0]) - 60
    return float('inf')
