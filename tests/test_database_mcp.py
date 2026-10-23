import json
from decimal import Decimal
from datetime import datetime
import pytest


def test_database_sql_rejects_writes_and_multiple_statements():
    from database_mcp.database import prepare_query
    for engine in ('mysql', 'postgres'):
        for sql in ('DELETE FROM users', 'SELECT 1; DROP TABLE users', 'COMMIT',
                    'WITH changed AS (DELETE FROM users RETURNING *) SELECT * FROM changed',
                    "SELECT * INTO copy_users FROM users", 'SELECT * FROM users FOR UPDATE'):
            with pytest.raises(ValueError):
                prepare_query(sql, engine, 100)


def test_database_query_caps_rows_and_removes_executable_comments():
    from database_mcp.database import prepare_query
    for engine in ('mysql', 'postgres'):
        assert 'LIMIT 101' in prepare_query('SELECT * FROM users', engine, 100).upper()
        assert 'LIMIT 10' in prepare_query('SELECT * FROM users LIMIT 10', engine, 100).upper()
        assert 'LIMIT 101' in prepare_query('SELECT * FROM users LIMIT 999999', engine, 100).upper()
    sql = prepare_query("SELECT 1 /*!50000 INTO OUTFILE '/tmp/x' */", 'mysql', 10)
    assert 'OUTFILE' not in sql.upper()


def test_database_rows_preserve_precision_and_duplicate_columns():
    from database_mcp.database import format_result
    result = format_result(['n','n','date','raw'], [(Decimal('1.234567890123456789'), 2, datetime(2026,1,1), b'abc'), (3,4,None,None)], 1)
    assert result['columns'] == ['n','n','date','raw']
    assert result['rows'][0][0] == '1.234567890123456789'
    assert result['rows'][0][2] == '2026-01-01T00:00:00'
    assert result['rows'][0][3] == {'base64':'YWJj'}
    assert result['truncated'] is True
    assert result['row_count'] == 1
    json.dumps(result, allow_nan=False)


def test_database_metadata_binds_table_name():
    from database_mcp.database import Database, DatabaseConfig
    class Capture(Database):
        def execute(self, sql, params=None, limit=100):
            return {'sql':sql,'params':params}
    for engine in ('mysql','postgres'):
        db = Capture(DatabaseConfig(engine=engine, host='db.example', database='app', user='reader', password='test', token='test-token'))
        result = db.describe_table("users' OR '1'='1")
        assert "users' OR '1'='1" not in result['sql']
        assert "users' OR '1'='1" in result['params']


def test_database_http_requires_own_token():
    from starlette.testclient import TestClient
    from database_mcp.server import create_app
    from database_mcp.database import DatabaseConfig
    config = DatabaseConfig(engine='mysql', host='db.example', database='app', user='reader', password='private-password', token='dedicated-token')
    with TestClient(create_app(config)) as client:
        assert client.post('/mcp', json={}).status_code == 401
        assert client.post('/mcp', headers={'Authorization':'Bearer wrong'}, json={}).status_code == 401
        response = client.get('/health',headers={'Authorization':'Bearer dedicated-token'})
        assert response.status_code == 200
        assert 'private-password' not in response.text


def test_database_requires_nonempty_service_token(monkeypatch):
    from database_mcp.database import DatabaseConfig
    monkeypatch.setenv('MYSQL_HOST','db.example')
    monkeypatch.setenv('MYSQL_DATABASE','app')
    monkeypatch.setenv('MYSQL_USER','reader')
    monkeypatch.delenv('MYSQL_MCP_TOKEN',raising=False)
    with pytest.raises(ValueError,match='MYSQL_MCP_TOKEN'):
        DatabaseConfig.from_env('mysql')


def test_database_marks_truncated_cells():
    from database_mcp.database import format_result
    result = format_result(['text'], [('x' * 25000,)], 100)
    assert result['truncated'] is True
    assert len(result['rows'][0][0]) < 21000


def test_database_cleanup_does_not_leak_driver_errors(monkeypatch):
    import pymysql
    from database_mcp.database import Database, DatabaseConfig
    class Connection:
        closed = False
        def cursor(self): raise RuntimeError('password=private-credential')
        def rollback(self): raise RuntimeError('password=private-credential')
        def close(self): self.closed = True
    conn = Connection()
    monkeypatch.setattr(pymysql,'connect',lambda **kwargs:conn)
    db = Database(DatabaseConfig(engine='mysql',host='example',database='app',user='reader',password='private-credential',token='token'))
    with pytest.raises(ValueError) as error:
        db.query('SELECT 1')
    assert 'private-credential' not in str(error.value)
    assert conn.closed is True


@pytest.mark.parametrize('engine',['mysql','postgres'])
def test_database_driver_uses_readonly_and_bounded_fetch(engine, monkeypatch):
    import psycopg
    import pymysql
    from database_mcp.database import Database, DatabaseConfig
    commands, fetches = [], []
    class Cursor:
        description = [('value',)]
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,params=None): commands.append((sql,params))
        def fetchmany(self,count):
            fetches.append(count)
            return [(1,)]
    class Connection:
        read_only = False
        rolled_back = False
        closed = False
        def cursor(self): return Cursor()
        def rollback(self): self.rolled_back=True
        def close(self): self.closed=True
    conn = Connection()
    monkeypatch.setattr(pymysql if engine=='mysql' else psycopg,'connect',lambda **kwargs:conn)
    db = Database(DatabaseConfig(engine=engine,host='example',database='app',user='reader',password='test',token='token'))
    assert db.query('SELECT 1', 5)['rows'] == [[1]]
    if engine == 'mysql':
        assert commands[1][0] == 'START TRANSACTION READ ONLY'
    else:
        assert conn.read_only is True
    assert fetches == [6]
    assert conn.rolled_back and conn.closed
