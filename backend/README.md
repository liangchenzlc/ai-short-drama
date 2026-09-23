# 后端

Python 3.12+ / FastAPI / SQLAlchemy 2 / MySQL 8。负责创作数据、AI 配置、异步生成、媒体存储与候选采用。当前为单用户应用，不含登录与多用户授权。

## 开发入口

在本目录执行：

```powershell
uv sync --locked
# 仅首次创建配置；已有 .env 时不要覆盖。
Copy-Item .env.example .env
# 填写数据库、RabbitMQ、MinIO 和 ENCRYPTION_KEY 后启动。
uv run uvicorn short_drama.main:app --host 127.0.0.1 --port 8000
```

API 文档：`http://127.0.0.1:8000/docs`，机器可读契约：`/openapi.json`。数据库初始化和异步任务的另外四个进程见[开发与运行](../docs/development.md)。只启动 API 不能执行生成任务。

`uv.cmd` 是可选的本地工具包装器，仅在 `backend/.tools/uv/uv.exe` 已存在时可用；它不会安装 uv。标准开发流程使用系统安装的 `uv`。

## 代码职责

```text
src/short_drama/
  api/          HTTP 路由、依赖注入、入参与状态码
  schemas/      Pydantic 输入/输出模型及结构化模型结果验证
  service/      业务规则、事务边界、版本检查与采用
  dao/          SQLAlchemy 查询与持久化操作
  domain/       全部 21 张表的 ORM 映射
  db/           连接池、Session、MySQL 连接配置
  ai/           提示词模板、协议适配、受限网络传输
  tasks/        Publisher、Recovery、Celery Worker
  storage/      MinIO 适配器
  core/         配置、异常、日志和密钥加密
  utils/        雪花 ID
```

入口为 `main:create_app`、`tasks.runtime` 和 `tasks.celery_app:app`。HTTP 服务通过依赖注入共享请求 Session；Service 拥有事务，DAO 不自行提交。独立使用 Service 时传入尚未开启事务的 Session，详见[系统架构](../docs/architecture.md)。

通用 CRUD Service、来源记录、回收恢复服务有内部调用与测试用途，不代表每个操作都已开放 HTTP 路由。公开接口以[接口约定](../docs/api/README.md)及 OpenAPI 为准。

## 验证

```powershell
uv run pytest tests/unit tests/api -q
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
```

数据库结构由[完整 SQL](../docs/数据库模型/schema.mysql8.sql)定义。`tests/unit/test_domain.py` 校验 ORM 和 SQL；真实 MySQL 约束与迁移需单独验证：

```powershell
uv run python scripts/run_integration.py
```

该脚本复用 `.env` 中的服务器连接配置，在服务器创建随机 `_test` 数据库，结束后仅删除该临时库；需要 CREATE/DROP DATABASE 权限。不会以应用库作为测试库。RabbitMQ/MinIO 测试另需显式启用，详见[测试分层](../docs/development.md#测试与验证)。

## 修改约定

- 表结构变更同时修改 ORM、完整 SQL、旧库迁移和数据库说明。
- HTTP 变更同步 Pydantic、前端 DTO 和接口说明；不得绕过 Service 修改正文、排序、确认或采用状态。
- ID 与版本对外为十进制字符串，时间为 UTC；不要在前端转成 `Number`。
- 模型密钥只放环境或加密保存；不得写入日志、测试快照或前端配置。
- 生成输出与当前采用分开；失败恢复不得隐式重复提交可能已受理的模型请求。
