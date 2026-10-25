"""Local authorization boundary; no filesystem paths are accepted as roots remotely."""
import re
import stat
from contextlib import closing
from pathlib import Path, PureWindowsPath
from .safe_fs import open_checked,root_identity


class FileAccess:
    def __init__(self, roots, identities=None):
        if not isinstance(roots, dict) or not roots:
            raise ValueError('必须在本地明确授权至少一个目录')
        self.roots = {}
        self.identities = {}
        for alias, value in roots.items():
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', alias):
                raise ValueError('目录别名仅支持字母、数字、下划线、连字符')
            root = Path(value).expanduser().resolve(strict=True)
            if not root.is_dir():
                raise ValueError('授权路径必须是已存在的目录')
            self.roots[alias] = root
            identity=root_identity(root)
            if identities is not None and tuple(identities.get(alias,()))!=identity:
                raise ValueError('授权根目录发生变化，请重新授权')
            self.identities[alias]=identity

    def resolve(self, root_id, path=''):
        if root_id not in self.roots or not isinstance(path, str) or len(path)>2048:
            raise ValueError('目录未授权或路径无效')
        parts = path.replace('\\', '/').split('/')
        if PureWindowsPath(path).drive or path.startswith(('/', '\\')) or any(
                part == '..' or ':' in part or '\x00' in part or part.endswith((' ', '.'))
                for part in parts if part not in ('', '.')) or '..' in parts:
            raise ValueError('路径必须是授权目录内的相对路径')
        root = self.roots[root_id]
        target = root
        for part in parts:
            if part in ('', '.'):
                continue
            target = target / part
            info = target.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('不允许跟随符号链接或目录联接')
        resolved = target.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError('路径超出授权目录')
        return resolved

    @staticmethod
    def integer(value, low, high):
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'参数必须是 {low}–{high} 的整数')
        return value

    def call(self, method, arguments):
        if method == 'list_roots':
            return {'roots': [{'id': alias, 'access': 'read-only'} for alias in self.roots]}
        if method not in ('list_directory', 'file_info', 'read_text'):
            raise ValueError('不支持的只读操作')
        root_id = arguments.get('root_id')
        path = arguments.get('path', '')
        self.resolve(root_id, path)
        relative='/'.join(part for part in path.replace('\\','/').split('/') if part not in ('','.'))
        with open_checked(self.roots[root_id],relative,self.identities[root_id]) as target:
            return self._call_opened(method,arguments,path,target)

    def _call_opened(self,method,arguments,path,target):
        if method == 'list_directory':
            offset = self.integer(arguments.get('offset', 0), 0, 1_000_000)
            limit = self.integer(arguments.get('limit', 100), 1, 100)
            entries, index, truncated = [], 0, False
            # Stream directory entries instead of loading an unbounded directory.
            with closing(target.entries()) as stream:
                for entry in stream:
                    if index < offset:
                        index += 1
                        continue
                    if len(entries) == limit:
                        truncated = True
                        break
                    entries.append(entry)
                    index += 1
            return {'entries': entries, 'next_offset': index if truncated else None, 'truncated': truncated,
                    'note': '目录变化可能影响分页顺序'}
        if method == 'file_info':
            return {'path':path,**target.info()}
        offset = self.integer(arguments.get('offset', 0), 0, 2**53)
        maximum = self.integer(arguments.get('max_bytes', 16384), 1, 16384)
        data,truncated=target.read(offset,maximum)
        if b'\x00' in data:
            raise ValueError('文件不是 UTF-8 文本；不支持二进制读取')
        # Byte pagination can split a UTF-8 code point; make that visible.
        text=data.decode('utf-8', errors='replace')
        return {'text': text, 'offset': offset,
                'bytes_read': len(data), 'next_offset': offset+len(data) if truncated else None,
                'truncated': truncated, 'encoding': 'utf-8', 'decode_errors': '\ufffd' in text}
