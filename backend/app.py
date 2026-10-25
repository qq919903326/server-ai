import asyncio
import json
import os
import secrets
import copy
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager
from contextlib import suppress
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from .store import Store, now
from .schemas import ModelConfig, ModelDiscovery, MCPConfig, SkillConfig, ChatRequest, WorkflowConfig, WorkflowRun, valid_id
from .model_catalog import cached_catalog,discover_models,merge_credentials
from .api_config import import_api_config, discover_imported_models
from .agent import completion, completion_stream, agent_events
from .tool_errors import ToolExecutionError
from .mcp_client import ToolHub, discover
from .runtime import model_config, skill_prompt, execute_workflow
from .projects import ProjectService
from .session_storage import enabled as session_storage_enabled,StorageSetting,ArchiveAcknowledgement

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
store = Store(os.getenv('AI_DATA_DIR', str(ROOT / 'data')))
locks = set()
projects_service=ProjectService(store)
store.projects_service=projects_service


def seed():
    # A one-time upgrade adds the new provider without resetting saved models
    # or restoring a preset that the administrator subsequently deletes.
    if not store.get('meta', 'mimo-preset-v1'):
        if not store.get('models', 'mimo'):
            store.put('models', 'mimo', {'name': '小米 MiMo', 'provider': 'mimo',
                'model': 'mimo-v2.6-flash', 'base_url': 'https://api.xiaomimimo.com/v1',
                'key_env': 'MIMO_API_KEY', 'api_key': '', 'enabled': True})
        store.put('meta', 'mimo-preset-v1', {})
    if store.get('meta', 'initialized'):
        return
    defaults = [
        ('deepseek', {'name': 'DeepSeek', 'provider': 'deepseek', 'model': 'deepseek-flash', 'base_url': 'https://api.deepseek.com', 'key_env': 'DEEPSEEK_API_KEY'}),
        ('openai', {'name': 'OpenAI · GPT', 'provider': 'openai', 'model': 'gpt-5.5', 'base_url': 'https://api.openai.com/v1', 'key_env': 'OPENAI_API_KEY'})]
    for id, cfg in defaults:
        if not store.get('models', id):
            store.put('models', id, {**cfg, 'api_key': '', 'enabled': True})
    if not store.get('meta', 'initialized'):
        store.put('skills', 'concise', {'name': '简明助手', 'description': '先给结论，再解释关键依据', 'content': '# 简明助手\n使用用户的语言回答。先给出结论，再给出必要的依据。代码应完整、可操作。遇到不确定的信息明确说明，不编造来源。', 'enabled': True})
        example = WorkflowConfig(name='内容整理与润色', description='两个模型步骤：先组织要点，再润色输出。', steps=[{'id': 'outline', 'type': 'prompt', 'prompt': '整理以下内容的主要观点：\n{{input}}', 'next': 'polish'}, {'id': 'polish', 'type': 'prompt', 'prompt': '将以下要点写成结构清晰、自然的中文短文：\n{{previous}}'}])
        store.put('workflows', 'writing', example.model_dump())
        store.put('meta', 'initialized', {})


@asynccontextmanager
async def lifespan(app):
    seed()
    imported = import_api_config(store, ROOT)
    catalog_task = asyncio.create_task(discover_imported_models(store, imported))
    # Interrupted jobs cannot be resumed automatically.
    for run in store.all('runs'):
        if run.get('kind')=='chat' and not session_storage_enabled(store):
            continue
        if run['status'] == 'running':
            store.put('runs', run['id'], {**run, 'status': 'cancelled', 'error': '服务重启，任务已中断', 'finished_at': now()})
    heartbeat=asyncio.create_task(projects_service.hub.heartbeat())
    try:yield
    finally:
        catalog_task.cancel()
        await asyncio.gather(catalog_task, return_exceptions=True)
        heartbeat.cancel()
        await asyncio.gather(heartbeat,return_exceptions=True)
        await projects_service.hub.close()


app = FastAPI(title='AI Studio API', version='0.1.0', lifespan=lifespan)
app.include_router(projects_service.router())


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Validation errors must not echo submitted passwords or API keys.
    errors = [{'loc': item['loc'], 'msg': item['msg'], 'type': item['type']} for item in exc.errors()]
    return JSONResponse({'detail': errors}, status_code=422)


@app.middleware('http')
async def protect(request: Request, call_next):
    if request.url.path.startswith('/api'):
        expected = os.getenv('APP_TOKEN', '')
        if request.url.path!='/api/health' and not expected and (
                request.url.hostname not in ('127.0.0.1','localhost','::1','testserver') or
                request.client.host not in ('127.0.0.1','::1','testclient')):
            return JSONResponse({'detail':'远程访问前，请在服务器配置 APP_TOKEN 并在控制台填写访问令牌。'},status_code=403)
        if expected and not secrets.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer ' + expected).encode()):
            return JSONResponse({'detail': '访问令牌无效，请在界面右上角设置。'}, status_code=401)
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/') and origin not in ('http://127.0.0.1:5173', 'http://localhost:5173'):
            return JSONResponse({'detail': '请求来源不允许'}, status_code=403)
    response=await call_next(request)
    if request.url.path.startswith('/api'):
        response.headers.setdefault('Cache-Control','no-store')
    return response


def require(kind, id):
    result = store.get(kind, id)
    if not result:
        raise HTTPException(404, '记录不存在')
    return result


def check_id(id):
    try:
        return valid_id(id)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def public_model(cfg):
    catalog=cached_catalog(store,cfg)
    return {k: v for k, v in cfg.items() if k != 'api_key'} | {
        'has_key': bool(cfg.get('api_key') or os.getenv(cfg.get('key_env') or '__unset__')),
        'available_models':catalog['models'] if catalog else [],
        'models_fetched_at':catalog['updated_at'] if catalog else None}


@app.get('/api/health')
def health():
    return {'status': 'ok', 'version': '0.1.0', 'auth_enabled': bool(os.getenv('APP_TOKEN'))}


@app.get('/api/models')
def models():
    return [public_model(m) for m in store.all('models')]


@app.put('/api/models/{id}')
def save_model(id: str, cfg: ModelConfig):
    check_id(id)
    old = store.get('models', id) or {}
    body = merge_credentials(old,cfg.model_dump(mode='json'))
    return public_model(store.put('models', id, body))


@app.post('/api/models/{id}/catalog')
async def model_catalog(id: str,body: ModelDiscovery):
    check_id(id)
    old=store.get('models',id) or {}
    updates=body.model_dump(mode='json',exclude_unset=True)
    if any(updates.get(k,'') is None for k in ('provider','base_url')):
        raise HTTPException(422,'服务商和地址不能为空')
    config=merge_credentials(old,updates)
    try:
        return await discover_models(store,id,config)
    except ValueError as exc:
        raise HTTPException(400,str(exc))


@app.post('/api/models/{id}/test')
async def test_model(id: str):
    try:
        cfg = model_config(store, id)
        result = await completion(cfg, [{'role': 'user', 'content': 'Reply with OK.'}], [])
        return {'ok': True, 'text': result.get('content', '')}
    except Exception as exc:
        raise HTTPException(400, str(exc) if isinstance(exc, ValueError) else '连接失败，请检查服务地址及网络。')


@app.get('/api/mcp')
def mcps():
    result=store.all('mcp')
    for item in result:
        if item.get('service_type')=='project':
            project=store.get('projects',item.get('project_id',''))
            if project:item['name']='文件库 · '+project['name']
    return [public_mcp(item) for item in result]


def public_mcp(cfg):
    return {k: v for k, v in cfg.items() if k not in ('password', 'clear_password')} | {
        'has_password': bool(cfg.get('password'))}


@app.put('/api/mcp/{id}')
def save_mcp(id: str, cfg: MCPConfig):
    check_id(id)
    body = cfg.model_dump(mode='json')
    old = store.get('mcp', id) or {}
    clear = body.pop('clear_password', False)
    if body['service_type'] in ('database', 'ssh', 'jenkins'):
        if not body['password'] and not clear:
            body['password'] = old.get('password', '')
    else:
        body.pop('password', None)
    return public_mcp(store.put('mcp', id, body))


@app.post('/api/mcp/{id}/test')
async def test_mcp(id: str):
    cfg = require('mcp', id)
    try:
        tools = await discover(store, id)
        return {'ok': True, 'tools': tools}
    except ToolExecutionError as error:
        raise HTTPException(400, str(error)) from None
    except Exception:
        if cfg.get('service_type') == 'jenkins':
            raise HTTPException(400, 'Jenkins 连接失败，请检查地址、端口、账号密码/API Token、网络、HTTPS 证书和账号读取权限。')
        if cfg.get('service_type') == 'ssh':
            raise HTTPException(400, 'SSH 连接失败，请检查 IP、端口、账号密码、主机密钥和服务器密码登录设置。')
        if cfg.get('service_type') == 'database':
            raise HTTPException(400, '数据库连接失败，请检查数据库 URL、账号密码、网络和访问权限。')
        raise HTTPException(400, 'MCP 连接失败，请检查远程服务地址和认证环境变量。')


@app.get('/api/skills')
def skills():
    return store.all('skills')


@app.put('/api/skills/{id}')
def save_skill(id: str, cfg: SkillConfig):
    check_id(id)
    body = cfg.model_dump()
    body['name'] = body['name'] or id
    folder = store.root / 'skills' / id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'SKILL.md').write_text(cfg.content, encoding='utf-8')
    return store.put('skills', id, body)


@app.get('/api/workflows')
def workflows():
    return store.all('workflows')


@app.put('/api/workflows/{id}')
def save_workflow(id: str, cfg: WorkflowConfig):
    check_id(id)
    return store.put('workflows', id, cfg.model_dump())


@app.post('/api/workflows/{id}/run')
async def run_workflow(id: str, body: WorkflowRun):
    workflow = require('workflows', id)
    return await execute_workflow(store, workflow, body)


@app.post('/api/workflows/{id}/stream')
async def stream_workflow(id: str, body: WorkflowRun, request: Request):
    workflow = require('workflows', id)
    async def events():
        queue = asyncio.Queue(maxsize=8)
        def publish(record):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(copy.deepcopy(record))
        def sse(event, data):
            return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
        task = asyncio.create_task(execute_workflow(store, workflow, body, on_update=publish))
        try:
            while not task.done() or not queue.empty():
                if await request.is_disconnected():
                    break
                try:
                    snapshot = await asyncio.wait_for(queue.get(), timeout=5)
                    yield sse('snapshot', snapshot)
                except asyncio.TimeoutError:
                    yield ': keepalive\n\n'
            if task.done():
                yield sse('done', task.result())
        finally:
            if not task.done():
                task.cancel()
            with suppress(asyncio.CancelledError):
                await task
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})


@app.delete('/api/{kind}/{id}')
def delete(kind: str, id: str):
    if kind not in ('models', 'mcp', 'skills', 'workflows', 'sessions', 'runs'):
        raise HTTPException(404)
    check_id(id)
    require(kind, id)
    if id in locks:
        raise HTTPException(409, '会话正在执行')
    store.delete(kind, id)
    if kind=='models':
        store.delete('catalogs',id)
        for catalog in store.all('catalogs'):
            if catalog.get('connection_id')==id:
                store.delete('catalogs',catalog['id'])
    return {'ok': True}


@app.get('/api/sessions')
def sessions():
    if not session_storage_enabled(store):return []
    return [{k: v for k, v in s.items() if k != 'messages'} for s in store.all('sessions')]


@app.get('/api/settings/session-storage')
def session_storage_setting():
    return {'enabled':session_storage_enabled(store)}


@app.put('/api/settings/session-storage')
def update_session_storage(body: StorageSetting):
    store.put('settings','session-storage',{'enabled':body.enabled})
    return {'enabled':body.enabled}


@app.get('/api/sessions/local-migration')
def export_local_history():
    if session_storage_enabled(store):return {'sessions':[],'runs':[]}
    return {'sessions':store.all('sessions')[:100],
            'runs':[r for r in store.all('runs') if r.get('kind')=='chat'][:100]}


@app.post('/api/sessions/local-migration')
def acknowledge_local_history(body: ArchiveAcknowledgement):
    if session_storage_enabled(store):raise HTTPException(409,'服务端存储已开启，请刷新后重试迁移')
    deleted=0
    # Re-check policy and versions under the store lock; do not remove updated rows.
    with store.lock:
        if session_storage_enabled(store):raise HTTPException(409,'会话存储设置已变化')
        for kind,items in [('sessions',body.sessions),('runs',body.runs)]:
            for item in items:
                current=store.get(kind,item.id)
                if current and current.get('updated_at')==item.updated_at and (kind!='runs' or current.get('kind')=='chat'):
                    store.delete(kind,item.id);deleted+=1
    return {'ok':True,'deleted':deleted}


@app.get('/api/sessions/{id}')
def session(id: str):
    if not session_storage_enabled(store):raise HTTPException(404,'会话仅保存在浏览器本地')
    return require('sessions', id)


@app.get('/api/runs')
def runs():
    return [r for r in store.all('runs') if r.get('kind')!='chat' or session_storage_enabled(store)][:100]


@app.post('/api/chat')
async def chat(body: ChatRequest, request: Request):
    sid = body.session_id or uuid4().hex
    persist_requested=body.store_session and session_storage_enabled(store)
    if sid in locks:
        raise HTTPException(409, '当前会话已有任务正在执行')
    locks.add(sid)
    session = (store.get('sessions',sid) if persist_requested else None) or {'title':body.message[:40],'messages':[]}
    prior=[m.model_dump() for m in body.history] if body.history is not None else session['messages']
    history = prior + [{'role': 'user', 'content': body.message}]
    session['messages'] = history
    def persist(kind,id,value):
        with store.lock:
            if persist_requested and session_storage_enabled(store):store.put(kind,id,value)
    persist('sessions',sid,session)
    rid = uuid4().hex
    record = {'kind': 'chat', 'name': session['title'], 'model_id': body.model_id, 'session_id': sid, 'status': 'running', 'started_at': now(), 'trace': []}
    persist('runs',rid,record)

    async def events():
        def sse(event, data):
            return f'event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'
        try:
            yield sse('session', {'session_id': sid, 'run_id': rid})
            cfg = model_config(store, body.model_id)
            messages = [{'role': 'system', 'content': skill_prompt(store, body.skill_ids)}, *history]
            async with asyncio.timeout(300):
                async with ToolHub(store, body.mcp_ids) as hub:
                    async def model(messages, tools):
                        return await completion(cfg, messages, tools)
                    def stream(messages, tools):
                        return completion_stream(cfg, messages, tools)
                    async for event, data in agent_events(messages, hub.tools, model, hub.call, body.max_rounds, stream, tool_timeout=hub.timeout_for):
                        if await request.is_disconnected():
                            raise asyncio.CancelledError()
                        if event == 'answer':
                            record['output'] = data['text']
                            session['messages'] = history + [{'role': 'assistant', 'content': data['text']}]
                            persist('sessions',sid,session)
                        elif event != 'delta':
                            record['trace'].append({'event': event, **data})
                        yield sse(event, data)
            record['status'] = 'completed'
        except asyncio.CancelledError:
            record.update(status='cancelled', error='任务已中断')
            raise
        except Exception as exc:
            error = str(exc) if isinstance(exc, ValueError) else '执行失败，请检查模型及 MCP 配置；请求超时请缩小任务。'
            record.update(status='failed', error=error)
            yield sse('error', {'message': error})
        finally:
            record['finished_at'] = now()
            persist('runs',rid,record)
            locks.discard(sid)
        yield sse('done', {'status':record['status'],'run':{**record,'id':rid},'stored_on_server':persist_requested and session_storage_enabled(store)})

    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})


dist = ROOT / 'frontend' / 'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
