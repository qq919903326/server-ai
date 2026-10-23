import secrets
import anyio
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from starlette.responses import JSONResponse
from .database import Database


class TokenAuth:
    def __init__(self, app, token):
        self.app, self.expected = app, ('Bearer ' + token).encode()

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            supplied = dict(scope['headers']).get(b'authorization', b'')
            if not secrets.compare_digest(supplied, self.expected):
                response = JSONResponse({'detail':'MCP 访问令牌无效'}, status_code=401,
                    headers={'WWW-Authenticate':'Bearer'})
                return await response(scope, receive, send)
        return await self.app(scope, receive, send)


def create_app(config, database=None):
    db = database or Database(config)
    mcp = FastMCP(f'{config.engine}-database', host='0.0.0.0', stateless_http=True, json_response=True,
        instructions='数据库只读工具。先列出表并查看字段，再编写 SELECT 查询。结果可能截断，不能据此声称已返回全部记录。')
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    limiter = anyio.CapacityLimiter(8)

    @mcp.tool(annotations=annotations)
    async def list_tables() -> dict:
        """列出配置数据库或 schema 下可访问的表和视图，最多 500 条。"""
        return await anyio.to_thread.run_sync(db.list_tables, limiter=limiter)

    @mcp.tool(annotations=annotations)
    async def describe_table(table_name: str) -> dict:
        """查看表字段、数据类型、可空性、默认值和字段顺序。"""
        return await anyio.to_thread.run_sync(db.describe_table, table_name, limiter=limiter)

    @mcp.tool(annotations=annotations)
    async def query(sql: str, max_rows: int = 100) -> dict:
        """执行单条只读 SELECT，可含 CTE、JOIN 或聚合。max_rows 为 1–500，默认 100。禁止写入和多语句。"""
        return await anyio.to_thread.run_sync(db.query, sql, max_rows, limiter=limiter)

    @mcp.custom_route('/health', methods=['GET'])
    async def health(request):
        return JSONResponse({'status':'ok','engine':config.engine})

    return TokenAuth(mcp.streamable_http_app(), config.token)
