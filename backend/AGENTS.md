# backend 协作指南

> 本文件只写 `backend/` 特有约定。通用规则见根 `AGENTS.md` 与 `PRINCIPLES.md`；冲突时停下来说明，不自行取舍。

Python 3.12+、FastAPI、Pydantic 2、SQLAlchemy 2、MySQL、Celery。Settings 从当前工作目录读取 `.env`，后端进程与脚本一律在 `backend/` 下执行。

## 文件存放

| 目录 | 放什么 | 不该放 |
| --- | --- | --- |
| `src/short_drama/api/v1` | 路由：HTTP 语义、依赖注入、状态码 | 业务规则、直接写 SQL |
| `api/dependencies.py` | Service 构造与依赖提供 | 业务分支 |
| `schemas/` | Pydantic 请求／响应，按资源一模块 | 把 ORM 对象直接外泄 |
| `service/` | 业务规则、事务边界、跨 DAO 组合 | 绕开 DAO 拼散装 SQL |
| `dao/` | SQL 查询与 ORM 读取 | 提交事务、业务判断 |
| `domain/` | SQLAlchemy ORM，表结构唯一来源 | 业务方法 |
| `ai/` | 适配器、网关、流式、`prompts/<场景>/system.md` | 页面或路由逻辑 |
| `agent/` | 会话、工具、`builtin_skill_data/` 方法包 | — |
| `tasks/` | Celery 任务与媒体处理 | 跨请求的长事务 |
| `db/`、`storage/`、`core/` | 会话与权限、MinIO、配置／异常／日志 | 业务规则 |
| `utils/` | 无状态工具函数 | 依赖 Session 的逻辑 |
| `scripts/` | 运维与迁移脚本，可独立执行 | 被应用 import 的业务逻辑 |
| `tests/unit`、`tests/api`、`tests/integration`、`tests/storage_integration`、`tests/support`、`tests/fixtures` | 分层测试与公共夹具 | 临时调试脚本 |

命名：

- 模块 `snake_case`；一个资源成套出现：`api/v1/<资源>.py` + `schemas/<资源>.py` + `service/<资源>_service.py` + `dao/<资源>_dao.py`。
- 不新建 `<模块>_v2.py` 并列实现，直接改造现有文件；只有新增表才新增 `domain/<表>.py`。
- 增量 SQL 进 `docs/database/migrations/<日期-功能>/`，不要放进 `backend/`。

## 代码编写规范

- 分层 API → Service → DAO → ORM：路由不查库，DAO 不提交，Service 管事务边界。
- 依赖通过 `api/dependencies.py` 的 `Annotated[T, Depends(...)]` 注入，不在路由里直接 new Service。
- 事务用 `with self.session.begin():`；独立调用 Service 时传入尚未开启事务的 Session。
- 时间统一取 `service.base.utcnow()`（与 MySQL 连接的 naive UTC 一致），不要各处写 `datetime.now()`。
- ID 对外十进制字符串、内部 `int`，用 `schemas.base.Identifier` 与 `parse_identifier` 转换。
- 异常只抛 `core/exceptions.py` 的 `BusinessError` 家族：`NotFound`、`Conflict`、`WorkflowError(code, message, status_code, details)`。不把供应商原文、SQL 或堆栈细节透给前端。
- 日志用模块级 `logger = logging.getLogger(__name__)`；I/O 与异常路径记录上下文，不吞异常、不静默失败。
- 输入输出全部经 Pydantic 校验，结构化 AI 输出同样校验；提示词约束不能替代服务端校验。
- 表结构变更三步齐全：改 ORM → 在当前库执行 DDL → 更新 `docs/database/schema/schema.mysql8.sql` 与增量迁移。
- 类型注解齐全，`snake_case`，行宽 100，ruff 选择 `E/F/I/UP/B`（`api/**` 允许 `B008`）。

## 验证

- `uv run ruff check src tests scripts`、`uv run ruff format --check src tests scripts`。
- 测试范围按根 [按功能执行测试](../docs/instruction/按功能执行测试.md) 选择；数据库行为追加 `mysql`，队列／存储追加 `infra`。
- 只启动 API 不会执行生成任务；需要 Worker 时用 `.\scripts\start_generation.ps1 -Role <角色>`，角色与配置见[开发运行与验证](../docs/instruction/开发运行与验证.md)。
