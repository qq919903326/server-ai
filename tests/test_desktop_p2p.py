"""Real loopback WebRTC/DataChannel regression; no model or external service."""
import asyncio

import httpx
import pytest
from fastapi import FastAPI

from backend.projects import ProjectService
from backend.store import Store
from file_mcp.agent import connect
from file_mcp.desktop_assistant import DesktopFiles, synchronize
from file_mcp.desktop_bootstrap import read_bootstrap


def test_http_desktop_pair_local_grant_real_p2p_read_and_revoke(tmp_path, monkeypatch):
    template = tmp_path / 'template.exe'
    template.write_bytes(b'MZ-protocol-test-only')
    monkeypatch.setenv('AI_ASSISTANT_EXE', str(template))
    monkeypatch.setenv('APP_TOKEN', 'protocol-test-admin')
    monkeypatch.setenv('FILE_ICE_SERVERS_JSON', '[]')
    root = tmp_path / 'chosen'
    root.mkdir()
    (root / 'hello.txt').write_text('read through the real P2P data channel', encoding='utf-8')

    async def scenario():
        service = ProjectService(Store(tmp_path / 'db'))
        app = FastAPI()
        app.include_router(service.router())
        peer = None
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://203.0.113.10:8000') as client:
                response = await client.post('/api/projects/assistant-download')
                assert response.status_code == 200
                paired = tmp_path / 'paired.exe'
                paired.write_bytes(response.content)
                cfg = read_bootstrap(paired)
                response = await client.post('/agent/desktop-claim', json={k: cfg[k] for k in ('pairing_id', 'ticket')})
                assert response.status_code == 200
                device = response.json()
                client.headers['Authorization'] = 'Bearer ' + device['token']
                files = DesktopFiles({'roots': {}}, lambda state: None)
                id = files.authorize(str(root))
                payload = await synchronize(client, files)
                peer = await connect(client, device['device_id'], device['token'], files, payload['ice_servers'], '/agent/signal/')
                result = await service.call(id, 'read_text', {'path': 'hello.txt'})
                assert result['text'] == 'read through the real P2P data channel'
                entries = await service.call(id, 'list_directory', {'path': ''})
                assert any(item['name'] == 'hello.txt' for item in entries['entries'])
                assert 'path' not in service.store.get('projects', id)
                await client.delete('/api/projects/' + id)
                with pytest.raises(ValueError):
                    await service.call(id, 'read_text', {'path': 'hello.txt'})
                await synchronize(client, files)
                with pytest.raises(ValueError):
                    files.call('read_text', {'root_id': id, 'path': 'hello.txt'})
        finally:
            if peer:
                await peer.close()
            await service.hub.close()

    asyncio.run(scenario())
