import json
import os
import asyncio
from openai import AsyncOpenAI, APIError
from .tool_errors import ToolExecutionError


async def completion(config, messages, tools):
    key = config.get('api_key') or os.getenv(config.get('key_env') or '__unset__')
    if not key:
        raise ValueError('模型尚未配置 API Key，请在模型配置中保存密钥或设置环境变量。')
    try:
        async with AsyncOpenAI(api_key=key, base_url=config['base_url'], timeout=60, max_retries=1) as client:
            kwargs = {'model': config['model'], 'messages': messages}
            if tools:
                kwargs['tools'] = tools
            if config['provider'] in ('deepseek', 'mimo'):
                kwargs['extra_body'] = {'thinking': {'type': 'disabled'}}
            result = await client.chat.completions.create(**kwargs)
            return result.choices[0].message.model_dump(exclude_none=True)
    except APIError as exc:
        status = getattr(exc, 'status_code', None)
        raise ValueError(f'模型服务请求失败（HTTP {status or "网络异常"}），请检查密钥、模型名称和服务地址。') from None


async def completion_stream(config, messages, tools):
    key = config.get('api_key') or os.getenv(config.get('key_env') or '__unset__')
    if not key:
        raise ValueError('模型尚未配置 API Key，请在模型配置中保存密钥或设置环境变量。')
    content, calls = '', {}
    try:
        async with AsyncOpenAI(api_key=key, base_url=config['base_url'], timeout=60, max_retries=1) as client:
            kwargs = {'model': config['model'], 'messages': messages, 'stream': True}
            if tools:
                kwargs['tools'] = tools
            if config['provider'] in ('deepseek', 'mimo'):
                kwargs['extra_body'] = {'thinking': {'type': 'disabled'}}
            stream = await client.chat.completions.create(**kwargs)
            async with stream:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        content += delta.content
                        yield 'delta', {'text': delta.content}
                    for call in delta.tool_calls or []:
                        item = calls.setdefault(call.index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                        if call.id:
                            item['id'] = call.id
                        if call.function:
                            item['function']['name'] += call.function.name or ''
                            item['function']['arguments'] += call.function.arguments or ''
            message = {'role': 'assistant', 'content': content or None}
            if calls:
                message['tool_calls'] = [calls[index] for index in sorted(calls)]
            yield 'message', message
    except APIError as exc:
        status = getattr(exc, 'status_code', None)
        raise ValueError(f'模型服务请求失败（HTTP {status or "网络异常"}），请检查密钥、模型名称和服务地址。') from None


async def agent_events(messages, tools, model_call, tool_call, max_rounds=8, model_stream=None, tool_timeout=None):
    for round in range(max_rounds):
        yield 'status', {'text': f'正在思考 · 第 {round + 1} 轮'}
        if model_stream:
            response = None
            async for event, data in model_stream(messages, tools):
                if event == 'message':
                    response = data
                else:
                    yield event, data
            if response is None:
                raise ValueError('模型输出流未正常结束')
        else:
            response = await model_call(messages, tools)
        calls = response.get('tool_calls') or []
        if not calls:
            content = response.get('content') or ''
            if not content:
                raise ValueError('模型没有返回文本，请检查所选模型是否支持当前请求。')
            messages.append(response)
            yield 'answer', {'text': content}
            return
        if len(calls) > 16:
            raise ValueError('单轮工具调用数量超过 16 次限制')
        messages.append(response)
        for call in calls:
            name = call['function']['name']
            yield 'tool_start', {'name': name}
            try:
                args = json.loads(call['function']['arguments'])
                if not isinstance(args, dict):
                    raise ValueError('工具参数必须为对象')
                timeout = tool_timeout(name, args) if tool_timeout else 45
                output = await asyncio.wait_for(tool_call(name, args), timeout=timeout)
            except ToolExecutionError as error:
                output = json.dumps({'error': str(error), 'code': error.code}, ensure_ascii=False)
            except Exception:
                output = json.dumps({'error': '工具调用失败，请检查参数、工具权限及连接状态。'}, ensure_ascii=False)
            content = str(output)[:30_000]
            messages.append({'role': 'tool', 'tool_call_id': call['id'], 'content': content})
            yield 'tool_end', {'name': name, 'result': content}
    raise ValueError('已达到最大 Agent 轮数，请缩小任务范围或增加轮数。')
