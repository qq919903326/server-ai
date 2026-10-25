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
                if cfg.get('service_type')=='project':
                    from .project_tools import ProjectSession
                    session=ProjectSession(self.store.projects_service,cfg['project_id'])
                    available=await session.tools()
                elif cfg.get('service_type') == 'database':
                    from .database_tools import DatabaseSession
                    session = DatabaseSession(cfg)
                    available = await session.tools()
                elif cfg.get('service_type') == 'jenkins':
                    from .jenkins_tools import JenkinsSession
                    session = JenkinsSession(cfg)
                    available = await session.tools()
                elif cfg.get('service_type') == 'ssh':
                    from .ssh_tools import SSHSession
                    session = SSHSession(cfg, self.store.root)
                    available = await session.tools()
                else:
                    session = await connect(self.stack, cfg)
                    available=await list_tools(session)
                for tool in available:
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

    def timeout_for(self, alias, args):
        route = self.routes.get(alias)
        if route:
            from .ssh_tools import SSHSession
            if isinstance(route[0], SSHSession):
                seconds = args.get('timeout', 60) if route[1] == 'exec' else 30
                return min(300, max(1, seconds)) + 60 if isinstance(seconds, int) else 90
        return 45


async def discover(store, id):
    cfg = store.get('mcp', id)
    if not cfg:
        raise ValueError('找不到 MCP 配置')
    if cfg.get('service_type')=='project':
        from .project_tools import ProjectSession
        return [tool.model_dump() for tool in await ProjectSession(store.projects_service,cfg['project_id']).tools()]
    if cfg.get('service_type') == 'database':
        from .database_tools import DatabaseSession
        session = DatabaseSession(cfg)
        await session.test()
        return [tool.model_dump() for tool in await session.tools()]
    if cfg.get('service_type') == 'jenkins':
        from .jenkins_tools import JenkinsSession
        session = JenkinsSession(cfg)
        await session.test()
        return [tool.model_dump() for tool in await session.tools()]
    if cfg.get('service_type') == 'ssh':
        from .ssh_tools import SSHSession
        session = SSHSession(cfg, store.root)
        await session.test()
        return [tool.model_dump() for tool in await session.tools()]
    async with AsyncExitStack() as stack:
        session = await connect(stack, cfg)
        return [tool.model_dump() for tool in await list_tools(session)]
