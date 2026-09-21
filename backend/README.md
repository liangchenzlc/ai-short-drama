# AI 短剧后端

Python 3.12+ / uv / FastAPI / SQLAlchemy 2.x / PyMySQL，MySQL 8.0.21+。

## 启动

在 `backend` 目录执行：

```powershell
uv sync --locked
uv run uvicorn short_drama.main:app --reload --host 127.0.0.1 --port 8000
```

当前机器的 uv 和 Python 已准备在本项目 `.tools/` 下；没有全局 uv 时，把上述 `uv` 替换为 `.\uv.cmd`，例如：

```powershell
.\uv.cmd sync --locked
.\uv.cmd run uvicorn short_drama.main:app --reload --host 127.0.0.1 --port 8000
```

首次部署复制 `.env.example` 为 `.env` 并配置数据库。当前工作目录的 `.env` 已配置用户提供的云数据库，不要用模板覆盖。

- 测试接口：<http://127.0.0.1:8000/api/v1/test>，不访问数据库，返回 `{"message":"ok"}`。
- 数据库测试：<http://127.0.0.1:8000/api/v1/test/db>，执行 `SELECT 1`；连接失败返回 503，不泄露连接信息。
- MinIO 测试：<http://127.0.0.1:8000/api/v1/test/minio>，只读检查图片和视频 bucket；不可用或缺配置返回 503。
- Swagger：<http://127.0.0.1:8000/docs>。

应用启动不会建表或修改表。新数据库执行[完整建表脚本](../docs/数据库模型/schema.mysql8.sql)，包含当前全部 21 张表、模型能力缓存及五种任务状态，项目已移除目标时长。脚本仅含 CREATE TABLE；执行前选定空数据库，并将连接设为 utf8mb4、UTC 和严格 SQL 模式。历史增量脚本供旧库升级使用，五阶段工作流须按对应迁移 README 执行；实际业务库迁移进度见验收记录，不能根据代码版本推断已迁移。

## 目录与事务

```text
src/short_drama/
  api/v1/       HTTP 路由、参数和响应
  service/      业务规则、事务、审计、并发锁
  dao/          参数化查询和 flush，不提交事务
  domain/       21 个 SQLAlchemy 表映射
  schemas/      独立 Pydantic Create / Update / Read
  db/           连接池、Session、UTC 与严格 SQL 模式
  storage/      MinIO SDK、流式传输、稳定对象定位值
  core/         配置、异常、日志、密钥加密
  utils/        SnowflakeGenerator 雪花 ID 工具
  ai/           模型协议识别、请求校验、厂商适配和安全下载
  tasks/        RabbitMQ 发布、Celery Worker、租约恢复与调度进程
```

service 每次调用拥有完整事务，成功提交，失败回滚；调用时应传入尚未开启事务的 Session。多个 DAO 在同一业务事务中共享 Session。服务返回已物化的 Read 模型，离开事务后不触发延迟加载。查询列表返回 `items/total/offset/limit`，limit 为 1–500。

HTTP 已开放测试接口、AI 配置、异步生成、媒体资产管理、项目/分集、小说/剧本、分镜及三层素材库。素材图片上传作为候选保存，显式确认后才采用。尚未接入用户认证，生成资产不自动删除。

## 项目与分集管理

- `POST /api/v1/projects`：`name/aspect` 必填，`synopsis/style` 可选；返回 201。
- `POST /api/v1/projects/{project_id}/episodes`：`title` 必填，`synopsis/aspect/style` 可选；返回 201。省略画幅和风格时继承项目，显式空风格表示清空。排序由服务端在项目行锁内分配。
- ID 返回字符串；时间为 UTC（Z）。拒绝客户端归属、排序、审计字段及已移除的 `target_ms`。创建不会生成示例或触发 AI，POST 不自动去重。
- 现有数据库须先执行[目标时长移除迁移](../docs/数据库模型/migrations/2026-09-20-project-creation/README.md)，应用不自动迁移。
- [项目契约与事务规则](../docs/superpowers/specs/2026-09-20-project-creation-api.md)；[五阶段工作流接口](../docs/api/2026-09-21-production-workflow-api.md)涵盖素材、分镜与业务生成。

| 方法与路径（前缀 `/api/v1`） | 用途 |
| --- | --- |
| `GET /projects?offset=0&limit=20&q=名称` | 项目分页，按最近打开时间及 ID 倒序，返回分集数量 |
| `GET /projects/{id}` | 项目详情 |
| `PATCH /projects/{id}` | 修改名称、梗概、风格、画幅 |
| `POST /projects/{id}/open` | 记录最近打开时间，不改变项目修改时间 |
| `DELETE /projects/{id}` | 删除项目，成功返回 204 |
| `GET /projects/{id}/episodes?offset=0&limit=20` | 分集分页，按 position、ID 排序 |
| `GET /projects/{id}/episodes/{episode_id}` | 分集详情，额外返回按排序计算的 episode_number |
| `PATCH /projects/{id}/episodes/{episode_id}` | 修改标题、简介、独立画幅和风格 |
| `DELETE /projects/{id}/episodes/{episode_id}` | 删除分集，成功返回 204 |

所有分集读写均校验路径归属，不属于该项目的分集返回 404。分页 limit 为 1–100。
删除沿用外键 RESTRICT：仍有关联分集、素材或制作内容时返回 409，不隐式级联删除。

## 分集写作

旧库先执行[分集写作迁移](../docs/数据库模型/migrations/2026-09-21-episode-writing/README.md)，再重启后台进程。迁移仅增加编辑稿指针和版本，保留已有小说与所有候选剧本；新库完整 schema 已包含这些字段。

前缀为 `/api/v1/projects/{project_id}/episodes/{episode_id}`：

| 方法 | 路径 | 请求 / 返回 |
| --- | --- | --- |
| GET | `/writing` | 返回 episode_id、content_version、novel、editing_script、confirmed_script_id；未创建的正文为 null，读取不建记录 |
| PUT | `/novel` | content、content_version；返回 content_version、novel |
| PUT | `/script` | content、content_version、script_id；仅首次创建时 script_id=null；返回 content_version、script |
| PUT | `/editing-script` | script_id、content_version；返回完整 writing |
| POST | `/scripts/{script_id}/confirm` | content_version；返回完整 writing |

正文对象包含 id、content、updated_at；剧本额外包含 state（unconfirmed/confirmed）。ID 和版本返回字符串，时间为 UTC（Z）。所有写入在分集锁内校验版本，过期版本返回 409，错误归属返回 404，空白剧本确认和正文超限返回 422。单份正文上限 1 MiB UTF-8；允许空字符串，保留空白和换行，不接受 null。无变化保存不增加版本。

小说和剧本独立保存。修改已确认剧本会取消该稿确认；确认另一份稿时在同一事务中取消旧稿确认，最多保留一份已确认稿。选中候选只是切换编辑对象；小说编辑不自动修改剧本。后台 AI 新增候选不改变编辑指针和 content_version，显式切换/确认才推进版本；不能绕开 Service 直接修改这些表。

前端停顿 1 秒自动保存，小说/剧本串行提交最新版本，返回的旧响应不覆盖新输入。冲突暂停保存并允许下载草稿、明确载入服务端版本；网络异常先读取服务端核对。已有浏览器稿仅提供预览和显式导入，模型选择仍存于浏览器。确认是独立操作，不调用 AI；生成按钮创建异步任务。

验证命令：

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_episode_writing.py -q
.venv/Scripts/python.exe scripts/run_integration.py tests/integration/test_episode_writing.py tests/integration/test_episode_writing_migration.py -q
```

第二条命令只在随机创建的一次性 `_test` 数据库中测试并发与迁移，不对应用库运行破坏性测试。

## 异步生成启动

详见 [运行与验收说明](../docs/模型生成运行说明.md)。API、调度进程和 Worker 必须分别启动；只有 API 时任务会留在排队中。

在 `backend` 目录使用当前机器的本地 uv（各命令分别在独立终端执行）：

```powershell
# API，雪花节点 1
$env:SNOWFLAKE_WORKER_ID='1'
.\uv.cmd run uvicorn short_drama.main:app --host 127.0.0.1 --port 8000

# Publisher + Recovery（不调用模型），节点 10
$env:SNOWFLAKE_WORKER_ID='10'
.\uv.cmd run python -m short_drama.tasks.runtime

# 文本、图片、视频 Worker，各使用一个进程和线程池
$env:SNOWFLAKE_WORKER_ID='2'
.\uv.cmd run celery -A short_drama.tasks.celery_app:app worker --pool=threads --concurrency=4 -Q tasks.ai.text --hostname=text@%h --loglevel=WARNING

$env:SNOWFLAKE_WORKER_ID='3'
.\uv.cmd run celery -A short_drama.tasks.celery_app:app worker --pool=threads --concurrency=2 -Q tasks.ai.image --hostname=image@%h --loglevel=WARNING

$env:SNOWFLAKE_WORKER_ID='4'
.\uv.cmd run celery -A short_drama.tasks.celery_app:app worker --pool=threads --concurrency=2 -Q tasks.ai.video --hostname=video@%h --loglevel=WARNING
```

Windows 本地可用 `scripts/start_generation.ps1 -Role api|scheduler|text|image|video` 按角色后台启动，PID 和日志写到 `.runtime/`。每个角色只启动一次；生产部署推荐 Linux 容器，仍需为每个进程分配独立雪花节点号，不使用共用一个节点的 prefork 多进程池。

RabbitMQ 使用 `.env` 中的 `RABBITMQ_HOST/PORT/USER/PASSWORD/VHOST/TLS`。`5672` 是 AMQP 端口，`15672` 是管理页面端口；不会安装或重建用户的 RabbitMQ。执行 `python scripts/check_generation_infra.py` 可只读检查连接与新增表。

## AI 配置接口

统一前缀 `/api/v1`，支持 `text/image/video` 三类配置。前端通过 Axios 和 Vite `/api` 代理调用。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /ai-model-configs?service_type=text&offset=0&limit=20` | 分类分页，返回 `items/total/offset/limit` |
| `POST /ai-model-configs` | 创建，返回 201 |
| `POST /ai-model-configs/discover-models` | 使用服务地址和密钥探测模型列表，不保存配置 |
| `GET /ai-model-configs/{id}` | 查询单条 |
| `PATCH /ai-model-configs/{id}` | 更新，JSON 必须含 `row_version` |
| `DELETE /ai-model-configs/{id}?row_version=1` | 软删除，返回 204 |
| `PUT /ai-model-configs/{id}/default` | 设置同类默认，JSON 必须含 `row_version` |

创建字段为 `service_type/name/provider/model_key/base_url/apikey/enabled`；更新不可更改类别。`id` 和 `row_version` 在 JSON 中为十进制字符串，前端不得转换为 Number；`enabled/is_default/is_deleted` 为 0 或 1。编辑时省略 `apikey` 保留原密钥，传 `null` 清除，传新值则加密替换。读取仅返回 `has_api_key`。

错误响应统一为 `{"error":{"code":"...","message":"...","fields":[]}}`（`fields` 仅校验错误提供）。不存在返回 404，版本冲突返回 409，参数错误返回 422，依赖不可用返回 503。校验响应不回显请求输入。409 后应重新读取版本并让用户确认编辑，不能自动覆盖。

模型探测请求为 `{"base_url":"https://example.com/v1","apikey":"可选密钥","config_id":"可选的已存配置ID"}`，响应为 `{"items":[{"id":"model-name"}],"truncated":false}`。传 `config_id` 且省略 `apikey` 时，仅在地址与已存地址一致时复用已存密钥；显式 `null` 表示无密钥，填写新值仅用于这次探测。无密钥的已存配置可更换地址探测。探测不修改数据库，不调用生成接口。

参考 [cc-switch 模型发现](https://github.com/farion1231/cc-switch/blob/main/src-tauri/src/services/model_fetch.rs)：保留 URL 中的版本段和自定义路径，尝试 `/models` 或 `/v1/models`，仅在 404/405 时尝试备用路径。默认采用 OpenAI 兼容认证与 `data[].id`；支持 `models[].slug`，以及官方 Gemini/Anthropic 域名的认证和列表格式。不同服务不保证提供模型列表，前端始终允许手动填写；列表不猜测文本/图像/视频分类。上游分页或超过 2000 个模型时返回 `truncated=true`。

请求有超时和 2 MiB 响应限制，不跟随跳转，不返回上游原始错误或密钥。默认仅允许解析为公开 IP 的 HTTP(S) 地址，连接固定到已校验 IP；内网网关可由部署者通过 `MODEL_DISCOVERY_ALLOWED_HOSTS=["gateway.internal"]` 精确放行主机名。域名的多个已校验 IP 可在总超时内依次尝试。探测失败使用稳定的 `model_discovery_*` 错误码，前端映射成操作提示。

```python
from short_drama.core.config import Settings
from short_drama.db.session import build_engine, session_factory
from short_drama.schemas import ProjectCreate
from short_drama.service.project_service import ProjectService

engine = build_engine(Settings())
try:
    with session_factory(engine)() as session:
        projects = ProjectService(session)
        project = projects.create(
            ProjectCreate(
                name="新短剧",
                aspect="9:16",
            )
        )
        projects.update(project.id, {"synopsis": "故事梗概"})
        page = projects.list(offset=0, limit=20)
        print(page.model_dump_json())  # id 等标识符输出字符串
finally:
    engine.dispose()
```

普通 service 提供 `create/get/list/update/delete`；创建审计、主键、归属字段和技术生成列不接受普通更新。生成来源及回收记录不可更新。

特殊操作：

| 服务 | 操作 |
| --- | --- |
| ProjectService | `open(id)` 仅更新最近打开时间 |
| AssetService | `copy_for_library(library, link_id, changes)` 复制素材并只替换指定素材库关联，library 为 global/project/episode |
| 有序集合服务 | `reorder(parent_id, ids)` 必须提供该集合全部 ID；全局库 parent_id 为 None |
| EpisodeScriptService | `confirm(id)` 同一分集仅保留一份确认；正文实际变化自动取消确认 |
| AIModelConfigService | `update(id, payload)`、`delete(id, row_version)`、`set_default(id, row_version)`；软删除与乐观锁 |
| ShotImageService / ShotVideoService | `update` 替换媒体时旧结果进入回收站；`delete` 表示废弃，保留文件 |
| MediaRecycleBinService | `restore(id)` 恢复原参数并移除该回收记录；`delete` 仅移除回收记录 |
| NovelScriptRecordService / ScriptShotRecordService | `create_batch(payloads)` 原子写入同一批来源记录，精确重复请求返回已有记录 |
| GenerationService | `generate_scripts(novel_id, contents, model_id=None, batch_id=None)`、`generate_shots(script_id, contents, ...)` 将已取得的输出及来源一次入库，不调用 AI |

生成重试必须复用 `batch_id`。可先调用 `GenerationService.allocate_batch_id()`；同批来源、模型、结果内容必须一致。当前表结构没有历史输入快照，已编辑的结果不能当作原批次的精确重放。

## 雪花 ID

`utils/snowflake.py` 提供线程安全 `SnowflakeGenerator` 和进程统一入口 `next_id()`。所有 DAO 创建记录及生成批次使用该入口；主键不接受客户端指定。

- 纪元：2024-01-01 00:00:00 UTC。
- 41 位毫秒时间、10 位节点、12 位序列，生成正数 63 位 ID。
- `SNOWFLAKE_WORKER_ID` 取值 0–1023，当前默认单进程为 1。
- 每个同时运行的进程必须使用不同节点号。不要使用同一配置直接启动多个 uvicorn workers；多实例应分别分配节点号。
- 时钟回拨拒绝生成；同毫秒超过 4096 个 ID 等待时钟前进。重启复用节点前应确保旧进程停止、时钟超过旧进程最后时间。
- 主键、外键和批次 ID 在 JSON 中为字符串。Python 内部仍为整数。

现有库即使仍保留 `AUTO_INCREMENT`，显式写入雪花 ID 也兼容；应用不会使用数据库自增分配。新的建表脚本和 ORM 均已取消自增。现有库未自动执行 ALTER TABLE。

## AI 密钥

`ENCRYPTION_KEY` 为 Base64 编码的 32 字节 AES 主密钥，独立保存在环境配置，不进入数据库。初始化命令：

```powershell
uv run python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

新部署生成后自行填入 `.env` 的 `ENCRYPTION_KEY`；当前本地配置已生成该密钥。已加密存量数据依赖原主密钥，不要随意覆盖。当前信封版本为 v1，AES-256-GCM，随机 nonce，认证标签随密文保存。响应仅包含 `has_api_key`，不返回明文或密文。未配置主密钥时仍可启动和查询，但不能保存非空 API Key。

## 测试

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
```

默认运行单元/API 测试，未设置 `TEST_DATABASE_URL` 时明确跳过 MySQL 集成测试。

使用 `.env` 中的数据库服务器执行集成测试：

```powershell
uv run python scripts/run_integration.py
```

脚本只复用服务器连接凭据，在该服务器创建随机名称 `short_drama_<uuid>_test` 的临时库，运行原始建表 SQL 和测试，最后仅删除此次创建的临时库。需要 CREATE DATABASE / DROP DATABASE 权限，不会写入或清空应用数据库。也可通过环境变量 `TEST_DATABASE_URL` 指定独立测试服务器（协议 mysql+pymysql，库名以 `_test` 结尾），直接运行 `uv run pytest tests/integration`。

覆盖：20 表字段与约束映射、输入边界、雪花 ID 并发、DAO 事务归属、CRUD、审计、外键回滚、排序、AI 配置加密/版本/软删除、剧本确认、媒体回收恢复、来源批次及并发确认。MySQL 行锁、生成列和唯一约束均使用真实 MySQL 验证；测试库仅执行完整建表脚本初始化。

配置中的数据库口令与主密钥只放 `.env`；`.env`、虚拟环境和本地工具均被 `.gitignore` 排除。

## MinIO 对象存储

使用官方 `minio` SDK，通过 `StorageService → MinioStorage` 调用现有服务。当前 `.env` 已配置指定云端 endpoint 和凭据，复用 `image`、`video` 两个 bucket；不会自动创建 bucket 或修改访问策略。

| 配置 | 说明 |
| --- | --- |
| `MINIO_ENDPOINT` | `host:port`，不含 `http://` 或路径 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 访问凭据，仅放 `.env` 或部署环境 |
| `MINIO_SECURE` | 是否使用 HTTPS；当前给定端口使用 false |
| `MINIO_REGION` | 默认 `us-east-1` |
| `MINIO_IMAGE_BUCKET` / `MINIO_VIDEO_BUCKET` | 默认 `image` / `video` |
| `MINIO_CONNECT_TIMEOUT` / `MINIO_READ_TIMEOUT` | 默认 5 / 60 秒 |
| `MINIO_PRESIGN_EXPIRY` | 临时下载链接有效期，默认 900 秒，范围 1–604800 秒 |

客户端在 FastAPI lifespan 内创建，按进程复用连接池，关闭时释放；应用启动不发起远端探活。路由可通过 `Depends(get_storage_service)` 获取服务，同步 SDK 操作应放在普通 `def` 路由或线程池执行。

独立脚本调用示例：

```python
from pathlib import Path

from short_drama.core.config import Settings
from short_drama.service.storage_service import StorageService
from short_drama.storage.minio import MinioStorage

settings = Settings()
adapter = MinioStorage(settings)
storage = StorageService(adapter, settings)
try:
    path = Path("example.png")
    with path.open("rb") as stream:
        result = storage.upload(stream, length=path.stat().st_size, content_type="image/png")
    # result.storage_locator 可作为 media_files.storage_locator 保存。
    # 基础设施层不自动创建数据库记录。
    metadata = storage.stat(result.storage_locator)
    download_url = storage.download_url(result.storage_locator, expires_seconds=300)
    with storage.open(result.storage_locator) as response:
        with Path("downloaded.png").open("wb") as output:
            for chunk in response.stream(64 * 1024):
                output.write(chunk)
finally:
    adapter.close()
```

- `upload(data, length=..., content_type=...)` 按 MIME 的 image/video 类别选桶。对象名为 UTC 日期路径和雪花 ID，例如 `2026/09/18/123456789.png`；不用原始文件名作为对象路径。未知扩展名的合法媒体 MIME 保留 content type，对象名不加扩展名。
- `stat(locator)` 返回大小、类型、ETag、版本 ID、修改时间等。ETag 是存储系统标识，不能当作 SHA-256 内容校验值。
- `open(locator)` 是上下文管理器，退出时总会关闭响应并归还连接；视频可分块读取。
- `download_url(locator, expires_seconds=...)` 生成临时预签名下载 URL。URL 使用配置的 endpoint，因此访问者需要能连到该地址。
- `delete(locator, version_id=None)` 是物理删除基础能力。后续业务服务应先检查数据库引用，再决定删除；当前媒体元数据 CRUD 不会调用此方法。启用版本控制时，不传版本 ID 会写删除标记；传入上传结果中的版本 ID 则删除该版本。

持久化定位值为 `minio://image/2026/09/18/123456789.png`，包含 bucket 和对象名，不包含服务器地址或临时签名。只允许配置中的两个 bucket；图片/视频内容识别、上传权限、数据库与对象存储的补偿事务留给后续业务上传流程。此处校验的是调用方声明的 MIME 和传输参数。

连接检查和存储集成测试：

```powershell
uv run python scripts/check_minio.py
$env:RUN_MINIO_INTEGRATION = '1'
uv run pytest tests/storage_integration -q
Remove-Item Env:RUN_MINIO_INTEGRATION
```

`check_minio.py` 只读检查两个 bucket。存储集成测试仅在显式设置变量时执行，各创建一个独立图片/视频测试对象，验证上传、元数据、流式下载及签名链接，最后删除本次对象并确认不存在；不遍历、删除已有文件。视频测试对象仅用于传输验证，不是可播放成片。默认 `uv run pytest` 会明确跳过云存储集成测试。
