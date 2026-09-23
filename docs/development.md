# 开发、运行与验证

命令按 Windows PowerShell 编写；除明确标注外，后端命令在 `backend/`、前端命令在 `frontend/` 执行。Linux/macOS 使用相同 uv/npm 命令，环境变量按对应 shell 设置。

## 环境准备

| 依赖 | 要求 |
| --- | --- |
| Python | 3.12+，依赖由 `backend/uv.lock` 固定 |
| Node.js | 20.19+ 或 22.12+，依赖由 `frontend/package-lock.json` 固定 |
| MySQL | 8.0.21+，InnoDB、utf8mb4、严格模式、UTC |
| RabbitMQ | 可访问的 AMQP 服务；5672 默认明文，TLS 端口按部署配置 |
| MinIO | 预先准备图片和视频两个 bucket，应用不会自动创建 |

```powershell
# backend/
uv sync --locked
# 仅首次执行；不要覆盖已有 .env。
Copy-Item .env.example .env
```

填写 `backend/.env`。Settings 从当前工作目录读取 `.env`，所以 API、调度器、Worker 和脚本都应从 `backend/` 启动。

| 配置 | 含义与注意事项 |
| --- | --- |
| `DB_HOST/PORT/USER/PASSWORD/NAME` | 应用数据库连接；不要把测试临时库写成应用库 |
| `ENCRYPTION_KEY` | Base64 编码的 32 字节 AES 主密钥；已有密文依赖原密钥 |
| `SNOWFLAKE_WORKER_ID` | 0–1023，每个同时运行的进程必须不同 |
| `RABBITMQ_*` | AMQP 连接；15672 是管理 UI 端口，不能用于任务连接 |
| `MINIO_ENDPOINT` | `host:port`，不带协议或路径，浏览器也必须能访问该地址 |
| `MINIO_ACCESS_KEY/SECRET_KEY/SECURE` | 凭据及是否 HTTPS，与实际服务一致 |
| `MINIO_IMAGE_BUCKET/VIDEO_BUCKET` | 默认 `image/video`，两者必须不同 |
| `MINIO_PRESIGN_EXPIRY` | 临时媒体 URL 有效秒数，默认 900 |
| `MODEL_DISCOVERY_ALLOWED_HOSTS` | 仅需内网模型网关时设置精确主机名 JSON 数组，默认 `[]` |
| `GENERATION_*_BUDGET_SECONDS` | 文本/图片/视频执行和媒体归档预算，详见 `.env.example` |
| `EXTRACTION_MAX_*` | 提取输入字符、候选数和输出 Token 上限，默认 30000/100/8192 |

完整可用配置及限制以 [Settings](../backend/src/short_drama/core/config.py) 为准。`GENERATION_QUEUE_NAMESPACE` 默认 `short_drama`；自定义时需同步修改 Worker 队列名，见下文。

仅新部署可生成主密钥：

```powershell
uv run python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

将结果保存到本机 `.env` 或部署密钥系统。不要提交、发到日志或覆盖已有主密钥；数据库备份需配套妥善保管原主密钥。

## 初始化与升级数据库

新库先创建并选定空数据库，按[数据库说明](数据库模型/MySQL8数据表设计.md)执行 [schema.mysql8.sql](数据库模型/schema.mysql8.sql)。该文件仅有当前 21 张表的完整 `CREATE TABLE`，不要再拼接增量迁移，也不要对已有表重复执行。

旧库按[迁移索引](数据库模型/migrations/README.md)核验基线，备份后执行所缺批次及各自验证脚本。应用不执行自动建表或迁移；只替换代码不能替代数据库升级。

## 启动后端

需要五个独立进程，以下各段分别在独立终端执行。默认队列下：

```powershell
# API
$env:SNOWFLAKE_WORKER_ID = '1'
uv run uvicorn short_drama.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Publisher + Recovery
$env:SNOWFLAKE_WORKER_ID = '10'
uv run python -m short_drama.tasks.runtime
```

```powershell
# 文本 Worker
$env:SNOWFLAKE_WORKER_ID = '2'
uv run celery -A short_drama.tasks.celery_app:app worker --pool=threads --concurrency=4 -Q tasks.ai.text --hostname='text@%h' --loglevel=WARNING
```

```powershell
# 图片 Worker
$env:SNOWFLAKE_WORKER_ID = '3'
uv run celery -A short_drama.tasks.celery_app:app worker --pool=threads --concurrency=2 -Q tasks.ai.image --hostname='image@%h' --loglevel=WARNING
```

```powershell
# 视频 Worker
$env:SNOWFLAKE_WORKER_ID = '4'
uv run celery -A short_drama.tasks.celery_app:app worker --pool=threads --concurrency=2 -Q tasks.ai.video --hostname='video@%h' --loglevel=WARNING
```

线程池里的线程共享同一进程雪花生成器。不要直接改成共用同一节点 ID 的 prefork 多进程或多个 Uvicorn Worker。重启复用节点前，应确认旧进程已停止且时钟超过旧进程最后发号时间。

自定义 namespace 为 `studio_dev` 时，队列是 `studio_dev.tasks.ai.text/image/video`，所有进程配置必须一致。默认 namespace 不加前缀。不同数据库或独立开发环境共用 RabbitMQ 时必须使用不同 namespace，避免另一环境消费并丢弃本环境的任务。手动启动时同步修改 `-Q` 队列名。

Windows 可选用 `scripts/start_generation.ps1 -Role api|scheduler|text|image|video` 分别后台启动，PID/输出写入 `backend/.runtime/`。脚本使用现有 `.venv`，从后端 Settings 自动解析实际队列名，并使用上表节点号；每个角色只启动一次，不与手动进程重复启动。它不是通用生产进程管理器，也不会因进程启动成功就保证依赖健康。

## 启动前端

```powershell
# frontend/
npm ci
npm run dev
```

地址 `http://127.0.0.1:5173`。端口被占用时退出，不自动换端口。开发服务器将 `/api` 转发至 `http://127.0.0.1:8000`。

如需调整，复制 `frontend/.env.example` 为 `.env.local`，设置 `API_PROXY_TARGET` 后重启 Vite。`VITE_API_BASE_URL` 默认 `/api/v1`，会进入浏览器产物，只能放公开配置。

## 测试与验证

后端快速验证，不依赖真实模型或业务数据库：

```powershell
uv run pytest tests/unit tests/api -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

前端验证：

```powershell
npm test
npm run typecheck
npm run build
```

Node 测试覆盖纯逻辑、保存会话、请求契约和部分组件源码约定，不是浏览器端到端测试。生产构建不能证明真实模型可用或桌面/移动端交互已验收。

真实 MySQL 验证：

```powershell
uv run python scripts/run_integration.py
# 或仅运行指定测试
uv run python scripts/run_integration.py tests/integration/test_production_workflow_migration.py -q
```

`run_integration.py` 读取 `.env` 中的数据库服务器凭据，覆盖子进程的 `TEST_DATABASE_URL` 为测试基址；fixture 创建随机 `short_drama_<uuid>_test` 数据库，执行完整建表 SQL，结束后删除它。需要服务器 CREATE/DROP DATABASE 权限。脚本不采用应用数据库作为测试库，也不会使用原环境中的测试 URL 覆盖 `.env` 服务器。

要使用独立测试服务器，直接设置 `TEST_DATABASE_URL` 并运行 `uv run pytest tests/integration -q`；连接协议必须是 `mysql+pymysql`，所提供数据库名以 `_test` 结尾。fixture 仍会创建新的随机库。没有配置该变量时，直接运行 `uv run pytest` 会跳过 MySQL 集成测试。

以下测试需显式启用，使用真实外部基础设施；应在隔离环境执行，结束后移除启用变量：

| 环境变量 | 测试 | 写入范围 |
| --- | --- | --- |
| `RUN_MINIO_INTEGRATION=1` | `tests/storage_integration` | 独立图片/视频测试对象，测试结束清理 |
| `RUN_GENERATION_BROKER_TESTS=1` | `tests/integration/test_generation_broker.py` | 随机数据库及 RabbitMQ 隔离命名空间 |
| `TEST_PRODUCTION_INFRA=1` | `tests/integration/test_production_generation.py` | 随机库、队列及 MinIO 测试对象，模型上游为测试替身 |

隔离基础设施测试不等于真实供应商验收。真实模型联调应记录配置、输入、张数/时长、费用预算和结果；不要用付费调用作为默认单元测试。

## 只读检查与故障定位

```powershell
uv run python scripts/check_generation_infra.py
uv run python scripts/check_minio.py
```

`check_generation_infra.py` 按当前 Settings 检查实际 namespace 下的队列与消费者数量。

已成功生成且有持久化媒体记录的 `save` 消息，若投递后超过一个执行租约周期仍无人接手，调度器会增加消息版本并仅重投保存动作；旧版本消息失效，归档按输出标识幂等保存，不重新调用模型。超过归档预算则转为可恢复的保存超时。`submit/poll` 的已投递消息不使用此规则，避免重复付费生成。

HTTP 检查：`GET /api/v1/test`、`/api/v1/test/db`、`/api/v1/test/minio`。API 启动不主动探活数据库和 MinIO，因此不能只凭进程存活判断系统可用。

| 现象 | 先检查 |
| --- | --- |
| 任务一直排队 | scheduler 与对应 Worker 是否运行；namespace、AMQP 端口及连接是否一致 |
| 任务失败且不能重试 | 读取 `can_resume/can_retry` 和错误码；未知受理不能自动重新提交 |
| 保存返回 409 | 保留草稿，重新读取版本后核对；不要自动覆盖 |
| 生图参数拒绝 | 模型是否支持参考图、画幅、分辨率与张数；独立任务可先省略可选参数 |
| 图片 URL 无法显示 | 签名是否过期；浏览器是否能访问 MinIO endpoint；HTTPS 页面是否引用 HTTP 资源 |
| 旧库缺字段/表 | 核对迁移索引和 schema；重启 API 不能补数据库结构 |
| 读配置正常但保存 Key 失败 | `ENCRYPTION_KEY` 是否有效，已有密文是否仍使用原主密钥 |

RabbitMQ 消费确认超时需要覆盖单次 Worker 调用时长。较长文本/视频预算应同步评估 broker 的 `consumer_timeout`；应用不会替部署者修改 RabbitMQ 配置。

## 部署与维护

- 非 loopback 地址访问使用 HTTPS；生成请求依赖安全上下文中的 Web Crypto（crypto.subtle/randomUUID）。localhost/127.0.0.1 是本地开发例外，普通局域网 HTTP 不满足该前提。
- API、调度器与 Worker 交给进程管理器或容器管理，分配唯一雪花节点，保留可检索日志和异常退出告警。
- 静态站点先将 `/api/` 代理到后端，再对页面路径使用 `index.html` fallback；Vite 开发代理不会进入构建产物。
- **部署前核对 Vite `base`。** 当前配置为 `./`，而路由采用 BrowserRouter；深层链接刷新可能将资源解析到错误目录。部署到域名根目录时应设为 `/`，子路径部署需同时协调资源 base、路由 basename 和代理路径，再验证直接打开分集 URL。这是当前部署限制，不是已通过的验收项。
- 当前无账户与多用户权限，只适合受控个人环境；对外多人服务需要补齐认证和数据隔离。
- 备份 MySQL、MinIO 对象和加密主密钥，验证恢复流程；不能只备份数据库中的临时媒体 URL。
- 不按名称批量删除验收数据。若存在旧 `.runtime/*manifest*` 清单，先核实具体 ID、对象与引用，再按清单精确处理；仓库历史验收记录不能证明这些对象仍存在。
- SQL、接口、产品能力变化时同步对应维护文档；不在当前文档里累计机器 PID、一次性截图路径或历史通过数量。
