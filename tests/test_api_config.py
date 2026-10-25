import asyncio
import importlib
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import api_config, model_catalog
from backend.store import Store


def write_config(path, entries=None):
    path.write_text(json.dumps(entries if entries is not None else [
        {'apiKey': 'fixture-secret', 'models': [
            {'url': 'https://api.deepseek.com/chat/completions'},
            {'url': 'https://api.deepseek.com/v1'},
            {'url': 'https://api.xiaomimimo.com/v1/chat/completions'},
            {'url': 'https://api.openai.com/v1/responses'}]}]), encoding='utf-8')


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.delenv('AI_API_CONFIG', raising=False)
    return Store(tmp_path / 'data')


def test_import_normalizes_deduplicates_and_preserves_manual_changes(tmp_path, local_store):
    write_config(tmp_path / 'api.json')
    manual = local_store.put('models', 'deepseek', {'api_key': 'manual-secret', 'name': 'manual'})
    imported = api_config.import_api_config(local_store, tmp_path)
    assert {c['provider']: c['base_url'] for c in imported} == {
        'deepseek': 'https://api.deepseek.com', 'mimo': 'https://api.xiaomimimo.com/v1',
        'openai': 'https://api.openai.com/v1'}
    assert all(c['api_key'] == 'fixture-secret' for c in imported)
    assert api_config.import_api_config(local_store, tmp_path) == imported
    assert len(local_store.all('models')) == 4
    assert local_store.get('models', 'deepseek') == manual
    changed = local_store.put('models', imported[0]['id'], {**imported[0], 'api_key': 'replacement'})
    api_config.import_api_config(local_store, tmp_path)
    assert local_store.get('models', changed['id']) == changed


def test_duplicate_entries_and_multiple_credentials(tmp_path, local_store):
    write_config(tmp_path / 'api.json', [
        {'apiKey': key, 'models': [{'url': 'https://api.openai.com/v1'}]}
        for key in ('first-fixture', 'first-fixture', 'second-fixture')])
    imported = api_config.import_api_config(local_store, tmp_path)
    assert [c['id'] for c in imported] == ['api-import-0-openai', 'api-import-2-openai']
    assert api_config.import_api_config(local_store, tmp_path) == imported


def test_disabled_does_not_open_file(tmp_path, local_store, monkeypatch):
    monkeypatch.setenv('AI_API_CONFIG', '')
    def forbidden(*args, **kwargs):
        pytest.fail('Disabled importer opened a file')
    monkeypatch.setattr(api_config.Path, 'open', forbidden)
    assert api_config.import_api_config(local_store, tmp_path) == []


def test_explicit_relative_and_absolute_paths(tmp_path, local_store, monkeypatch):
    path = tmp_path / 'fixture.json'
    write_config(path)
    for value in ('fixture.json', str(path)):
        monkeypatch.setenv('AI_API_CONFIG', value)
        assert len(api_config.import_api_config(local_store, tmp_path)) == 3


@pytest.mark.parametrize('url', [
    'http://api.openai.com/v1', 'https://127.0.0.1/v1', 'https://localhost/v1',
    'https://api.openai.com.evil.example/v1', 'https://api.openai.com@evil.example/v1',
    'https://fixture-secret@api.openai.com/v1', 'https://api.openai.com:444/v1',
    'https://api.openai.com/v1?key=fixture-secret', 'https://api.openai.com/v1#fixture-secret',
    'https://api.openai.com/v1?', 'https://api.openai.com/v1#',
    'https://api.openai.com/v1/../evil', 'https://api.openai.com/%76%31',
    'https://api.openai.com\\@evil.example/v1', 'https://api.openai.com./v1',
    ' https://api.openai.com/v1', 'https://api.openai.com/v1\n',
    'https://api.openai.com:bad/v1', '//api.openai.com/v1', None, {},
])
def test_rejects_untrusted_urls(url):
    assert api_config.parse_connections([{'apiKey': 'fixture-secret', 'models': [{'url': url}]}]) == []


@pytest.mark.parametrize('payload', [None, {}, [None], [{'apiKey': '', 'models': []}],
    [{'apiKey': 'bad\nkey', 'models': [{'url': 'https://api.openai.com/v1'}]}],
    [{'apiKey': ['fixture-secret'], 'models': []}], [{'apiKey': 'fixture-secret', 'models': {}}]])
def test_invalid_structures_are_ignored(payload):
    assert api_config.parse_connections(payload) == []


def test_file_failures_are_safe(tmp_path, local_store, caplog):
    assert api_config.import_api_config(local_store, tmp_path) == []
    path = tmp_path / 'api.json'
    for content in ('{"apiKey":"fixture-secret",broken', '\xff', '[' * 1100,
                    'fixture-secret' + 'x' * api_config.MAX_BYTES):
        path.write_bytes(content.encode('latin-1'))
        assert api_config.import_api_config(local_store, tmp_path) == []
    assert 'fixture-secret' not in caplog.text
    assert str(path) not in caplog.text


def test_discovery_only_gets_models_and_reuses_cache(tmp_path, local_store, monkeypatch):
    write_config(tmp_path / 'api.json')
    imported = api_config.import_api_config(local_store, tmp_path)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'data': [{'id': 'model-a'}, {'id': 'fixture-secret'}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(model_catalog.httpx, 'AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    asyncio.run(api_config.discover_imported_models(local_store, imported))
    asyncio.run(api_config.discover_imported_models(local_store, imported))
    assert len(requests) == 3
    assert all(r.method == 'GET' and r.url.path.endswith('/models') for r in requests)
    assert all(r.headers['Authorization'] == 'Bearer fixture-secret' for r in requests)
    assert all(model_catalog.cached_catalog(local_store, cfg)['models'] == ['model-a'] for cfg in imported)


@pytest.mark.parametrize('status', [302, 401, 500])
def test_provider_errors_and_redirects_never_leak_or_forward_key(tmp_path, local_store, monkeypatch, caplog, status):
    write_config(tmp_path / 'api.json')
    configs = api_config.import_api_config(local_store, tmp_path)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={'Location': 'https://evil.example/fixture-secret'},
                              json={'error': 'fixture-secret'})
    original = httpx.AsyncClient
    monkeypatch.setattr(model_catalog.httpx, 'AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    asyncio.run(api_config.discover_imported_models(local_store, configs))
    assert len(requests) == 3
    assert all(r.url.host in api_config.PROVIDERS for r in requests)
    assert local_store.all('catalogs') == []
    assert 'fixture-secret' not in caplog.text
    assert 'evil.example' not in caplog.text


def test_manually_changed_import_destination_is_not_discovered(tmp_path, local_store):
    write_config(tmp_path / 'api.json')
    configs = api_config.import_api_config(local_store, tmp_path)
    for config in configs:
        local_store.put('models', config['id'], {**config, 'base_url': 'https://evil.example'})
    assert api_config.import_api_config(local_store, tmp_path) == []


def test_failed_discovery_is_safe_and_concurrency_bounded(tmp_path, local_store, monkeypatch, caplog):
    configs = [{'id': str(i)} for i in range(8)]
    configs = [local_store.put('models', c['id'], c) for c in configs]
    active = peak = 0
    async def failing(*args):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(.01)
        active -= 1
        raise RuntimeError('fixture-secret')
    monkeypatch.setattr(api_config, 'discover_models', failing)
    asyncio.run(api_config.discover_imported_models(local_store, configs))
    assert peak == 3
    assert 'fixture-secret' not in caplog.text


def test_discovery_has_overall_timeout(local_store, monkeypatch):
    config = local_store.put('models', 'timeout', {})
    cancelled = []
    async def blocked(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    original = asyncio.wait_for
    def fast_timeout(awaitable, timeout):
        assert timeout == 25
        return original(awaitable, timeout=.01)
    monkeypatch.setattr(api_config, 'discover_models', blocked)
    monkeypatch.setattr(api_config.asyncio, 'wait_for', fast_timeout)
    asyncio.run(api_config.discover_imported_models(local_store, [config]))
    assert cancelled == [True]


def test_lifespan_imports_without_waiting_and_api_never_echoes_key(tmp_path, monkeypatch):
    monkeypatch.setattr('dotenv.load_dotenv', lambda *args, **kwargs: False)
    monkeypatch.setenv('AI_DATA_DIR', str(tmp_path / 'data'))
    path = tmp_path / 'fixture.json'
    write_config(path)
    monkeypatch.setenv('AI_API_CONFIG', str(path))
    monkeypatch.delenv('APP_TOKEN', raising=False)
    module = importlib.import_module('backend.app')
    importlib.reload(module)
    entered, cancelled = threading.Event(), threading.Event()
    async def blocked(*args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(api_config, 'discover_models', blocked)
    with TestClient(module.app) as client:
        assert entered.wait(2)
        response = client.get('/api/models')
        imported = [c for c in response.json() if c['id'].startswith('api-import-')]
        assert len(imported) == 3
        assert all(c['has_key'] and 'api_key' not in c for c in imported)
        assert 'fixture-secret' not in response.text
        saved = client.put('/api/models/' + imported[0]['id'], json=imported[0])
        assert saved.status_code == 200
        assert 'fixture-secret' not in saved.text
        assert client.get('/api/health').status_code == 200
    assert cancelled.wait(2)
