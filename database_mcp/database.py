import base64
import json
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID
import sqlglot
from sqlglot import exp

MAX_ROWS = 500
MAX_BYTES = 512_000


@dataclass(frozen=True)
class DatabaseConfig:
    engine: str
    host: str
    database: str
    user: str
    password: str = field(repr=False)
    token: str = field(repr=False)
    port: int = 0
    schema: str = 'public'
    timeout_ms: int = 15_000
    sslmode: str = 'require'
    ssl_ca: str = ''

    def __post_init__(self):
        if self.engine not in ('mysql', 'postgres'):
            raise ValueError('engine 必须为 mysql 或 postgres')
        if not self.token.strip():
            raise ValueError('必须配置独立的 MCP 访问令牌')
        if not 100 <= self.timeout_ms <= 30_000:
            raise ValueError('查询超时必须在 100–30000 毫秒之间')
        if self.port and not 1 <= self.port <= 65535:
            raise ValueError('数据库端口无效')

    @classmethod
    def from_env(cls, engine):
        if engine not in ('mysql', 'postgres'):
            raise ValueError('engine 必须为 mysql 或 postgres')
        prefix = 'MYSQL' if engine == 'mysql' else 'POSTGRES'
        def required(key):
            name = prefix + '_' + key
            value = os.getenv(name, '')
            if not value.strip():
                raise ValueError('缺少环境变量：' + name)
            return value
        return cls(engine=engine, host=required('HOST'), database=required('DATABASE'),
            user=required('USER'), password=os.getenv(prefix + '_PASSWORD', ''), token=required('MCP_TOKEN'),
            port=int(os.getenv(prefix + '_PORT') or ('3306' if engine == 'mysql' else '5432')),
            schema=os.getenv('POSTGRES_SCHEMA', 'public'),
            timeout_ms=int(os.getenv(prefix + '_QUERY_TIMEOUT_MS', '15000')),
            sslmode=os.getenv('POSTGRES_SSLMODE', 'require'), ssl_ca=os.getenv(prefix + '_SSL_CA', ''))


def prepare_query(sql, engine, max_rows):
    if not isinstance(max_rows, int) or not 1 <= max_rows <= MAX_ROWS:
        raise ValueError('max_rows 必须在 1–500 之间')
    if not sql.strip() or len(sql) > 50_000:
        raise ValueError('SQL 不能为空，且不能超过 50000 字符')
    try:
        statements = [node for node in sqlglot.parse(sql, read=engine) if node is not None]
        if len(statements) != 1:
            raise ValueError('仅允许一条 SELECT 查询')
        query = statements[0]
        if not isinstance(query, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            raise ValueError('仅允许 SELECT 查询')
        denied = tuple(getattr(exp, name) for name in (
            'Insert', 'Update', 'Delete', 'Create', 'Drop', 'Alter', 'Command', 'Transaction',
            'Into', 'Lock', 'Copy', 'Merge', 'Grant', 'Revoke', 'Set', 'Execute', 'Hint') if hasattr(exp, name))
        for node in query.walk():
            if isinstance(node, denied):
                raise ValueError('查询包含写入、锁定或不支持的操作')
            if isinstance(node, exp.Func):
                name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
                if name in {'set_config', 'pg_sleep', 'pg_sleep_for', 'pg_sleep_until', 'pg_notify',
                    'load_file', 'sleep', 'benchmark', 'get_lock', 'release_lock', 'release_all_locks',
                    'sys_eval', 'sys_exec'} or name.startswith(('dblink', 'lo_', 'pg_advisory',
                    'pg_cancel', 'pg_terminate', 'pg_read', 'pg_write', 'pg_ls_', 'pg_log', 'pg_reload', 'pg_rotate')):
                    raise ValueError('不允许调用具有副作用或访问外部资源的函数')
        cap = max_rows + 1
        limit = query.args.get('limit')
        value = limit.expression if limit else None
        if not isinstance(value, exp.Literal) or not value.is_int or int(value.this) > cap:
            query = query.limit(cap)
        return query.sql(dialect=engine, comments=False)
    except ValueError:
        raise
    except Exception:
        raise ValueError('SQL 解析失败，请使用标准 SELECT 语句') from None


def json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        result = {'base64': base64.b64encode(data[:15000]).decode()}
        if len(data) > 15000:
            result['truncated'] = True
        return result
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        return value if len(value) <= 20000 else value[:20000] + '…[已截断]'
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)[:20000]


def format_result(columns, rows, max_rows):
    result, size = [], len(json.dumps(columns, ensure_ascii=False).encode())
    truncated = len(rows) > max_rows
    def clipped(value):
        if isinstance(value, str):
            return len(value) > 20000
        if isinstance(value, (bytes, bytearray, memoryview)):
            return len(value) > 15000
        if isinstance(value, dict):
            return any(clipped(v) for v in value.values())
        if isinstance(value, (list, tuple)):
            return any(clipped(v) for v in value)
        return False
    for row in rows[:max_rows]:
        truncated = truncated or clipped(row)
        values = [json_value(v) for v in row]
        size += len(json.dumps(values, ensure_ascii=False, allow_nan=False).encode())
        if size > MAX_BYTES:
            truncated = True
            break
        result.append(values)
    return {'columns': columns, 'rows': result, 'row_count': len(result), 'truncated': truncated}


class Database:
    def __init__(self, config):
        self.config = config

    def execute(self, sql, params=None, limit=100):
        cfg = self.config
        connection = None
        try:
            if cfg.engine == 'mysql':
                import pymysql
                kwargs = dict(host=cfg.host, port=cfg.port or 3306, user=cfg.user,
                    password=cfg.password, database=cfg.database, charset='utf8mb4',
                    autocommit=False, connect_timeout=10, read_timeout=20, write_timeout=10)
                if cfg.ssl_ca:
                    kwargs.update(ssl_ca=cfg.ssl_ca, ssl_verify_cert=True, ssl_verify_identity=True)
                connection = pymysql.connect(**kwargs)
            else:
                import psycopg
                kwargs = dict(host=cfg.host, port=cfg.port or 5432, user=cfg.user,
                    password=cfg.password, dbname=cfg.database, connect_timeout=10,
                    sslmode=cfg.sslmode, options=f'-c statement_timeout={cfg.timeout_ms} -c lock_timeout=2000 -c default_transaction_read_only=on')
                if cfg.ssl_ca:
                    kwargs['sslrootcert'] = cfg.ssl_ca
                connection = psycopg.connect(**kwargs)
                connection.read_only = True
            with connection.cursor() as cursor:
                if cfg.engine == 'mysql':
                    cursor.execute('SET SESSION MAX_EXECUTION_TIME = %s', (cfg.timeout_ms,))
                    cursor.execute('START TRANSACTION READ ONLY')
                cursor.execute(sql, params)
                columns = [item[0] for item in cursor.description or []]
                return format_result(columns, cursor.fetchmany(limit + 1), limit)
        except Exception:
            raise ValueError('数据库查询失败，请检查连接、只读账号权限、SQL 及查询超时；连接信息不会返回客户端。') from None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
                finally:
                    try:
                        connection.close()
                    except Exception:
                        pass

    def query(self, sql, max_rows=100):
        return self.execute(prepare_query(sql, self.config.engine, max_rows), limit=max_rows)

    def list_tables(self):
        schema = self.config.database if self.config.engine == 'mysql' else self.config.schema
        return self.execute('SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name LIMIT 501', (schema,), MAX_ROWS)

    def describe_table(self, table_name):
        if not table_name or len(table_name) > 256 or '\x00' in table_name:
            raise ValueError('表名必须为 1–256 个字符')
        schema = self.config.database if self.config.engine == 'mysql' else self.config.schema
        return self.execute('SELECT column_name, data_type, is_nullable, column_default, ordinal_position FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position LIMIT 501', (schema, table_name), MAX_ROWS)
