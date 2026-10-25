"""Keep all filesystem operations on verified handles, never reopen checked paths."""
import os
import stat
from contextlib import contextmanager, ExitStack
from pathlib import Path
from datetime import datetime, timezone


class PosixHandle:
    def __init__(self, fd):self.fd = fd

    def info(self):
        info = os.fstat(self.fd)
        return {'type':'directory' if stat.S_ISDIR(info.st_mode) else 'file',
                'size':info.st_size,'modified_at':datetime.fromtimestamp(info.st_mtime,timezone.utc).isoformat()}

    def entries(self):
        with os.scandir(self.fd) as stream:
            for entry in stream:
                info = entry.stat(follow_symlinks=False)
                link = stat.S_ISLNK(info.st_mode)
                yield {'name':entry.name,'type':'link' if link else 'directory' if stat.S_ISDIR(info.st_mode) else 'file','readable':not link}

    def read(self, offset, maximum):
        if not stat.S_ISREG(os.fstat(self.fd).st_mode):
            raise ValueError('只允许读取普通文本文件')
        os.lseek(self.fd,offset,os.SEEK_SET)
        data = os.read(self.fd,maximum+1)
        return data[:maximum],len(data)>maximum


class WindowsHandle:
    def __init__(self, handle):self.handle = handle

    def info(self):
        import win32file
        info = win32file.GetFileInformationByHandle(self.handle)
        return {'type':'directory' if info[0]&0x10 else 'file',
                'size':(info[5]<<32)+info[6],'modified_at':info[3].isoformat()}

    def entries(self):
        import win32file, pywintypes
        if self.info()['type']!='directory':raise ValueError('目标不是目录')
        while True:
            try:batch = win32file.GetFileInformationByHandleEx(self.handle,10)
            except pywintypes.error as exc:
                if exc.winerror==18:return  # ERROR_NO_MORE_FILES
                raise
            if not batch:return
            for item in batch:
                if item['FileName'] in ('.','..'):continue
                attrs = item['FileAttributes']
                blocked = bool(attrs&0x400)
                yield {'name':item['FileName'],'type':'link' if blocked else 'directory' if attrs&0x10 else 'file','readable':not blocked}

    def read(self, offset, maximum):
        import win32file, pywintypes
        if self.info()['type']!='file' or win32file.GetFileType(self.handle)!=1:
            raise ValueError('只允许读取普通文本文件')
        win32file.SetFilePointer(self.handle,offset,0)
        try:_, data = win32file.ReadFile(self.handle,maximum+1)
        except pywintypes.error as exc:
            if exc.winerror==38:return b'',False
            raise
        return data[:maximum],len(data)>maximum


def _windows_path(value):
    if value.startswith('\\\\?\\UNC\\'):value='\\\\'+value[8:]
    elif value.startswith('\\\\?\\'):value=value[4:]
    return os.path.normcase(os.path.normpath(value))


def _windows_identity(handle):
    import win32file
    info=win32file.GetFileInformationByHandle(handle)
    return info[4],(info[8]<<32)|info[9]


def root_identity(root):
    if os.name=='nt':
        import win32file
        handle=win32file.CreateFile(str(root),0x80000000,3,None,3,0x02200000,None)
        try:return _windows_identity(handle)
        finally:handle.Close()
    info=root.stat()
    return info.st_dev,info.st_ino


@contextmanager
def open_checked(root, relative, identity):
    """Reject links at open time and keep all ancestor handles until completion."""
    parts = Path(relative).parts if relative else ()
    with ExitStack() as stack:
        if os.name=='nt':
            import win32file
            # FILE_SHARE_DELETE deliberately omitted: pin ancestors while used.
            target = root
            handle = None
            for part in (None,*parts):
                if part is not None:target = target/part
                handle = win32file.CreateFile(str(target),0x80000000,3,None,3,0x02000000|0x00200000,None)
                stack.callback(handle.Close)
                attributes = win32file.GetFileInformationByHandle(handle)[0]
                if part is None and _windows_identity(handle)!=identity:
                    raise ValueError('授权根目录发生变化，请重新授权')
                if attributes&0x400:
                    raise ValueError('不允许跟随符号链接或目录联接')
                actual = _windows_path(win32file.GetFinalPathNameByHandle(handle,0))
                if actual!=_windows_path(str(target)):
                    raise ValueError('文件路径发生变化，已拒绝读取')
            yield WindowsHandle(handle)
        else:
            flags = os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK
            fd = os.open(root,flags|os.O_DIRECTORY)
            stack.callback(os.close,fd)
            info = os.fstat(fd)
            if (info.st_dev,info.st_ino)!=identity:
                raise ValueError('授权根目录发生变化，请重新授权')
            for index,part in enumerate(parts):
                fd = os.open(part,flags|(os.O_DIRECTORY if index<len(parts)-1 else 0),dir_fd=fd)
                stack.callback(os.close,fd)
            yield PosixHandle(fd)
