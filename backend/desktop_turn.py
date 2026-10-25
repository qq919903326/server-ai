"""Authenticated, fixed-destination TURN/TCP byte tunnel (no file protocol)."""
import asyncio
import base64
import hashlib
import hmac
import os
import threading
import time

from fastapi import HTTPException, WebSocket, WebSocketDisconnect

MAX_FRAME = 64 * 1024
RECHECK_SECONDS = 5
TURN_ADDRESS = ('127.0.0.1', 3478)


def turn_config(device):
    secret = os.getenv('FILE_DESKTOP_TURN_SECRET')
    if not secret or device.get('kind') != 'desktop':
        return None
    # Reconnect before expiry: coturn REST credentials cannot rotate in-place.
    expires = min(int(time.time()) + 86400, int(device['expires_at']))
    username = f"{expires}:{device['id']}"
    credential = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode()
    return {
        'turn_tunnel': {'path': '/agent/desktop-turn', 'server_host': TURN_ADDRESS[0], 'server_port': TURN_ADDRESS[1]},
        'ice_server': {'urls': 'turn:127.0.0.1:3478?transport=tcp', 'username': username, 'credential': credential},
    }


class DesktopTurnTunnel:
    def __init__(self, authenticate):
        self.authenticate = authenticate
        self.connections = {}
        self.lock = threading.Lock()

    def authorized(self, websocket):
        if not os.getenv('FILE_DESKTOP_TURN_SECRET'):
            raise HTTPException(403, 'TURN disabled')
        device = self.authenticate(websocket)
        if device.get('kind') != 'desktop':
            raise HTTPException(403, 'Desktop device required')
        return device

    async def handle(self, websocket: WebSocket):
        try:
            device = self.authorized(websocket)
        except HTTPException:
            await websocket.close(code=1008)
            return
        id = device['id']
        with self.lock:
            count = self.connections.get(id, 0)
            if count < 4:
                self.connections[id] = count + 1
        if count >= 4:
            await websocket.close(code=1008)
            return
        writer = None
        tasks = []
        code = 1000
        try:
            await websocket.accept()
            # Never accept destination addresses from headers, query, or frames.
            reader, writer = await asyncio.wait_for(asyncio.open_connection(*TURN_ADDRESS, limit=MAX_FRAME), 5)
            self.authorized(websocket)

            async def upstream():
                while True:
                    message = await websocket.receive()
                    if message['type'] == 'websocket.disconnect':
                        return
                    data = message.get('bytes')
                    if data is None or len(data) > MAX_FRAME:
                        return 1009 if data is not None else 1003
                    writer.write(data)
                    await writer.drain()  # Bound buffering: don't read ahead of TCP.

            async def downstream():
                while data := await reader.read(MAX_FRAME):
                    await websocket.send_bytes(data)  # ASGI/network backpressure.

            async def revalidate():
                while True:
                    await asyncio.sleep(RECHECK_SECONDS)
                    self.authorized(websocket)

            tasks = [asyncio.create_task(coro()) for coro in (upstream, downstream, revalidate)]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                result = task.result()
                if result:
                    code = result
        except HTTPException:
            code = 1008
        except (OSError, asyncio.TimeoutError):
            code = 1011
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 2)
                except (OSError, asyncio.TimeoutError):
                    pass
            try:
                await websocket.close(code=code)
            except (RuntimeError, OSError, WebSocketDisconnect):
                pass
            with self.lock:
                remaining = self.connections[id] - 1
                if remaining:
                    self.connections[id] = remaining
                else:
                    del self.connections[id]
