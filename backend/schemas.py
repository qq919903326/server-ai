import re
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, TypeAdapter, model_validator


def valid_id(value):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', value):
        raise ValueError('ID 仅支持 1–64 位字母、数字、下划线或连字符')
    return value


class ModelConfig(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    provider: Literal['deepseek', 'openai', 'mimo', 'custom'] = 'custom'
    base_url: HttpUrl
    model: str = Field(min_length=1, max_length=150)
    api_key: str = Field(default='', max_length=4096)
    key_env: str = ''
    clear_key: bool = False
    enabled: bool = True


class ModelDiscovery(BaseModel):
    provider: Literal['deepseek','openai','mimo','custom'] | None = None
    base_url: HttpUrl | None = None
    api_key: str = Field(default='',max_length=4096)
    key_env: str = ''
    clear_key: bool = False


class MCPConfig(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    transport: Literal['http'] = 'http'
    url: str = Field(default='', max_length=4096)
    token_env: str = ''
    enabled: bool = False
    allowed_tools: list[str] = Field(default_factory=list)
    service_type: Literal['generic','file_p2p','database','ssh','jenkins'] = 'generic'
    host: str = Field(default='', max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    database_engine: Literal['mysql','postgres'] = 'mysql'
    username: str = Field(default='', max_length=256)
    password: str = Field(default='', max_length=4096, repr=False)
    clear_password: bool = False
    device_id: str = Field(default='',max_length=64)
    project_path: str = Field(default='',max_length=2048)

    @model_validator(mode='after')
    def check_file_project(self):
        if self.service_type == 'database':
            if not self.username.strip():
                raise ValueError('请输入数据库账号')
            from .database_tools import database_config
            database_config(self.model_dump())
            if 'allowed_tools' not in self.model_fields_set:
                self.allowed_tools = ['list_tables', 'describe_table', 'query']
        elif self.service_type == 'jenkins':
            from .jenkins_tools import JENKINS_TOOLS, normalize_jenkins_url
            self.url = normalize_jenkins_url(self.url)
            self.username = self.username.strip()
            if not self.username or ':' in self.username or any(ord(c) < 32 for c in self.username):
                raise ValueError('请输入有效的 Jenkins 账号')
            if 'allowed_tools' not in self.model_fields_set:
                self.allowed_tools = JENKINS_TOOLS.copy()
        elif self.service_type == 'ssh':
            from ipaddress import ip_address
            self.host = self.host.strip()
            try:
                ip_address(self.host)
            except ValueError:
                if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?', self.host):
                    raise ValueError('请输入有效的 SSH IP 或主机名') from None
            self.username = self.username.strip()
            if not self.username or any(ord(c) < 32 for c in self.username):
                raise ValueError('请输入有效的 SSH 账号')
            self.url = ''
            if 'allowed_tools' not in self.model_fields_set:
                self.allowed_tools = ['exec', 'list_directory', 'read_file', 'write_file']
        else:
            self.url = str(TypeAdapter(HttpUrl).validate_python(self.url))
        if self.service_type=='file_p2p':
            from pathlib import PureWindowsPath,PurePosixPath
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',self.device_id):
                raise ValueError('请输入有效的本地设备 ID')
            path=self.project_path
            if not path.strip() or any(ord(c)<32 for c in path) or not (PureWindowsPath(path).is_absolute() or PurePosixPath(path).is_absolute()) or '..' in path.replace('\\','/').split('/'):
                raise ValueError('请输入需要读取的本地项目目录绝对路径')
        return self

class SkillConfig(BaseModel):
    name: str = ''
    description: str = ''
    content: str = Field(min_length=1, max_length=100_000)
    enabled: bool = True


class ChatMessage(BaseModel):
    role: Literal['user','assistant']
    content: str=Field(max_length=100_000)


class ChatRequest(BaseModel):
    model_id: str
    message: str = Field(min_length=1, max_length=100_000)
    session_id: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    history: list[ChatMessage] | None = Field(default=None,max_length=200)
    store_session: bool=False
    skill_ids: list[str] = Field(default_factory=list)
    mcp_ids: list[str] = Field(default_factory=list)
    max_rounds: int = Field(default=8, ge=1, le=20)

    @model_validator(mode='after')
    def check_history_size(self):
        if sum(len(m.content) for m in self.history or [])>1_000_000:
            raise ValueError('会话上下文过长，请新建会话')
        return self


class NodePosition(BaseModel):
    x: float = Field(ge=-100000, le=100000, allow_inf_nan=False)
    y: float = Field(ge=-100000, le=100000, allow_inf_nan=False)


class Step(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    type: Literal['prompt', 'template', 'condition', 'tool'] = 'prompt'
    label: str = Field(default='', max_length=100)
    position: NodePosition | None = None
    prompt: str = ''
    model_id: str = ''
    skill_ids: list[str] = Field(default_factory=list)
    mcp_ids: list[str] = Field(default_factory=list)
    server_id: str = ''
    tool_name: str = ''
    arguments: dict = Field(default_factory=dict)
    contains: str = ''
    next: str | None = None
    otherwise: str | None = None
    retries: int = Field(default=0, ge=0, le=3)


class WorkflowConfig(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ''
    entry_id: str | None = None
    steps: list[Step] = Field(min_length=1, max_length=40)

    @model_validator(mode='after')
    def check_graph(self):
        nodes = {s.id: s for s in self.steps}
        if len(nodes) != len(self.steps):
            raise ValueError('步骤 ID 不可重复')
        if self.entry_id is not None and self.entry_id not in nodes:
            raise ValueError('开始节点不存在')
        active, visited = set(), set()
        def visit(id):
            if id not in nodes:
                raise ValueError('流程引用了不存在的步骤：' + id)
            if id in active:
                raise ValueError('流程不允许循环')
            if id in visited:
                return
            active.add(id)
            step = nodes[id]
            for target in [step.next, step.otherwise]:
                if target:
                    visit(target)
            active.remove(id)
            visited.add(id)
        for id in nodes:
            visit(id)
        return self


class WorkflowRun(BaseModel):
    input: str = Field(default='', max_length=100_000)
    model_id: str = ''
