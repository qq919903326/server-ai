"""Bounded request/reply messages over one ordered WebRTC data channel."""
import asyncio
import json
from uuid import uuid4

MAX_MESSAGE = 60000


class RpcPeer:
    def __init__(self, pc, channel, handler=None):
        self.pc, self.channel, self.handler = pc, channel, handler
        self.pending, self.tasks = {}, set()
        self.closed = asyncio.Event()
        self.ready = asyncio.Event()
        if channel.readyState == 'open':
            self.ready.set()
        channel.on('open', self.ready.set)
        channel.on('close', self._disconnected)
        channel.on('message', self._message)

        @pc.on('connectionstatechange')
        def state_changed():
            if pc.connectionState in ('closed', 'failed'):
                self._disconnected()

    def _disconnected(self):
        self.closed.set()
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(ValueError('本地文件代理已断开'))

    def _send(self, value):
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded.encode('utf-8')) > MAX_MESSAGE:
            raise ValueError('响应过大，请缩小读取范围或分页大小')
        if not self.online or self.channel.bufferedAmount>256000:
            raise ValueError('本地文件代理未连接或通道繁忙')
        self.channel.send(encoded)

    @property
    def online(self):
        return not self.closed.is_set() and self.channel.readyState == 'open'

    def _message(self, raw):
        if not isinstance(raw,str) or len(raw.encode('utf-8'))>MAX_MESSAGE:
            return
        try:
            message = json.loads(raw)
            if not isinstance(message,dict) or not isinstance(message.get('id'),str):
                return
            if 'method' not in message:
                future = self.pending.get(message['id'])
                if future and not future.done():
                    future.set_result(message.get('result',{'error':'响应格式错误'}))
            elif len(self.tasks)<8:
                task = asyncio.create_task(self._serve(message))
                self.tasks.add(task)
                task.add_done_callback(self.tasks.discard)
        except (ValueError, TypeError):
            pass

    async def _serve(self, message):
        try:
            if message['method']=='ping':
                result = {'ok':True}
            elif self.handler and isinstance(message.get('arguments'),dict):
                result = await asyncio.to_thread(self.handler,message['method'],message['arguments'])
            else:
                raise ValueError('不支持的操作')
            self._send({'id':message['id'],'result':result})
        except Exception as exc:
            # Do not send OS exception messages: they can contain local paths.
            result = {'error':str(exc) if isinstance(exc,ValueError) else '无法读取：文件不存在、权限不足或文件已变化'}
            try:self._send({'id':message['id'],'result':result})
            except Exception:pass

    async def call(self, method, arguments, timeout=20):
        if not self.online or len(self.pending)>=8:
            raise ValueError('本地文件代理离线或请求过多')
        id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[id] = future
        try:
            self._send({'id':id,'method':method,'arguments':arguments})
            return await asyncio.wait_for(future,timeout)
        except asyncio.TimeoutError:
            raise ValueError('本地文件代理响应超时') from None
        finally:
            self.pending.pop(id,None)

    async def close(self):
        self._disconnected()
        for task in list(self.tasks):task.cancel()
        await asyncio.gather(*list(self.tasks),return_exceptions=True)
        await self.pc.close()
