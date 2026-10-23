import asyncio
import json
import os
import secrets
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from .store import Store, now
from .schemas import ModelConfig, MCPConfig, SkillConfig, ChatRequest, WorkflowConfig, WorkflowRun, valid_id
from .agent import completion, completion_stream, agent_events
from .mcp_client import ToolHub, discover
from .runtime import model_config, skill_prompt, execute_workflow

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env')
store = Store(os.getenv('AI_DATA_DIR', str(ROOT / 'data')))
locks = set()


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
    # Interrupted jobs cannot be resumed automatically.
    for run in store.all('runs'):
        if run['status'] == 'running':
            store.put('runs', run['id'], {**run, 'status': 'cancelled', 'error': '服务重启，任务已中断', 'finished_at': now()})
    yield


app = FastAPI(title='AI Studio API', version='0.1.0', lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    # Validation errors must not echo submitted passwords or API keys.
    errors = [{'loc': item['loc'], 'msg': item['msg'], 'type': item['type']} for item in exc.errors()]
    return JSONResponse({'detail': errors}, status_code=422)


@app.middleware('http')
async def protect(request: Request, call_next):
    if request.url.path.startswith('/api'):
        expected = os.getenv('APP_TOKEN', '')
        if expected and not secrets.compare_digest(request.headers.get('authorization', '').encode(), ('Bearer ' + expected).encode()):
            return JSONResponse({'detail': '访问令牌无效，请在界面右上角设置。'}, status_code=401)
        origin = request.headers.get('origin')
        if origin and origin.rstrip('/') != str(request.base_url).rstrip('/') and origin not in ('http://127.0.0.1:5173', 'http://localhost:5173'):
            return JSONResponse({'detail': '请求来源不允许'}, status_code=403)
    return await call_next(request)


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
    return {k: v for k, v in cfg.items() if k != 'api_key'} | {'has_key': bool(cfg.get('api_key') or os.getenv(cfg.get('key_env') or '__unset__'))}


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
    body = cfg.model_dump(mode='json', exclude={'clear_key'})
    body['api_key'] = '' if cfg.clear_key else cfg.api_key or old.get('api_key', '')
    return public_model(store.put('models', id, body))


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
    return store.all('mcp')


@app.put('/api/mcp/{id}')
def save_mcp(id: str, cfg: MCPConfig):
    check_id(id)
    return store.put('mcp', id, cfg.model_dump(mode='json'))


@app.post('/api/mcp/{id}/test')
async def test_mcp(id: str):
    require('mcp', id)
    try:
        tools = await discover(store, id)
        return {'ok': True, 'tools': tools}
    except Exception:
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


@app.delete('/api/{kind}/{id}')
def delete(kind: str, id: str):
    if kind not in ('models', 'mcp', 'skills', 'workflows', 'sessions', 'runs'):
        raise HTTPException(404)
    check_id(id)
    require(kind, id)
    if id in locks:
        raise HTTPException(409, '会话正在执行')
    store.delete(kind, id)
    return {'ok': True}


@app.get('/api/sessions')
def sessions():
    return [{k: v for k, v in s.items() if k != 'messages'} for s in store.all('sessions')]


@app.get('/api/sessions/{id}')
def session(id: str):
    return require('sessions', id)


@app.get('/api/runs')
def runs():
    return store.all('runs')[:100]


@app.post('/api/chat')
async def chat(body: ChatRequest, request: Request):
    sid = body.session_id or uuid4().hex
    if body.session_id:
        require('sessions', sid)
    if sid in locks:
        raise HTTPException(409, '当前会话已有任务正在执行')
    locks.add(sid)
    session = store.get('sessions', sid) or {'title': body.message[:40], 'messages': []}
    history = session['messages'] + [{'role': 'user', 'content': body.message}]
    session['messages'] = history
    store.put('sessions', sid, session)
    rid = uuid4().hex
    record = {'kind': 'chat', 'name': session['title'], 'model_id': body.model_id, 'session_id': sid, 'status': 'running', 'started_at': now(), 'trace': []}
    store.put('runs', rid, record)

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
                    async for event, data in agent_events(messages, hub.tools, model, hub.call, body.max_rounds, stream):
                        if await request.is_disconnected():
                            raise asyncio.CancelledError()
                        if event == 'answer':
                            record['output'] = data['text']
                            session['messages'] = history + [{'role': 'assistant', 'content': data['text']}]
                            store.put('sessions', sid, session)
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
            store.put('runs', rid, record)
            locks.discard(sid)
        yield sse('done', {'status': record['status']})

    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


dist = ROOT / 'frontend' / 'dist'
if dist.exists():
    app.mount('/', StaticFiles(directory=dist, html=True), name='frontend')
