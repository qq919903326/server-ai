"""Credential-scoped, provider-reported model discovery. No inference calls."""
import hashlib
import json
import os
from urllib.parse import unquote
import httpx


def merge_credentials(old, updates):
    config={**old,**updates}
    same_source=all(str(config.get(k,'')).rstrip('/')==str(old.get(k,'')).rstrip('/') for k in ('provider','base_url'))
    config['api_key']='' if updates.get('clear_key') else updates.get('api_key') or (old.get('api_key','') if same_source else '')
    if not same_source and config.get('key_env')==old.get('key_env'):
        config['key_env']=''
    config.pop('clear_key',None)
    return config


def credentials(config):
    key=config.get('api_key') or os.getenv(config.get('key_env') or '__unset__')
    if not key:
        raise ValueError('请先填写 API Key 或有效的密钥环境变量；更换服务地址后需要重新提供密钥。')
    url=str(config.get('base_url','')).rstrip('/')
    if not url:
        raise ValueError('请填写 API 地址')
    return key,url


def fingerprint(config):
    key,url=credentials(config)
    return hashlib.sha256(json.dumps([config.get('provider'),url,key]).encode()).hexdigest()


def cached_catalog(store,config):
    try:
        return store.get('catalogs',config.get('id','')+':'+fingerprint(config))
    except ValueError:
        return None


async def discover_models(store,id,config):
    key,url=credentials(config)
    try:
        async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
            response=await client.get(url+'/models',headers={'Authorization':'Bearer '+key,'Accept':'application/json'})
            response.raise_for_status()
            payload=response.json()
        if not isinstance(payload,dict) or not isinstance(payload.get('data'),list):
            raise ValueError('模型列表响应格式不兼容，请手动填写模型 ID。')
        ids=sorted({m['id'] for m in payload['data'] if isinstance(m,dict) and isinstance(m.get('id'),str) and 0<len(m['id'])<=150 and key not in m['id'] and not any(ord(c)<32 for c in m['id'])})
        if len(ids)>2000:
            raise ValueError('模型列表过大，请使用更明确的服务地址。')
        stamp=fingerprint(config)
        saved=store.put('catalogs',id+':'+stamp,{'connection_id':id,'fingerprint':stamp,'models':ids})
        return {'models':ids,'fetched_at':saved['updated_at']}
    except httpx.HTTPStatusError as exc:
        status=exc.response.status_code
        explanation={401:'API Key 无效',403:'该 Key 没有读取模型列表的权限',404:'服务不支持模型列表接口',429:'请求过于频繁，请稍后重试'}.get(status,'服务请求失败')
        raise ValueError(f'获取模型列表失败（HTTP {status}）：{explanation}。可检查配置后重试或手动填写模型。') from None
    except (httpx.HTTPError,json.JSONDecodeError):
        raise ValueError('无法获取模型列表，请检查地址、网络及响应格式；也可以手动填写模型 ID。') from None


def resolve_model_config(store,id):
    connection,separator,variant=id.partition('::')
    config=store.get('models',connection)
    if not config or not config['enabled']:
        raise ValueError('模型不存在或未启用')
    if separator:
        name=unquote(variant)
        catalog=cached_catalog(store,config)
        if not catalog or name not in catalog['models']:
            raise ValueError('所选模型不在当前 Key 的已获取列表中，请刷新可用模型并重新选择。')
        config={**config,'model':name}
    return config
