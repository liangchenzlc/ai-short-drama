# Docker 一键启动后端依赖

## 依赖分析

依据 `backend/pyproject.toml`、`core/config.py`、`tasks/celery_app.py` 和 `storage/minio.py`，后端需要以下外部服务：

| 服务 | 用途 | Compose 镜像 | 默认本机端口 |
| --- | --- | --- | --- |
| MySQL | 50 张表：账号与项目权限、业务数据、异步任务和媒体元数据 | `mysql:8.4`（满足 8.0.21+ 要求） | 3306 |
| RabbitMQ | Celery 原生、成片、Agent 任务消息和死信交换机 | `rabbitmq:4.1-management` | 5672；管理页面 15672 |
| MinIO | 图片、视频对象存储和临时签名 URL | `minio/minio:RELEASE.2025-04-22T22-12-26Z` | S3 API 9000；控制台 9001 |

Celery 是后端 Python 进程，不是额外的数据库服务；任务结果由 MySQL 保存，当前没有 Redis 依赖。队列由后端调度器和 Worker 声明。RabbitMQ 配置将消费确认超时设为 25 小时，覆盖应用允许的最长 24 小时视频执行预算。

Python 3.12+ 及 FastAPI、Uvicorn、SQLAlchemy/PyMySQL、Pydantic、Cryptography、Celery、MinIO SDK、HTTPX、urllib3、Pillow、python-multipart 等库，通过 `backend/` 中的 `uv sync --locked` 安装。AI 模型服务的地址、模型和 API Key 需在应用中另行配置；Compose 不部署模型服务。

## 一键启动

安装并启动 Docker Desktop（Windows 使用 Linux 容器），或安装 Docker Engine 和 Compose v2。在**仓库根目录**执行：

```powershell
docker compose up -d
```

无需先创建根目录 `.env`，Compose 内置了本地开发默认值。首次启动会拉取镜像，并执行以下初始化：

- MySQL 创建 `short_drama` 数据库和同名应用用户；仅在数据卷为空时执行仓库的完整 `schema.mysql8.sql`，创建 50 张表。已有卷按[协作迁移](collaboration-deployment.md)显式升级，容器重启不会补齐结构。
- RabbitMQ 创建 `short_drama` 用户，使用 `/` vhost。
- `minio-init` 等待 MinIO 健康后创建 `image`、`video` 两个私有 bucket，重复执行不会删除已有对象。
- 三个服务使用独立命名数据卷，并配置健康检查和自动重启。

检查启动结果；首次建库可能需要一两分钟：

```powershell
docker compose ps -a
docker compose logs minio-init
```

MySQL、RabbitMQ、MinIO 应显示 `healthy`；`minio-init` 应为 `Exited (0)`，日志显示 buckets ready。`up -d` 返回不代表所有初始化已经完成；若初始化失败，先检查对应服务日志。

默认凭据仅用于本机开发，所有端口都绑定 `127.0.0.1`：

| 服务 | 用户 | 密码 | 入口 |
| --- | --- | --- | --- |
| MySQL 应用用户 | `short_drama` | `short_drama_dev` | `127.0.0.1:3306/short_drama` |
| MySQL 管理用户 | `root` | `short_drama_root_dev` | 容器内使用 |
| RabbitMQ | `short_drama` | `short_drama_mq_dev` | <http://127.0.0.1:15672> |
| MinIO | `short_drama` | `short_drama_minio_dev` | <http://127.0.0.1:9001> |

## 连接本机后端

首次配置时，在 `backend/` 执行 `uv sync --locked`，将 `.env.example` 复制为 `.env`。已有 `.env` 时保留现有配置，按需修改以下项目：

```dotenv
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=short_drama
DB_PASSWORD=short_drama_dev
DB_NAME=short_drama

RABBITMQ_HOST=127.0.0.1
RABBITMQ_PORT=5672
RABBITMQ_USER=short_drama
RABBITMQ_PASSWORD=short_drama_mq_dev
RABBITMQ_VHOST=/
RABBITMQ_TLS=false

MINIO_ENDPOINT=127.0.0.1:9000
MINIO_ACCESS_KEY=short_drama
MINIO_SECRET_KEY=short_drama_minio_dev
MINIO_SECURE=false
MINIO_REGION=us-east-1
MINIO_IMAGE_BUCKET=image
MINIO_VIDEO_BUCKET=video
```

`MINIO_ENDPOINT` 使用宿主机可访问的地址，因为后端生成的签名 URL 也会被浏览器访问；不要改成仅容器网络可解析的 `minio:9000`。

新部署还需在 `backend/` 生成主密钥并填入 `backend/.env` 的 `ENCRYPTION_KEY`；已有加密数据时继续使用原密钥：

```powershell
uv run python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

随后按[开发指南](development.md#启动后端)启动 API、调度器及所用队列的独立 Worker（默认文本、图片、视频、成片与 Agent；音频按启用情况增加），并给各进程分配不同的 `SNOWFLAKE_WORKER_ID`。成片 Worker 还需要宿主机安装 FFmpeg / FFprobe。本 Compose 只启动外部依赖。Agent 默认开启，已有库启动前需显式七表迁移并准备独立 Worker；迁移期间或暂不使用时显式设置 `AGENT_ENABLED=false`，详见 [Agent 部署](agent-deployment.md)；不增加新的依赖容器。API 启动后可用 `/api/v1/test/db` 和 `/api/v1/test/minio` 检查连接。

默认 MySQL 应用用户仅有 `short_drama` 库权限，足以运行应用；创建随机数据库的集成测试需要另行配置具有 CREATE/DROP DATABASE 权限的测试用户。

## 修改端口、凭据与日常维护

需要覆盖默认值时，将**根目录** `.env.example` 复制为 `.env` 并修改，再执行 `docker compose up -d`。例如端口被已有 MySQL 占用时设置 `MYSQL_PORT=3307`，同时将 `backend/.env` 中的 `DB_PORT` 改为 `3307`。所有可覆盖变量见根目录示例文件。

根目录 `.env` 供 Compose 使用，`backend/.env` 供 Python 应用使用，两者不会自动同步。MySQL 和 RabbitMQ 的默认用户配置只在空数据卷首次初始化时生效；已有数据时更改密码需使用相应服务的用户管理功能，再同步后端配置。

```powershell
# 查看日志
docker compose logs --tail=100 mysql rabbitmq minio minio-init

# 停止并移除容器、网络，保留数据库、消息和对象数据
docker compose down

# 再次启动，复用现有数据
docker compose up -d

# 修复配置后重新执行 bucket 初始化
docker compose run --rm minio-init
```

不要随意使用 `docker compose down -v`，它会删除这套服务的数据卷。更新 SQL 文件不会自动升级已有数据库；旧库仍需按[迁移指南](数据库模型/migrations/README.md)执行增量迁移。

声音制作增加私有桶 `MINIO_AUDIO_BUCKET`（默认 `short-drama-audio`），`minio-init` 可重复创建。数据库增量迁移、配音队列与 FFmpeg/中文字幕字体配置见[制作功能说明](production-features.md)。依赖容器启动不等于 API、调度器或配音 worker 已启动。
