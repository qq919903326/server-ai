import json
from urllib.parse import parse_qs, unquote, urlsplit

import anyio
from mcp.types import CallToolResult, TextContent, Tool
from database_mcp.database import Database, DatabaseConfig


def database_config(cfg):
    engine = cfg['database_engine']
    url = cfg['url'].strip()
    try:
        parsed = urlsplit(url if '://' in url else f'{engine}://{url}')
        schemes = ('mysql',) if engine == 'mysql' else ('postgres', 'postgresql', 'pgsql')
        if parsed.scheme not in schemes or not parsed.hostname or parsed.fragment:
            raise ValueError()
        if parsed.username is not None or parsed.password is not None:
            raise ValueError()
        database = unquote(parsed.path.lstrip('/'))
        if not database or '/' in database or any(ord(c) < 32 for c in url):
            raise ValueError()
        options = parse_qs(parsed.query, keep_blank_values=True)
        if set(options) - {'sslmode', 'schema'} or any(len(v) != 1 for v in options.values()):
            raise ValueError()
        sslmode = options.get('sslmode', ['prefer'])[0]
        if sslmode not in ('disable', 'allow', 'prefer', 'require', 'verify-ca', 'verify-full'):
            raise ValueError()
        return DatabaseConfig(engine=engine, host=parsed.hostname,
            port=parsed.port or (3306 if engine == 'mysql' else 5432), database=database,
            user=cfg['username'], password=cfg.get('password', ''), token='internal-direct',
            schema=options.get('schema', ['public'])[0], sslmode=sslmode)
    except (ValueError, KeyError):
        raise ValueError('数据库 URL 无效，请填写 mysql://主机:3306/数据库名 或 postgresql://主机:5432/数据库名，账号密码请填写在独立字段中。') from None


class DatabaseSession:
    def __init__(self, cfg):
        self.database = Database(database_config(cfg))
        self.limiter = anyio.CapacityLimiter(8)

    async def tools(self):
        return [Tool(name=name, description=description, inputSchema={
            'type': 'object', 'properties': properties, 'required': required,
            'additionalProperties': False}) for name, description, properties, required in [
                ('list_tables', '列出数据库中的表和视图，最多 500 条。', {}, []),
                ('describe_table', '查看指定表的字段结构。',
                 {'table_name': {'type': 'string', 'minLength': 1, 'maxLength': 256}}, ['table_name']),
                ('query', '执行单条只读 SELECT 查询，结果可能截断。',
                 {'sql': {'type': 'string', 'minLength': 1, 'maxLength': 50000},
                  'max_rows': {'type': 'integer', 'minimum': 1, 'maximum': 500, 'default': 100}}, ['sql'])]]

    async def call_tool(self, name, args):
        if name not in ('list_tables', 'describe_table', 'query'):
            raise ValueError('未知数据库工具')
        from functools import partial
        result = await anyio.to_thread.run_sync(
            partial(getattr(self.database, name), **args), limiter=self.limiter)
        return CallToolResult(content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False))])

    async def test(self):
        await anyio.to_thread.run_sync(self.database.query, 'SELECT 1', limiter=self.limiter)
