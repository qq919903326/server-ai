import os
from pydantic import BaseModel,Field


def enabled(store):
    setting=store.get('settings','session-storage')
    return bool(setting['enabled']) if setting else os.getenv('STORE_SESSIONS','false').lower() in ('true','1','yes')


class StorageSetting(BaseModel):
    enabled: bool=False


class ArchiveItem(BaseModel):
    id: str=Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    updated_at: str=Field(max_length=100)


class ArchiveAcknowledgement(BaseModel):
    sessions: list[ArchiveItem]=Field(default_factory=list,max_length=100)
    runs: list[ArchiveItem]=Field(default_factory=list,max_length=100)
