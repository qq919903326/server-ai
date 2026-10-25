"""Bounded EXE overlay. SHA-256 detects corruption; it is NOT a signature."""
import hashlib
import json
import math
import struct
from pathlib import Path
from urllib.parse import urlsplit

MAGIC = b'AIStudioBootstrap\x00v1\x00'
MAX_JSON = 16 * 1024
TRAILER_SIZE = 4 + 32 + len(MAGIC)


def validate(config):
    if not isinstance(config, dict) or set(config) != {'server', 'pairing_id', 'ticket', 'expires_at', 'version'}:
        raise ValueError('助手配对配置字段无效')
    if type(config['version']) is not int or config['version'] != 1:
        raise ValueError('助手配对版本不支持')
    if not all(isinstance(config[k],str) for k in ('server','pairing_id','ticket')):
        raise ValueError('助手配对字段类型无效')
    url = urlsplit(config['server'])
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
        raise ValueError('助手服务器必须是 HTTP(S) origin')
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', config['pairing_id']) or not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', config['ticket']):
        raise ValueError('助手配对凭证格式无效')
    if type(config['expires_at']) not in (int, float) or not math.isfinite(config['expires_at']):
        raise ValueError('助手配对时间无效')
    return config


def build_download(template, config):
    payload = json.dumps(validate(config), ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(payload) > MAX_JSON:
        raise ValueError('助手配对数据过大')
    return template + payload + struct.pack('>I', len(payload)) + hashlib.sha256(payload).digest() + MAGIC


def read_bootstrap(path):
    with Path(path).open('rb') as stream:
        stream.seek(0, 2)
        size = stream.tell()
        if size < TRAILER_SIZE:
            raise ValueError('EXE 未包含助手配对数据')
        stream.seek(-TRAILER_SIZE, 2)
        trailer = stream.read(TRAILER_SIZE)
        if trailer[36:] != MAGIC:
            raise ValueError('EXE 未包含助手配对数据')
        length = struct.unpack('>I', trailer[:4])[0]
        if not 0 < length <= MAX_JSON or length + TRAILER_SIZE > size:
            raise ValueError('助手配对数据长度无效')
        stream.seek(-(TRAILER_SIZE + length), 2)
        payload = stream.read(length)
    if hashlib.sha256(payload).digest() != trailer[4:36]:
        raise ValueError('助手配对数据校验失败')
    try:
        return validate(json.loads(payload))
    except (TypeError, KeyError, UnicodeError) as exc:
        raise ValueError('助手配对数据无效') from exc


def has_bootstrap(path):
    with Path(path).open('rb') as stream:
        stream.seek(0, 2)
        if stream.tell() < len(MAGIC):
            return False
        stream.seek(-len(MAGIC), 2)
        return stream.read() == MAGIC
