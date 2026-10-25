# P2P 本地文件 MCP

## 推荐：下载 Windows P2P 授权助手（支持公网 IP + HTTP）

1. 在「本地文件库」点击「下载 Windows 授权助手」。
2. 双击 `AIStudioAssistant.exe`，无需安装 Python 或依赖。
3. 点击助手中的「选择文件夹并授权」，网页自动连接并出现文件库。

无需填写地址、设备 ID、Token 或本地路径。文件保留本机，服务端通过 WebRTC DataChannel 按需读取，不上传整个目录。仅有三个只读工具；本地绝对路径不会登记到服务器。请保持助手运行，关闭助手停止访问；「停止授权/移除」立即拒绝读取，网页移除会同步撤销。

首次下载附带 10 分钟有效的一次性配对票据，成功配对后设备凭据有效 30 天。重复打开同一个下载文件可恢复连接；到期或撤销后从网页重新下载。凭据通过 Windows DPAPI 绑定当前用户保存在 `%LOCALAPPDATA%/AIStudioAssistant`。HTTP 下下载、配对与信令不加密，P2P 不能消除 HTTP 链路的风险。

### 管理员构建 / Docker 部署

在 Windows x64 项目开发环境执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\build-assistant.ps1 -InstallBuildTools
```

构建脚本生成 `assistant_dist/AIStudioAssistant.exe`，并对无配对模板、附加配对数据后的 EXE 分别运行冻结依赖自检。最终用户运行 EXE 不执行 pip、不需要 Python。模板缺失时下载接口返回 503，不回退到在线安装依赖的脚本包。可配置 `AI_ASSISTANT_EXE` 指向模板；默认读取项目根目录的 `assistant_dist/AIStudioAssistant.exe`，Dockerfile 已复制该目录。

服务器需设置 `APP_TOKEN`；前端以认证 POST 下载单个 EXE，配对票据不会放在 URL。Windows 客户端无需开放入站 HTTP 服务或调整浏览器安全标志。

### UDP 受限网络的自动兼容

除 `FILE_ICE_SERVERS_JSON` 直连外，桌面助手支持通过既有 HTTP/HTTPS 端口承载 TURN/TCP 流。文件请求和内容仍在 WebRTC DTLS DataChannel 内传递，没有上传整目录或替换成文件上传接口。用户无需配置 TURN 地址或密钥。

管理员在服务端部署 coturn，TCP 仅监听 `127.0.0.1:3478`；`use-auth-secret` 使用的共享密钥须与 AI 服务的 `FILE_DESKTOP_TURN_SECRET` 一致。coturn 的 `relay-ip` 使用后端可达的服务器本机 IPv4，限制允许访问的 peer 为该后端地址。Linux Docker 可使用 host 网络，使后端与本机 relay 连通。可将 coturn 限制为 UDP relay、固定 relay 端口段和配额；无需将 TURN 控制端口暴露公网。

AI 服务通过已认证设备的 `/agent/desktop-turn` WebSocket 固定转发到本机 TURN，动态发放短期 TURN 凭据，不向客户端分发共享密钥。反向代理须允许此路径的 WebSocket Upgrade。撤销设备会关闭该通道，客户端会在凭据到期前自动重连续期。browser 授权模式仍使用正常 ICE，不会使用桌面专属通道。

已验证：Windows 模板与附加配对数据后的单文件 EXE 均通过冻结依赖自检，并能打开及正常关闭 GUI；真实本机 DataChannel 测试覆盖配对、授权、读取和撤销。公网 IP + HTTP 环境也已通过认证下载 EXE、配对、TURN 中继 WebRTC 数据通道、工作流只读工具读取本机测试文件与撤销同步验证，未调用真实模型。

## 可选：浏览器选择目录授权（HTTPS / localhost）

1. 在「本地文件库」点击「浏览器直接授权」。
2. 浏览器弹出系统目录选择窗口，选择项目文件夹并允许只读访问。
3. 浏览器自动完成设备识别、配对和 P2P 连接，项目加入路径库，点击「用于对话」即可使用。工作流也能选择该项目的只读工具。

无需设备 ID、MCP 地址、授权命令、助手下载或本地 Python。浏览器按需读取选中目录中的文件，不预先上传整个项目、不扫描磁盘。没有写入、删除或执行工具。系统授权提示由浏览器控制；不能绕过拒绝授权，取消选择不会新增项目或配对。

项目库显示浏览器提供的文件夹名称，不显示/推测磁盘绝对路径。目录句柄和配对信息保存在当前浏览器的 IndexedDB。刷新后自动检查只读权限，仍有效时恢复连接；权限失效时通过项目的「重新授权」再次选择目录，不会在后台弹授权窗口。

保持本站至少一个页面打开才能提供文件访问；关闭全部页面会停止访问。切换聊天、工作流等页面不影响连接。同一浏览器的多个标签页用 Web Locks 选出一个连接持有者，避免抢占；其他页共享已授权目录。不同电脑/浏览器需要各自选择授权。移除项目立即阻止服务端新的读取，不删除本地文件。

目录选择依赖 File System Access API，需支持该功能的浏览器和 HTTPS（localhost 测试除外）。详情见 [目录选择 API](https://developer.mozilla.org/en-US/docs/Web/API/Window/showDirectoryPicker) 与 [目录权限 API](https://developer.mozilla.org/en-US/docs/Web/API/FileSystemHandle/requestPermission)。不支持的浏览器会显示提示。

服务器部署需设置 APP_TOKEN，反向代理转发 /api/ 和 /agent/，保留正确的 Host 与 X-Forwarded-Proto，信令超时大于 40 秒。信令与项目功能复用 AI 服务端口，单进程运行。公网 WebRTC 需配置合适的 FILE_ICE_SERVERS_JSON（STUN/TURN）与网络访问；不能保证所有 NAT 环境均能直连。只读文件工具参数、分页限制与独立 MCP 一致。

实现验证：浏览器端到端测试以真实 OPFS 目录句柄代替无法在无头环境点击的系统目录选择窗口；IndexedDB、WebRTC、服务端 ToolHub 和工作流读取均为真实执行，并验证切页、刷新、多个标签页、取消及拒绝授权。跨公网 NAT/TURN 与真实系统目录提示仍需在目标浏览器部署后验证。

## 高级：独立部署文件 MCP

以下保留独立 Streamable HTTP 文件 MCP 的手工部署方式；日常文件库使用上面的桌面助手或浏览器授权流程。独立 Python 代理的 HTTPS 限制保持不变。

服务端运行独立 Streamable HTTP MCP（默认 9003 端口），文件所在电脑运行 Python 代理。代理主动通过 HTTPS 交换 WebRTC SDP，然后通过加密的 WebRTC DataChannel 传递只读文件请求及结果；不通过信令 HTTP 上传文件，不依赖本机开发环境的 MCP。

首次在文件所在电脑选择授权目录，配置保存到本地；以后启动代理自动读取配置、连接并断线重试。这里的自动权限是继承当前系统账号的权限并沿用明确授权的目录，不申请管理员权限，不扫描所有磁盘，不绕过系统访问控制。不需要摄像头或麦克风权限。

## 安装

服务端和本地电脑都需要 Python 3.11+。复制 `file_mcp` 目录到本地电脑的工作目录，按需建立虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r file_mcp/requirements.txt
```

AI Studio 项目执行 `setup.ps1` 时会一并安装这些依赖。代理不需要 React 或 AI 模型 Key。

## 1. 服务端配置

复制 `file_mcp/.env.example` 到 `file_mcp/.env`。生成两个独立随机 Token（不要复用）：

```powershell
.\.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))"
```

- `FILE_MCP_TOKEN`：AI Studio 调用此 MCP 的 Token。
- `FILE_DEVICES_JSON`：设备 ID 到设备 Token 的 JSON 对象，例如 `{"my-pc":"设备专用随机Token"}`。每个 Token 至少 32 字符，且不能与 MCP Token 或其他设备相同。
- `FILE_ICE_SERVERS_JSON`：ICE 服务列表，局域网可用 `[]`；跨公网通常需要自行部署或配置 STUN/TURN，示例：`[{"urls":"stun:stun.example.com:3478"},{"urls":"turn:turn.example.com:3478","username":"user","credential":"password"}]`。示例地址不可直接使用。

启动（项目根目录）：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-file-mcp.ps1
# 或 Linux/Windows 通用
python -m file_mcp --host 127.0.0.1 --port 9003
```

反向代理将独立域名的 `/signal/`、`/mcp`、`/health` 全部转发到此端口，使用 HTTPS。信令请求超时需大于 40 秒，保留 Authorization 请求头。代理的 server 地址必须是此独立根地址，不支持子路径前缀。同一服务只运行一个 worker；代理在线会话保存在内存。

WebRTC 使用独立 UDP 网络端口，不是 HTTP 反向代理内的数据流。需允许服务器动态 UDP 出入站或配置可用的 TURN；无法直连时可由 TURN 中继加密数据，不能保证所有网络下都实现直连。服务端和代理双方均要配置 ICE；不自动开放防火墙或修改路由器。

## 2. 本地首次授权

复制 `file_mcp/agent.env.example` 为 `file_mcp/agent.env`，填入此设备对应的 `FILE_DEVICE_TOKEN`。在**文件所在电脑**执行：

```powershell
.\.venv\Scripts\python -m file_mcp.agent --authorize --server https://files.example.com --device my-pc
```

弹出系统目录选择框，选中的目录授权为只读的 `files`。也可明确指定多个目录，不弹框：

```powershell
.\.venv\Scripts\python -m file_mcp.agent --authorize --server https://files.example.com --device my-pc --allow "docs=E:\Documents" --allow "reports=D:\Reports"
```

保存到 `file_mcp/agent.local.json`，包含服务地址、设备 ID、目录路径及文件身份、Token 环境变量名和 ICE 设置，不含设备 Token。根目录被删除或替换后会拒绝读取，需要重新授权。可在本地编辑其中的 `ice_servers`，格式同服务端 ICE 列表。授权命令会替换现有配置，修改授权目录时重新运行授权命令并重启代理。

然后启动：

```powershell
.\.venv\Scripts\python -m file_mcp.agent
# 在完整项目目录也可双击/运行脚本
powershell -ExecutionPolicy Bypass -File .\start-file-agent.ps1
```

首次授权后每次启动会自动恢复连接，断线按 2–30 秒退避重试。脚本不安装开机常驻服务。关闭代理立即停止新的读取；删除本地配置可撤销目录授权，服务端删除设备 Token 并重启可撤销配对。远程服务地址强制 HTTPS，仅 127.0.0.1 / localhost 的测试允许 HTTP。

## 3. 接入 AI Studio

独立部署方式下，在 AI Studio 服务端 `.env` 配置同名 `FILE_MCP_TOKEN` 并重启 AI Studio。在「MCP 工具」中新建普通远程连接，填写 `https://files.example.com/mcp`，Token 环境变量填写 `FILE_MCP_TOKEN`，白名单添加下表的工具名称，保存后测试发现并启用。授权目录仍由本节的本地 `--authorize` 命令管理。若希望无需命令和手工配置，请改用文档开头的「本地文件库」自动流程。

| 工具 | 参数与结果 |
| --- | --- |
| `list_devices` | 返回配置的设备 ID 和通道在线状态 |
| `list_roots` | `device_id`，返回该电脑授权目录别名 |
| `list_directory` | `device_id, root_id, path='', offset=0, limit=100`，最多 100 项/页 |
| `file_info` | `device_id, root_id, path`，返回类型、大小、修改时间 |
| `read_text` | `device_id, root_id, path, offset=0, max_bytes=16384`，只读 UTF-8 文本 |

例：“列出 my-pc 上授权的目录，再读取 docs 下的 project/README.md”。只传相对路径，禁止绝对路径、`..`、NTFS 数据流、符号链接及目录联接，不提供写入、删除、执行或自动扩展授权接口。目录不公开本地绝对路径；文本按字节分页，`truncated` 和 `next_offset` 表示还有数据，UTF-8 跨页切分可能出现替换字符。文件内容可能包含敏感信息或恶意指令，模型会接收到读取结果，因此只授权确实需要的目录。

设备离线时 MCP 服务仍能发现工具，但读取会明确失败。列表“在线”表示 WebRTC 通道已打开；心跳检测约 15–23 秒内识别失联。读取总超时 20 秒，每设备最多 8 个待处理请求，响应限制 60 KB。文件正在变动时结果不是一致性快照；本地授权配置及目录由可信本地用户管理。

## 已验证范围

测试使用临时目录，真实建立两个 aiortc peer，经过 HTTP MCP → WebRTC → 本地文件 → WebRTC 返回文本，并检查越界、鉴权和断线。未在用户真实电脑或跨公网 NAT/TURN 环境完成部署验证，实际连接仍需填入双方地址、令牌和授权目录。

实现依据：[aiortc WebRTC API](https://aiortc.readthedocs.io/en/latest/api.html)。
