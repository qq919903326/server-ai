"""Local bootstrap credentials. Never log file contents or parser exceptions."""
import asyncio
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlsplit

from .model_catalog import cached_catalog, discover_models
from .schemas import ModelConfig

logger = logging.getLogger(__name__)
PROVIDERS = {
    'api.deepseek.com': ('deepseek', 'DeepSeek', 'https://api.deepseek.com', 'deepseek-flash'),
    'api.xiaomimimo.com': ('mimo', '小米 MiMo', 'https://api.xiaomimimo.com/v1', 'mimo-v2.6-flash'),
    'api.openai.com': ('openai', 'OpenAI', 'https://api.openai.com/v1', 'gpt-5.5'),
}
MAX_BYTES = 1024 * 1024
MAX_CONNECTIONS = 100


def official_endpoint(value):
    if not isinstance(value, str) or len(value) > 256 or any(ord(c) <= 32 for c in value):
        return None
    try:
        url = urlsplit(value)
        provider = PROVIDERS.get(url.hostname)
        if (not provider or url.scheme != 'https' or url.username is not None or
                url.password is not None or url.port not in (None, 443) or
                '?' in value or '#' in value or '\\' in value):
            return None
        # Exact API roots and common full endpoint URLs only; no arbitrary paths.
        if url.path.rstrip('/') not in ('', '/v1', '/models', '/v1/models',
                                       '/chat/completions', '/v1/chat/completions', '/v1/responses'):
            return None
        return provider
    except ValueError:
        return None


def parse_connections(payload):
    if not isinstance(payload, list) or len(payload) > MAX_CONNECTIONS:
        return []
    connections, seen = [], set()
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            continue
        key, endpoints = entry.get('apiKey'), entry.get('models')
        if (not isinstance(key, str) or not key.strip() or len(key) > 4096 or
                any(ord(c) < 32 or ord(c) == 127 for c in key) or
                not isinstance(endpoints, list) or len(endpoints) > MAX_CONNECTIONS):
            continue
        for endpoint in endpoints:
            info = official_endpoint(endpoint.get('url') if isinstance(endpoint, dict) else endpoint)
            if not info:
                continue
            provider, name, base_url, model = info
            identity = (provider, key)
            if identity in seen:
                continue
            seen.add(identity)
            # No secret-derived identifiers exposed through the public API.
            id = f'api-import-{index}-{provider}'
            config = ModelConfig(name=f'{name} · api.json {index + 1}', provider=provider,
                                 base_url=base_url, model=model, api_key=key).model_dump(mode='json')
            config['base_url'] = base_url
            connections.append((id, config))
            if len(connections) >= MAX_CONNECTIONS:
                return connections
    return connections


def import_api_config(store, root):
    configured = os.getenv('AI_API_CONFIG')
    if configured == '':
        return []
    try:
        path = Path(configured) if configured is not None else Path('api.json')
        if not path.is_absolute():
            path = Path(root) / path
        with path.open('rb') as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError()
        connections = parse_connections(json.loads(raw.decode('utf-8-sig')))
    except FileNotFoundError:
        return []
    except (OSError, ValueError, RecursionError):
        logger.warning('API 配置未导入：文件不可读或格式无效。')
        return []
    imported = []
    for id, config in connections:
        with store.lock:
            current = store.get('models', id)
            if current is None:
                current = store.put('models', id, config)
            # Never send a bootstrap key to a manually changed destination.
            if (current.get('enabled') and current.get('provider') == config['provider'] and
                    current.get('base_url') == config['base_url'] and current.get('api_key') == config['api_key']):
                imported.append(current)
    return imported


async def discover_imported_models(store, configs):
    semaphore = asyncio.Semaphore(3)

    async def fetch(config):
        async with semaphore:
            try:
                current = store.get('models', config['id'])
                if current != config or cached_catalog(store, config):
                    return
                await asyncio.wait_for(discover_models(store, config['id'], config), timeout=25)
            except Exception:
                # Provider errors may contain credentials; no exception interpolation.
                logger.warning('API 配置模型发现失败；可在控制台重试。')

    await asyncio.gather(*(fetch(config) for config in configs))
