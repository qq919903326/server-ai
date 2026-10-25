# AI Studio · Python AI 服务端

FastAPI + React / TypeScript + Ant Design 实现的单管理员 AI 控制台。直接调用 DeepSeek、GPT 或 OpenAI Chat Completions 兼容 API，无知识库，无本地模型和 GPU 依赖。

## 当前功能

- 对话：选择模型、加载 Skills、选择 MCP；SSE 实时逐段输出、停止生成、会话历史。
- 模型：配置服务地址、模型名称、API Key / 环境变量；测试连接；接口不返回密钥明文。
- Agent：工具调用循环、JSON Schema 参数校验、单轮 16 次工具调用上限、最多 20 轮、聊天总超时 300 秒。
- MCP：Jenkins、SSH、数据库直连及远程 Streamable HTTP 服务，工具发现、逐工具白名单、启停；不加载本机或开发环境的 MCP。
- Skills：Markdown 编辑、SKILL.md 导入、启停、对话手动选择；保存到数据库和 data/skills/<id>/SKILL.md。
- Workflow：React Flow 图形编辑，支持拖动、连线、条件分支、节点参数及每步 0–3 次重试；运行时显示实际执行路径，保存节点布局和执行快照，兼容 JSON 配置。
- SQLite 保存配置和工作流运行记录；聊天会话与聊天运行记录默认仅保存到当前浏览器 IndexedDB，服务端不写入。可显式开启服务端会话副本。

## Windows 启动

要求 Python 3.11+、Node.js 20.19+。

首次安装：

```powershell
cd E:\server-ai
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

启动：双击 `start.cmd`，或执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

打开 http://127.0.0.1:8000 。按 Ctrl+C 停止服务。后端默认一个 worker；同会话并发控制使用进程内锁，请勿直接增加 workers。

## 配置模型

进入「模型连接」→ 编辑 → 填入自己的 API Key，离开输入框后自动获取当前 Key 的模型列表；也可点击「获取可用模型」刷新。选择默认模型并保存后，聊天及工作流的模型下拉框可选择此连接获取到的各个模型，仍支持手动填写模型 ID。

DeepSeek、MiMo、OpenAI 和兼容服务均通过所配置地址的 `/models` 接口读取列表，不逐个发起生成请求。列表按 Key、服务商和地址缓存，更换这些配置后旧列表失效；权限变化后请重新获取。厂商返回的列表可能包含语音、嵌入等模型，列出不代表支持当前聊天及工具调用。若服务不支持此接口，可手动填写模型 ID。

也可以复制 `.env.example` 为 `.env`，填入 `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` 后重启。

小米 MiMo 已提供预设：`https://api.xiaomimimo.com/v1`，模型 `mimo-v2.6-flash`，密钥环境变量 `MIMO_API_KEY`。也可直接在界面保存密钥，并根据账号权限修改模型名称。MiMo 默认关闭 thinking，以匹配当前 Agent 工具调用的历史消息格式。接口参考 [MiMo 官方文档](https://mimo.mi.com/docs/zh-CN/quick-start/summary/first-api-call)。升级仅首次添加预设，不覆盖已有配置；删除后不会在重启时恢复。

预置模型名称为示例，可根据账号访问权限修改。自定义服务必须兼容 `/chat/completions`，需要工具调用时模型还需支持 function tools。厂商参数并非完全一致；DeepSeek 适配器禁用 thinking，以避免跨轮推理字段差异。GPT 特有的 Responses 内置工具暂未接入。

API Key 在本地 SQLite 中保存明文，但不会通过查询接口返回；推荐通过服务器环境变量管理密钥，并限制 data 目录访问权限。界面中密钥留空表示保留，勾选清除才删除本地密钥；环境变量仍然有效。

## 会话存储与隐私

对话右侧提供「服务端存储会话」开关，默认关闭。浏览器始终将消息、工具过程和聊天运行记录保存到本地 IndexedDB；刷新可恢复，后续请求携带本地上下文，因此关闭服务端存储仍支持连续对话。清除网站数据或更换浏览器不会自动携带本地历史。

关闭时服务端仅在本次请求内存中处理消息，不将会话、消息摘要、模型输出或聊天工具结果写入 sessions/runs。调用模型和远程工具仍需要传输执行所需的内容。只有服务端开关开启且请求显式包含 `store_session: true` 才保存会话副本；`STORE_SESSIONS=false` 为环境默认值，界面保存的设置优先。

升级后、或关闭开关时，浏览器会将旧服务端会话及聊天运行记录迁入本地，确认 IndexedDB 事务提交后按版本确认删除服务端副本。冲突的旧版本另存为本地「服务端旧副本」，不会覆盖本地续写。浏览器存储失败或版本变化时不删除未确认的数据，并提示重试。此操作清除应用中的副本，不负责删除外部备份或代理日志。

工作流定义和工作流运行记录仍按原有功能保存到服务器，此开关针对聊天 Session。配置、模型 Key 与文件库授权元数据也不属于聊天 Session。

## MCP 使用

1. 数据库连接：在「MCP 工具」点击「连接 MySQL」或「连接 PostgreSQL」，填写数据库 URL、账号和密码并保存。URL 示例：`mysql://192.168.1.10:3306/app`、`postgresql://192.168.1.10:5432/app`，也支持 `主机:端口/数据库名`。数据库由 AI 服务器直接访问，无需部署 MCP 服务或配置 MCP 地址、Token。
2. 其他远程 MCP：点击「新建连接」，填写远程 MCP 的 HTTP URL。以下发现与白名单步骤适用于这类远程服务。
3. 远程 MCP 点击「测试与发现工具」查看工具原始 `name`，编辑连接，将允许执行的工具名称加入白名单并启用。
4. 在对话右侧选择数据库或 MCP 服务，再提出任务。

默认连接列表为空。支持显式配置的 Jenkins、SSH、数据库直连和远程 HTTP MCP，不支持 stdio、本机命令执行，也不读取当前开发环境提供的 tools 或 MCP 配置。

### Jenkins 连接与 Agent 操作

在「MCP 工具」点击「连接 Jenkins」，输入 Jenkins 地址（如 `192.168.1.10:8080` 或 `https://ci.example.com/jenkins`）、账号、密码并保存。省略协议时默认 HTTP；密码栏也支持 Jenkins 账号的 API Token。Jenkins 必须能从 AI 服务器访问，无需另外安装 Jenkins MCP 服务或插件。

保存后点击「测试与发现工具」验证真实登录身份和读取权限；在对话右侧的「MCP 工具服务」选择该 Jenkins，或在工作流中选择此连接。可直接向 Agent 提出：

- 「列出 Jenkins 的任务，查看 team/deploy 的构建参数。」
- 「使用 BRANCH=main 构建 team/deploy，查询排队状态和构建结果。」
- 「读取 deploy 第 42 次构建的日志并分析失败原因。」
- 「停止 deploy 第 42 次构建」或「禁用 nightly 任务」。
- 「读取现有任务配置，修改 Pipeline 后保存」或「根据 config.xml 新建任务」。

默认授权 12 个工具：`server_info`、`list_jobs`、`job_info`、`build_info`、`build_log`、`queue_info`、`trigger_build`、`stop_build`、`set_job_enabled`、`get_job_config`、`update_job_config`、`create_job`，可在编辑连接时修改白名单。所有操作使用所填账号的 Jenkins 权限；密码认证自动获取 CSRF crumb 并保持同一 Cookie 会话。触发构建返回排队编号后需继续查询队列获取构建编号，不能将「已排队」视为构建成功；写操作网络失败时先查询状态再决定是否重试。

密码保存在服务器 SQLite 配置中，查询接口不回传，编辑留空保留原密码。使用 HTTPS 时校验证书；服务器禁止密码方式访问 API 时，请在同一密码栏填写 API Token。

数据库连接默认启用并授权 `list_tables`、`describe_table`、`query` 三个只读工具。「测试与发现工具」会执行 `SELECT 1` 验证真实数据库连接。密码仅保存在服务器配置中，接口不回传；编辑时留空保持原密码。PostgreSQL 默认 schema 为 `public`、SSL 模式为 `prefer`，可通过 URL 的 `?schema=reports&sslmode=require` 设置。聊天右侧「配置数据库 MCP」可直接进入管理页。

HTTP 认证使用 `token_env` 引用环境变量，不在配置里写 Token 明文。支持分页发现工具（最多 20 页）；不支持 OAuth 登录交互、旧式 SSE transport。

## 数据库 MCP 服务

日常使用直接在界面填写数据库连接信息即可。项目也保留可单独部署的 MySQL 和 PostgreSQL 远程 HTTP MCP，供其他 MCP 客户端使用，部署步骤见 [数据库 MCP 说明](database_mcp/README.md)。默认不会注册任何数据库连接。

## SSH 服务器（Agent 操作）

在「MCP 工具」点击「连接 SSH」，填写 IP / 主机名、SSH 端口（默认 `22`）、账号和密码后保存。无需额外部署 MCP 服务或配置 MCP 地址。点击「测试与发现工具」会实际连接并验证 SSH 密码登录。

在对话右侧选中此 SSH 连接，Agent 即可按任务调用以下工具；工作流也可选择这些工具：

| 工具 | 功能 |
| --- | --- |
| `exec` | 执行远程 shell 命令，返回标准输出、错误输出、退出码和超时状态；可指定 `cwd` |
| `list_directory` | 通过 SFTP 分页查看目录、文件大小、权限和修改时间 |
| `read_file` | 分页读取远程 UTF-8 文本文件 |
| `write_file` | 写入远程 UTF-8 文本文件，覆盖已有内容 |

示例任务：「查看这台服务器的 CPU、内存和磁盘使用情况」「读取 /var/log/nginx/error.log 并分析错误」「修改 /srv/app/config.yaml 中的配置」。操作权限与填写的登录账号一致；远程命令每次在独立 shell 中运行，需要保留目录时应指定 `cwd` 或在同一次命令中执行 `cd`。

密码仅保存在服务端配置中，不回传到前端、不加入模型工具描述；编辑时留空保留原密码。连接时校验本机用户的 `~/.ssh/known_hosts` 和数据目录的 `ssh_known_hosts`；未知主机首次连接自动保存到数据目录，主机密钥变更会拒绝连接。遇到公钥不一致时，应先独立核对服务器当前公钥，再更新报错主机对应的旧记录。

SSH 连接测试和 Agent 工具结果会区分主机公钥不一致、密码认证失败、端口拒绝连接、网络超时、远程文件不存在和访问权限不足。可运行 `.venv/Scripts/python.exe tools/diagnose_ssh.py` 检查已保存连接的登录、只读命令和 SFTP 列目录，输出不包含密码。

命令默认超时 60 秒，可设置 1–300 秒；标准输出与错误输出合计最多保留 256 KB，超出会标记截断。超时会关闭 SSH 通道并标记远程进程可能仍在运行；超时不代表远程进程已终止。文件读取单次最多 200 KB，目录单页最多 200 项。

## 本地文件库（P2P 授权助手 / 浏览器授权）

**公网 IP + HTTP 推荐 Windows 授权助手：**在「本地文件库」点击「下载 Windows 授权助手」，双击下载的 `AIStudioAssistant.exe`，在助手中点击「选择文件夹并授权」。网页自动显示该目录的连接状态，可直接选用于对话和工作流。无需安装 Python、运行命令、填写地址、Token 或磁盘路径。

助手只允许访问本机明确选择的目录；文件留在本机，通过 WebRTC 按需读取，不上传整目录快照。关闭助手停止访问，在助手中移除目录立即撤销本地读取能力；网页也可移除单个文件库或撤销整个设备。配对信息绑定下载来源，首次需在下载后 10 分钟内启动；成功配对后可再次双击同一 EXE 恢复，设备凭据有效期 30 天。HTTP 下配对与信令传输不加密。支持直连；配置桌面 TURN 后，UDP 受限时自动通过同一服务端口中继，客户端无需配置网络参数。

**HTTPS / localhost 也可使用「浏览器直接授权」：**支持 File System Access 的浏览器可直接选择目录，句柄保存在当前浏览器，刷新后检查权限并恢复。请保持至少一个页面打开；权限失效时点击文件库的「重新授权」。两种模式均只提供列目录、文件信息、读取文本，不提供文件写入或执行。

服务器部署需配置 `APP_TOKEN`，公网 P2P 可能需要 `FILE_ICE_SERVERS_JSON`（STUN/TURN）。Windows 管理员首次构建助手：

```powershell
powershell -ExecutionPolicy Bypass -File .\build-assistant.ps1 -InstallBuildTools
```

生成的 `assistant_dist/AIStudioAssistant.exe` 是不含配对信息的模板，由下载接口自动附加一次性配对信息；请从网页下载后使用。Dockerfile 会一并打包此模板，也可将模板只读挂载并通过 `AI_ASSISTANT_EXE` 指定路径。未提供模板时下载接口明确返回 503。详见 [项目文件访问说明](file_mcp/README.md)。

## Skills 范围

导入 Markdown 技能说明后，在对话中选择技能，后端将完整内容加载到系统上下文。此版本不自动选择技能，不自动执行任意脚本；需要执行能力时，通过 MCP 工具提供。修改磁盘 SKILL.md 不会自动同步数据库，请通过界面编辑或重新导入。数据库为运行时来源，SKILL.md 为可编辑导出副本。

## Workflow 配置

点击「新建工作流」或已有流程的「图形编辑」打开画布：

1. 从左侧点击或拖入模型 / Agent、文本模板、条件分支、MCP 工具节点。
2. 拖动节点调整位置，从右侧输出端口拖到目标节点左侧输入端口。普通节点只有一个后续节点，条件节点有「匹配 / 不匹配」两个端口。
3. 点击节点，在右侧配置提示词、模型、Skills、远程 MCP 服务和工具参数；也可设为开始节点。
4. 点击连线可删除；循环连线会被拒绝。保存工作流后，节点位置与调用关系一并保存。
5. 「运行流程」打开实时执行图：蓝色执行中、绿色完成、红色失败，未执行分支灰显；点击节点查看输出与内部工具调用。

运行记录保留当时的图形快照，后续修改流程不会改变历史图。旧记录没有图形快照时仍以原来的结果详情展示。「JSON 配置」可导入或编辑完整流程对象。

默认从数组第一步开始执行，也可通过 `entry_id` 指定开始节点；每个步骤使用唯一 ID。`next` 连接下一步，不填写时结束。条件分支通过 `otherwise` 指定不匹配路径。节点可带 `label` 显示名称及 `position: {"x":0,"y":0}` 画布坐标。

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

`retries` 为额外重试次数，默认 0。具有写入副作用的工具请保持 0，避免重复操作。流程总超时 600 秒，最多 40 个步骤；按连线执行单条路径，不实现并行调度。图形界面通过 SSE 接收运行状态，可停止任务；当前没有持久任务队列及断点恢复，断开运行连接会取消任务。

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
npm test
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


### 访问令牌与 API 配置

所有业务 API（包括 localhost）必须携带 `Authorization: Bearer <APP_TOKEN>`。管理员需在 `.env` 设置非空 `APP_TOKEN`；未配置时业务 API 返回 503，未携带或错误令牌返回 401。网页打开后提示输入令牌，验证成功后仅保存到当前标签页 sessionStorage。`/api/health` 仅提供运行状态，不要求令牌。

服务启动时读取项目根目录的 `api.json`（可用 `AI_API_CONFIG` 指定路径，空字符串禁用）。支持现有配置格式：数组条目包含 `apiKey`，`models` 数组只需提供服务端点 `url`，不需要模型 ID。启动时通过服务商 `/models` 接口发现可用模型并缓存，不发起推理。支持 DeepSeek、MiMo、OpenAI 官方 HTTPS 地址；密钥不会出现在 API 响应。`api.json` 已从 Git 和 Docker 构建上下文排除，部署时需另外安全提供此文件。

脱敏结构示例（将占位值替换为对应服务商的密钥）：

```json
[
  {"apiKey": "YOUR_DEEPSEEK_KEY", "models": [{"url": "https://api.deepseek.com/chat/completions"}]},
  {"apiKey": "YOUR_MIMO_KEY", "models": [{"url": "https://api.xiaomimimo.com/v1"}]},
  {"apiKey": "YOUR_OPENAI_KEY", "models": [{"url": "https://api.openai.com/v1"}]}
]
```

相对 `AI_API_CONFIG` 路径按项目根目录解析；容器部署需单独只读挂载文件，并将该变量设为容器内路径。导入使用独立的 `api-import-条目下标-服务商` 连接，不覆盖已有连接或手工修改；相同服务商和密钥的重复端点合并。保持数组顺序可保证重复启动不产生新连接；后续密钥轮换请在控制台修改已导入连接。禁用读取不会删除已经保存的连接。模型发现后台执行，最多并发 3 个、每次总限时 25 秒，成功缓存会复用；失败不阻止启动，可在控制台重新获取。仅接受官方域名的 API 根地址或常见完整端点，拒绝自定义域名、HTTP、非标准端口、URL 认证信息、查询参数和片段。文件上限 1 MiB、最多导入 100 个连接；缺失文件忽略，错误信息不包含路径、密钥或文件内容。
