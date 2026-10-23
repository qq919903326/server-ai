# AI Studio · Python AI 服务端

FastAPI + React / TypeScript + Ant Design 实现的单管理员 AI 控制台。直接调用 DeepSeek、GPT 或 OpenAI Chat Completions 兼容 API，无知识库，无本地模型和 GPU 依赖。

## 当前功能

- 对话：选择模型、加载 Skills、选择 MCP；SSE 实时逐段输出、停止生成、会话历史。
- 模型：配置服务地址、模型名称、API Key / 环境变量；测试连接；接口不返回密钥明文。
- Agent：工具调用循环、JSON Schema 参数校验、单轮 16 次工具调用上限、最多 20 轮、聊天总超时 300 秒。
- MCP：手动配置远程 Streamable HTTP 服务，工具发现、逐工具白名单、启停；不加载本机或开发环境的 MCP。
- Skills：Markdown 编辑、SKILL.md 导入、启停、对话手动选择；保存到数据库和 data/skills/<id>/SKILL.md。
- Workflow：JSON 配置，支持模型、模板、条件、MCP 工具步骤、每步 0–3 次重试；检查未知步骤和循环。
- SQLite 保存配置、会话及运行记录；查看执行结果与步骤详情；重启标记未完成任务为中断。

## Windows 启动

要求 Python 3.11+、Node.js 20.19+。

首次安装：

```powershell
cd E:\服务端AI
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

启动：双击 `start.cmd`，或执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

打开 http://127.0.0.1:8000 。按 Ctrl+C 停止服务。后端默认一个 worker；同会话并发控制使用进程内锁，请勿直接增加 workers。

## 配置模型

进入「模型连接」→ 编辑 → 填入自己的 API Key、账号可用的模型名称 → 保存 → 测试连接。

也可以复制 `.env.example` 为 `.env`，填入 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` 后重启。

小米 MiMo 已提供预设：`https://api.xiaomimimo.com/v1`，模型 `mimo-v2.6-flash`，密钥环境变量 `MIMO_API_KEY`。也可直接在界面保存密钥，并根据账号权限修改模型名称。MiMo 默认关闭 thinking，以匹配当前 Agent 工具调用的历史消息格式。接口参考 [MiMo 官方文档](https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call)。升级仅首次添加预设，不覆盖已有配置；删除后不会在重启时恢复。

预置模型名称为示例，可根据账号访问权限修改。自定义服务必须兼容 `/chat/completions`，需要工具调用时模型还需支持 function tools。厂商参数并非完全一致；DeepSeek 适配器禁用 thinking，以避免跨轮推理字段差异。GPT 特有的 Responses 内置工具暂未接入。

API Key 在本地 SQLite 中保存明文，但不会通过查询接口返回；推荐通过服务器环境变量管理密钥，并限制 data 目录访问权限。界面中密钥留空表示保留，勾选清除才删除本地密钥；环境变量仍然有效。

## MCP 使用

1. 「MCP 工具」中点击「连接 MySQL」「连接 PostgreSQL」使用数据库模板，或新建其他连接；填写远程 MCP 的 HTTP URL。
2. 点击「测试与发现工具」查看工具原始 `name`。
3. 编辑连接，将允许执行的工具名称加入白名单并启用。
4. 在对话右侧选择这个服务，再提出任务。

默认 MCP 列表为空。仅连接管理员明确配置的远程 HTTP 服务，不支持 stdio、本地命令执行，也不读取当前开发环境提供的 tools 或 MCP 配置。

数据库模板会预填工具白名单与 Token 环境变量名，并默认启用；需要填入已部署服务的真实 URL 后保存。聊天右侧「配置数据库 MCP」可直接进入管理页；下拉框会标注未启用或未授权工具的连接，这些连接需完成配置后才能选中。

HTTP 认证使用 `token_env` 引用环境变量，不在配置里写 Token 明文。支持分页发现工具（最多 20 页）；不支持 OAuth 登录交互、旧式 SSE transport。

## 数据库 MCP 服务

项目提供可单独部署的 MySQL 和 PostgreSQL 远程 HTTP MCP：支持表列表、字段结构和只读 SQL 查询，分别使用独立 Token、连接凭据和端口。部署及接入步骤见 [数据库 MCP 说明](database_mcp/README.md)。默认不会启动或注册任何数据库连接。

## Skills 范围

导入 Markdown 技能说明后，在对话中选择技能，后端将完整内容加载到系统上下文。此版本不自动选择技能，不自动执行任意脚本；需要执行能力时，通过 MCP 工具提供。修改磁盘 SKILL.md 不会自动同步数据库，请通过界面编辑或重新导入。数据库为运行时来源，SKILL.md 为可编辑导出副本。

## Workflow 配置

从数组第一步开始执行；每个步骤使用唯一 ID。`next` 连接下一步，不填写时结束。条件分支通过 `otherwise` 指定不匹配路径。

```json
[
  {"id":"classify","type":"condition","contains":"技术","next":"answer","otherwise":"fallback"},
  {"id":"answer","type":"prompt","prompt":"请分析：{{input}}","skill_ids":["concise"],"retries":1},
  {"id":"fallback","type":"template","prompt":"收到任务：{{input}}"}
]
```

支持的模板变量：`{{input}}` 原始输入、`{{previous}}` 上一步输出、`{{steps.步骤ID}}` 已完成步骤输出。

步骤类型：

| type | 配置 | 行为 |
| --- | --- | --- |
| prompt | prompt、model_id、skill_ids、mcp_ids | 模型 / Agent 执行；model_id 留空采用运行时所选模型 |
| template | prompt | 模板替换，不调用模型 |
| condition | contains、next、otherwise | 根据上一步文本是否包含关键词分支，保持原输出 |
| tool | server_id、tool_name、arguments | 调用白名单中的 MCP 工具，参数中字符串支持模板变量 |

`retries` 为额外重试次数，默认 0。具有写入副作用的工具请保持 0，避免重复操作。流程总超时 600 秒，最多 40 个步骤；当前为请求内同步运行，无任务队列、断点恢复、拖拽编辑或并行节点。

## API 与开发

接口文档：http://127.0.0.1:8000/docs 。配置 `APP_TOKEN` 后 `/api/*` 要求 `Authorization: Bearer <token>`；在界面右上角填写访问令牌。API 文档 Authorize 未配置，可用 curl 添加请求头。

开发时两个终端分别运行：

```powershell
.\.venv\Scripts\python -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000
```

```powershell
cd frontend
npm run dev
```

前端访问 http://127.0.0.1:5173 ，Vite 代理 API 到 8000。构建后访问 8000 即可使用同一服务提供的界面。

验证：

```powershell
.\.venv\Scripts\python -m pytest -q
cd frontend
npm run build
npx playwright test
```

浏览器测试需要已启动本地 8000 服务及 Microsoft Edge。测试不调用付费模型，测试数据使用独立 ID 并清理。Python 集成测试通过临时 HTTP 协议模拟服务验证远程 MCP 调用，不访问开发环境的 MCP。

## Linux / Docker 部署

```bash
docker build -t ai-studio .
docker run -d --name ai-studio -p 127.0.0.1:8000:8000 --env-file .env -v ai-studio-data:/app/data ai-studio
```

Docker 配置已提供，本次未在 Docker 环境验证。远程使用时设置强 APP_TOKEN，并通过 HTTPS 反向代理访问；这是单管理员控制台，拥有模型密钥及远程 MCP 连接的管理权限，不作为多租户公共聊天入口。反向代理需要关闭 SSE 缓冲。

备份 `data/` 与 `.env`。

## 目录

```text
backend/           API、模型适配、MCP、Agent、Workflow 和 SQLite
frontend/          React 控制台
tests/             Python 接口和集成测试
data/              运行时数据（自动创建，不提交）
setup.ps1          安装依赖并构建前端
start.ps1          启动服务
start.cmd          Windows 双击入口
```
