import re
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, model_validator


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


class MCPConfig(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    transport: Literal['http'] = 'http'
    url: HttpUrl
    token_env: str = ''
    enabled: bool = False
    allowed_tools: list[str] = Field(default_factory=list)

class SkillConfig(BaseModel):
    name: str = ''
    description: str = ''
    content: str = Field(min_length=1, max_length=100_000)
    enabled: bool = True


class ChatRequest(BaseModel):
    model_id: str
    message: str = Field(min_length=1, max_length=100_000)
    session_id: str | None = None
    skill_ids: list[str] = Field(default_factory=list)
    mcp_ids: list[str] = Field(default_factory=list)
    max_rounds: int = Field(default=8, ge=1, le=20)


class Step(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    type: Literal['prompt', 'template', 'condition', 'tool'] = 'prompt'
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
    steps: list[Step] = Field(min_length=1, max_length=40)

    @model_validator(mode='after')
    def check_graph(self):
        nodes = {s.id: s for s in self.steps}
        if len(nodes) != len(self.steps):
            raise ValueError('步骤 ID 不可重复')
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
