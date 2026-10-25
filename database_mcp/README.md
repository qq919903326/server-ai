# MySQL / PostgreSQL 远程 HTTP MCP

AI Studio 内使用数据库时，在「MCP 工具」点击「连接 MySQL」或「连接 PostgreSQL」，填写数据库 URL、账号和密码即可。无需启动本目录的 HTTP MCP 服务、配置 MCP URL 或 Token；保存后自动提供三个只读工具，测试连接会执行 `SELECT 1`。下面的独立部署步骤供其他远程 MCP 客户端接入使用。在 AI Studio 接入独立 HTTP 服务时，请使用「新建连接」。

两个可独立部署的 MCP 服务，使用项目现有的 MCP Python SDK 1.x，通过 Streamable HTTP 提供工具。不读取开发环境 tools、不发现本机 MCP、不提供 stdio 或本地命令执行。

| 服务 | 默认端口 | MCP 路径 |
| --- | --- | --- |
| MySQL | 9001 | `/mcp` |
| PostgreSQL | 9002 | `/mcp` |

## 工具

两种服务均提供：

- `list_tables()`：列出指定数据库 / schema 的表和视图，最多 500 条。
- `describe_table(table_name)`：查看字段、类型、可空性、默认值和顺序。表名作为参数绑定。
- `query(sql, max_rows=100)`：执行单条 SELECT，支持 CTE、JOIN、聚合，最多返回 500 行。

结果为 `columns` 和二维 `rows`，保留重复列名；`row_count` 是实际返回行数。超出行数、输出大小或字段长度时 `truncated=true`，不代表已返回所有匹配记录。Decimal 返回字符串以保留精度，日期为 ISO 格式，二进制为 `{"base64":"..."}`。

## 安装与配置

从项目根目录运行：

```powershell
.\.venv\Scripts\python -m pip install -r database_mcp/requirements.txt
Copy-Item database_mcp/.env.example database_mcp/.env
```

编辑 `database_mcp/.env`，填写需要使用的数据库连接和独立 MCP Token。两个服务可以使用不同数据库主机、账号和 Token。启动只检查配置，真正的数据库连接在工具调用时建立；`/health` 表示服务存活，不代表数据库已连接。

MySQL 必填：`MYSQL_HOST`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_MCP_TOKEN`。密码通过 `MYSQL_PASSWORD` 设置。

PostgreSQL 必填：`POSTGRES_HOST`、`POSTGRES_DATABASE`、`POSTGRES_USER`、`POSTGRES_MCP_TOKEN`。密码通过 `POSTGRES_PASSWORD` 设置，默认 schema 为 `public`。

Token 可使用以下命令自行生成，分别填到数据库 MCP 配置及 AI 服务的环境变量中：

```powershell
.\.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(32))"
```

仅使用具有必要表 SELECT 权限的专用数据库账号。SQL 解析和只读事务是附加限制，数据库账号权限仍是访问边界；不要使用数据库管理员账号。只读 SELECT 也可能调用函数，应限制该角色可执行的自定义或高权限函数。元数据工具限定配置的 database/schema，SQL 查询的数据范围由该账号的数据库权限决定。

## Windows 运行

在两个终端中分别运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-database-mcp.ps1 -Engine mysql -BindAddress 0.0.0.0
```

```powershell
powershell -ExecutionPolicy Bypass -File .\start-database-mcp.ps1 -Engine postgres -BindAddress 0.0.0.0
```

脚本默认仅监听 127.0.0.1；部署为远程服务时显式指定上述 BindAddress，并通过 HTTPS 反向代理访问。也可用 `-Port` 和 `-EnvFile` 指定端口或配置文件。

## Linux 运行

将 `database_mcp/` 拷贝到部署目录，以该目录的父目录为工作目录：

```bash
python -m venv .venv
.venv/bin/pip install -r database_mcp/requirements.txt
.venv/bin/python -m database_mcp --engine mysql --host 0.0.0.0 --port 9001
.venv/bin/python -m database_mcp --engine postgres --host 0.0.0.0 --port 9002
```

最后两条命令分别在独立终端或服务管理器中运行。配置从 `database_mcp/.env` 加载，也可以用 `--env-file /path/db.env` 或直接设置服务器环境变量。

## 接入 AI Studio

在运行 AI Studio 的服务器根目录 `.env` 中设置：

```dotenv
MYSQL_MCP_TOKEN=与MySQL服务端一致的令牌
POSTGRES_MCP_TOKEN=与PostgreSQL服务端一致的令牌
```

重启 AI Studio，在「MCP 工具」中点击「新建连接」，配置名称、Token 环境变量名、工具白名单及远程地址并保存：

| 配置项 | MySQL | PostgreSQL |
| --- | --- | --- |
| 名称 | MySQL | PostgreSQL |
| 远程 MCP URL | `http://MCP服务器地址:9001/mcp` | `http://MCP服务器地址:9002/mcp` |
| Token 环境变量名 | `MYSQL_MCP_TOKEN` | `POSTGRES_MCP_TOKEN` |
| 工具白名单 | `list_tables`、`describe_table`、`query` | 同左 |

先测试连接发现工具，再保存白名单并启用。在对话右侧选中数据库服务，即可让模型先查看表和字段，再执行查询。数据库密码只配置在 MCP 所在服务器，不发送给模型，也不配置在 AI Studio 的工具参数里。

## 限制与超时

- 只允许单条 SELECT，拒绝写入、锁定、事务、多语句及部分已知副作用函数。SQL 经 SQLGlot 解析后重新生成，移除注释；某些厂商扩展语法可能不兼容。
- 查询默认 15 秒，可配置 `*_QUERY_TIMEOUT_MS` 为 100–30000 毫秒；最多 8 个数据库任务并发，每个调用独立连接，完成后回滚并关闭。
- MySQL 使用 `START TRANSACTION READ ONLY` 和 `MAX_EXECUTION_TIME`，目标为 MySQL 5.7+/8.x；未适配 MariaDB 的超时变量。
- PostgreSQL 使用 read_only 事务、statement_timeout 和 lock_timeout。
- 用户 SQL 会在根查询追加或缩小 LIMIT，以便识别截断；不会提高原先较小的 LIMIT。
- 每个字段文本最多 20000 字符、二进制最多 15000 字节，返回行数据约 512 KB 上限。
- PostgreSQL 默认 `sslmode=require`；需要验证证书时使用 `verify-full` 和 CA。MySQL 配置 `MYSQL_SSL_CA` 时验证证书及主机名，否则使用驱动默认连接方式。
- 所有 HTTP 路径要求 `Authorization: Bearer <MCP Token>`，包括 `/health`；当前采用静态 Token，不实现 OAuth。

## Docker

从项目根目录构建同一个镜像：

```bash
docker build -f database_mcp/Dockerfile -t database-mcp .
docker run -d --name mysql-mcp --env-file database_mcp/.env -p 9001:9001 database-mcp
docker run -d --name postgres-mcp --env-file database_mcp/.env -p 9002:9002 database-mcp --engine postgres --host 0.0.0.0 --port 9002
```

数据库 HOST 应填写容器能访问的数据库地址，不能用容器内 127.0.0.1 指代宿主机。CA 文件路径也必须在容器内存在，按需挂载证书。

## 验证范围

SQL 策略、参数绑定、输出序列化、驱动只读设置、认证，以及完整 HTTP MCP 客户端/服务端协议均有测试。协议测试使用数据库测试替身，不连接开发环境的 MCP。目前未提供真实数据库地址和凭据，也没有 Docker 环境，尚未验证真实 MySQL/PostgreSQL 实例及容器运行。

```powershell
.\.venv\Scripts\python -m pytest -q
```
