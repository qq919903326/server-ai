import asyncio
import json
import socket
import threading
import time
import pytest
import uvicorn
from backend.store import Store
from backend.mcp_client import discover, ToolHub
from database_mcp.database import DatabaseConfig, prepare_query
from database_mcp.server import create_app


@pytest.mark.parametrize('engine',['mysql','postgres'])
def test_database_service_over_real_mcp_http(engine, tmp_path, monkeypatch):
    """Exercises the actual HTTP service and SDK; database I/O is a test double."""
    class FixtureDB:
        def list_tables(self): return {'columns':['table_name'],'rows':[['orders']]}
        def describe_table(self, table_name): return {'columns':['column_name'],'rows':[['id']]}
        def query(self, sql, max_rows):
            prepare_query(sql, engine, max_rows)
            return {'columns':['value'],'rows':[[42]],'row_count':1,'truncated':False}
    config = DatabaseConfig(engine=engine,host='db.example',database='app',user='reader',password='not-used',token='fixture-token')
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(config, FixtureDB()),log_level='error'))
    thread = threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True)
    thread.start()
    store = Store(tmp_path)
    monkeypatch.setenv('FIXTURE_MCP_TOKEN','fixture-token')
    store.put('mcp','db',{'name':engine,'transport':'http','url':f'http://127.0.0.1:{port}/mcp','token_env':'FIXTURE_MCP_TOKEN','enabled':True,'allowed_tools':['list_tables','describe_table','query']})
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        async def run():
            tools = await discover(store, 'db')
            assert {t['name'] for t in tools} == {'list_tables','describe_table','query'}
            async with ToolHub(store,['db']) as hub:
                aliases = {route[1]:alias for alias,route in hub.routes.items()}
                tables = json.loads(await hub.call(aliases['list_tables'],{}))
                assert 'orders' in json.dumps(tables)
                result = json.loads(await hub.call(aliases['query'],{'sql':'SELECT 42','max_rows':5}))
                assert '42' in json.dumps(result)
                with pytest.raises(ValueError,match='工具执行失败'):
                    await hub.call(aliases['query'],{'sql':'DELETE FROM users'})
        asyncio.run(run())
    finally:
        server.should_exit=True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()
