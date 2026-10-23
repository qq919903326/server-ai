import os
import hashlib
from contextlib import AsyncExitStack
from datetime import timedelta
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from jsonschema import validate


async def connect(stack, cfg):
    if cfg.get('transport') != 'http':
        raise ValueError('仅支持手动配置的远程 HTTP MCP 服务')
    headers = {}
    if cfg.get('token_env'):
        token = os.getenv(cfg['token_env'])
        if not token:
            raise ValueError('缺少 MCP 认证环境变量')
        headers['Authorization'] = 'Bearer ' + token
    http_client = await stack.enter_async_context(httpx.AsyncClient(
        headers=headers, timeout=httpx.Timeout(30, connect=15), follow_redirects=True))
    transport = await stack.enter_async_context(streamable_http_client(
        cfg['url'], http_client=http_client))
    session = await stack.enter_async_context(ClientSession(
        *transport[:2], read_timeout_seconds=timedelta(seconds=30)))
    await session.initialize()
    return session


async def list_tools(session):
    tools, cursor = [], None
    for _ in range(20):
        result = await session.list_tools(cursor=cursor)
        tools.extend(result.tools)
        cursor = result.nextCursor
        if not cursor:
            return tools
    raise ValueError('远程 MCP 工具列表超过分页限制')


class ToolHub:
    def __init__(self, store, ids):
        self.store, self.ids = store, ids
        self.stack = AsyncExitStack()
        self.tools, self.routes = [], {}

    async def __aenter__(self):
        await self.stack.__aenter__()
        try:
            for id in dict.fromkeys(self.ids):
                cfg = self.store.get('mcp', id)
                if not cfg or not cfg['enabled']:
                    raise ValueError('MCP 不存在或未启用：' + id)
                session = await connect(self.stack, cfg)
                for tool in await list_tools(session):
                    if tool.name not in cfg.get('allowed_tools', []):
                        continue
                    alias = 'mcp_' + hashlib.sha256(f'{id}:{tool.name}'.encode()).hexdigest()[:24]
                    self.tools.append({'type': 'function', 'function': {
                        'name': alias,
                        'description': f'[{cfg["name"]}] {tool.name}: {tool.description or ""}'[:2000],
                        'parameters': tool.inputSchema}})
                    self.routes[alias] = (session, tool.name, tool.inputSchema, id)
            return self
        except BaseException:
            await self.stack.aclose()
            raise

    async def __aexit__(self, *args):
        return await self.stack.__aexit__(*args)

    async def call(self, alias, args):
        if alias not in self.routes:
            raise ValueError('工具未授权')
        session, name, schema, _ = self.routes[alias]
        validate(args, schema)
        result = await session.call_tool(name, args)
        if result.isError:
            raise ValueError('MCP 工具执行失败，请检查工具参数及服务状态。')
        return result.model_dump_json()


async def discover(store, id):
    cfg = store.get('mcp', id)
    if not cfg:
        raise ValueError('找不到 MCP 配置')
    async with AsyncExitStack() as stack:
        session = await connect(stack, cfg)
        return [tool.model_dump() for tool in await list_tools(session)]
