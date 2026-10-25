"""Check configured SSH connections without printing credentials or changing remote files."""
import asyncio
import base64
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app import store
from backend.ssh_tools import SSHSession


async def main():
    configs = [cfg for cfg in store.all('mcp') if cfg.get('service_type') == 'ssh']
    print(json.dumps({'ssh_connections': len(configs)}, ensure_ascii=False))
    for cfg in configs:
        print(json.dumps({'id': cfg['id'], 'host': cfg['host'], 'port': cfg.get('port', 22),
            'enabled': cfg.get('enabled'), 'has_password': bool(cfg.get('password')),
            'allowed_tools': cfg.get('allowed_tools', [])}, ensure_ascii=False))
        session = SSHSession(cfg, store.root)
        for name, operation in [('login', session.test),
                ('exec', lambda: session.call_tool('exec', {'command': 'printf "SSH_DIAGNOSTIC_OK\\n"; id; pwd', 'timeout': 15})),
                ('sftp', lambda: session.call_tool('list_directory', {'path': '.', 'limit': 1}))]:
            try:
                result = await operation()
                print(json.dumps({'check': name, 'ok': True,
                    'result': result.model_dump() if result else None}, ensure_ascii=False))
            except Exception as error:
                original = error.__context__ or error
                keys = {}
                for label, attr in [('received_fingerprint', 'key'), ('expected_fingerprint', 'expected_key')]:
                    key = getattr(original, attr, None)
                    if key is not None:
                        keys[label] = 'SHA256:' + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip('=')
                print(json.dumps({'check': name, 'ok': False, 'message': str(error),
                    'exception_type': type(original).__name__, 'errno': getattr(original, 'errno', None), **keys}, ensure_ascii=False))


if __name__ == '__main__':
    asyncio.run(main())
