# backend 协作指南

> 本文件只写 `backend/` 特有约定。通用规则见根 `AGENTS.md`；冲突时停下来说明，不自行取舍。

Python 3.12+、FastAPI、Pydantic 2、SQLAlchemy 2、MySQL、Celery、MinIO。Settings 从当前工作目录读取 `.env`，后端进程与脚本一律在 `backend/` 下执行。

本文件按「技术栈 → 目录 → 路由 → 引用与风格 → 校验与错误 → 配置与持久化 → 验证」组织，每条都写明**范围**、**约束**和**怎么检查**。与参照的 Node 服务端规范不一致处，标出原因与替代做法。

## 一、启动与装配

**范围**：`src/short_drama/main.py` 与进程启动方式。

`create_app(settings: Settings | None = None)` 只负责装配：中间件、路由、异常处理器，返回 FastAPI 实例。

**约束**：

- 不在 `create_app` 里启动监听；监听由 `uvicorn` 命令承担。
- 不在模块导入时创建数据库连接、Celery Worker 或后台线程。
- API 进程与 Celery Worker 共享 `service/` 与 `dao/`，不为 Worker 另写一套业务逻辑。

**检查**：`create_app` 内不出现 `uvicorn.run`、`Thread`、`Process`；Worker 任务里不出现重复的业务判断。

> 对应参照规范：`index.ts` / `app.ts` 分离 → 本项目由 uvicorn CLI 承担启动入口，`create_app` 只做装配。参照规范的「桌面端复用 app、不启 cluster」不适用——本项目没有桌面端，替代约束是 Worker 复用同一 Service 层。

## 二、目录与命名

**范围**：新增文件、目录、模块时的归属与命名。

| 目录 | 放什么 | 不该放 |
| --- | --- | --- |
| `api/v1/<业务>/` | 路由：HTTP 语义、依赖注入、状态码 | 业务规则、直接写 SQL |
| `api/dependencies/` | Service 构造与依赖提供 | 业务分支 |
| `schemas/<业务>/` | Pydantic 请求／响应 | 把 ORM 对象直接外泄 |
| `service/<业务>/` | 业务规则、事务边界、跨 DAO 组合 | 绕开 DAO 拼散装 SQL |
| `dao/<业务>/` | SQL 查询与 ORM 读取 | 提交事务、业务判断 |
| `domain/<业务>/` | SQLAlchemy ORM，表结构唯一来源 | 业务方法 |
| `tasks/` | Celery 任务与媒体处理 | 跨请求的长事务 |
| `core/`、`db/`、`storage/` | 配置、异常、日志、会话权限、MinIO | 业务规则 |

**约束**：

- 模块、函数、变量用 `snake_case`；类用 `PascalCase`；模块级常量用 `UPPER_SNAKE`。
- 业务目录按 identity、projects、creation、assistant、generations、ai_config、skills、media、canvas、system 划分，再按功能细分。
- 上层目录已有语义时，文件名不重复业务名：写 `service/projects/create.py`，不写 `service/projects/project_create_service.py`。
- 不新建并列 v2 实现；ORM 按实体放到业务目录，不集中到一个 models 文件。
- 不为单个简单接口新建 controller／service／repository 之外的层；但**已有的 API → Service → DAO 分层不因「接口简单」而跳过**。

**检查**：新增 `service/` 文件时问一句——它有没有绕过 DAO 直接写 SQL；新增路由时问一句——它有没有直接操作 Session。

> 对应参照规范：文件与变量小驼峰 → Python 用 `snake_case`，这是 PEP8 强制，与前端规范的小驼峰不冲突（语言不同）。「不为简单接口搭 controller/service/repository」→ 本项目方向相反：分层是既有约束，简单接口也必须走 Service，否则权限、事务、错误码会散落在路由里。

## 三、路由与路径

**范围**：`api/v1/**` 下的路由定义与注册。

**约束**：

- 路由用 `APIRouter(prefix="/...")`，在 `api/v1/router.py` 里显式 `include_router`，由 `create_app` 统一挂载。
- **普通 CRUD 路由用同步 `def`**。项目使用同步 SQLAlchemy 会话，`async def` 里执行同步查询会阻塞事件循环。
- 只有 SSE、流式响应、分片上传、中间件与 MCP 工具用 `async def`——这些要么框架强制，要么必须 `await`。现有 `canvas_events`、`conversation_events`、`upload_chunk`、`identity_middleware` 属于此类。
- 一个函数对应一个端点；同一业务模块的端点放同一文件（参照规范的「一个接口一个文件」不适用于本项目）。
- 路径用复数名词，路径参数用 `{project_id}` 形式，段内不用下划线或驼峰。
- 声明 `response_model=`，不在函数里手工拼字典返回。

**检查**：`grep -rn "async def" src/short_drama/api/` 的结果应只落在 SSE、上传、中间件、MCP 文件里；新增路由文件必须在 `api/v1/router.py` 出现。

> 对应参照规范：文件扫描自动生成路由、`bun run routes` 同步、`router1` 自动命名——均不适用。FastAPI 不做文件扫描，显式注册是刻意的：注册顺序、前缀冲突、路由可见性都可控。代价是新增模块要手工加一行，漏加时接口 404，这是可接受的失败方式。

## 四、引用与代码风格

**范围**：所有 `backend/src` 下的 Python 文件。

**约束**：

- 用绝对导入：`from short_drama.service.projects import ProjectService`。禁止 `sys.path` 操作、`import *`、跨顶层相对导入。
- 第三方库用包名导入；仅用于类型标注的导入放 `if TYPE_CHECKING:` 块。
- 第三方函数、类保留原始导出名，不做无意义的 `as` 重命名。
- 格式交给 `ruff format`，缩进、引号、行宽不由人决定；行宽 100，规则集 `E/F/I/UP/B`。
- 文件顺序：标准库 → 第三方 → 本项目导入；然后是常量、类、函数。
- 删除未使用的 import 与变量（`ruff` 的 `F401` 会拦）。
- 函数目标单屏读完：50 行、3 层嵌套、5 个参数。只在被两处以上调用、包住复杂判断、或能起准确名字时才抽取。
- 路由函数只写 HTTP 语义与依赖注入，业务分支放 Service。

**检查**：`uv run ruff check src tests scripts` 与 `ruff format --check` 零输出。

> 对应参照规范：「双引号、分号、两空格缩进」→ 由 ruff 统一，不在规范里约定；「`@/` 别名」→ Python 用包绝对路径，无别名机制；「`import u from "@/utils"` 统一出口」→ Python 直接按模块导入，不设聚合对象，避免循环导入。

## 五、参数校验、响应与错误

**范围**：所有 HTTP 出入口。

**约束**：

- 入参用 Pydantic 模型；body 用模型参数，query／path 用 `Query`／`Path` 标注，ID 用 `schemas.base.Identifier`。
- Pydantic 的默认值与类型转换会生效，但**跨字段规则、业务合法性仍要在 Service 校验**；不能用「模型能过」代替业务判断。
- 返回用 `response_model=` 声明，HTTP 状态码表达结果，不额外包 `{code, data, message}` 信封。
- 异常只抛 `core/exceptions.py` 的 `BusinessError` 家族：`NotFound`、`Conflict`、`WorkflowError(code, message, status_code, details)` 等，由 `create_app` 注册的处理器统一映射。
- 禁止 `except Exception` 后记日志继续执行；写入失败不得返回成功。
- 流式（SSE）接口在发送响应头之后不能改状态码，错误通过流内事件传递并清理资源。

**检查**：`grep -rn "except Exception" src/short_drama/` 逐条确认是否有重新抛出；新增路由的 `response_model` 不为空。

> 对应参照规范：`validateFields` 只校验不写回 → Pydantic 会把解析结果写回模型，但**不会写回 `request` 对象**，两者的差异要在写代码时意识到：拿到的永远是模型实例，不是原始 dict。`success/error` 信封 → 本项目用 `response_model` + HTTP 状态码，前端 DTO 直接对应模型。

## 六、配置与持久化

**范围**：配置读取、数据落盘、多进程状态。

**约束**：

- 配置只有一处：`core/config.py` 的 `Settings`，从 `.env` 读取，进程内单例。不在路由或 Service 里临时读环境变量。
- 业务数据写 MySQL，媒体写 MinIO；没有「应用数据目录」概念，不引入本地 JSON 配置文件。
- 用户级设置存数据库，随账号隔离。
- 表结构变更三步齐全：改 ORM → 在当前库执行 DDL → 更新 `docs/database/schema/schema.mysql8.sql` 与增量迁移。
- Celery 是多进程多实例：**不能用进程内变量保存跨请求状态**，租约、幂等、配额一律落库或走 Redis。

**检查**：`grep -rn "os.environ\|os.getenv" src/short_drama/` 除「把环境整体传给子进程」（如 `service/canvas/tools/depth/depth_execute.py`）外不应出现；新增模块级可变全局变量时确认是否需要跨进程。

> 对应参照规范：`conf` + `settings.json` + `TOONFLOW_DATA_DIR` + Windows/macOS 路径 → 全部不适用。本项目是服务端部署，配置来自 `.env`，数据在 MySQL 与 MinIO，没有「安装目录」与「可更新资源」的区别。参照规范的「工具实例是进程内单例、不支持多进程并发写同一工作区」→ 本项目反过来：多进程是常态，必须依赖数据库锁而不是进程内单例。

## 七、验证

**范围**：改完代码、准备声称完成之前。

**约束**：

- 静态检查：`uv run ruff check src tests scripts`、`uv run ruff format --check src tests scripts`。
- 结构门禁：仓库根执行 `node scripts/check-quality.mjs`，判定规则看脚本输出与报错位置。
- 测试选择：`node scripts/test.mjs --list` 查功能编号，`--feature <编号> --level fast` 跑；涉及数据库追加 `mysql`，队列／存储追加 `infra`。
- 需要 Worker 时用 `.\scripts\start_generation.ps1 -Role <角色>`；只启动 API 不会执行生成任务。
- 报告只写实际执行过的命令与输出；跳过、未运行、用测试替身的部分必须明说，构建或静态检查通过不等于接口行为已验证。

**检查**：交付说明里每条结论都能对应一条命令输出。

> 对应参照规范：`bun run routes`（无路由生成步骤）、`build`（Python 无构建）→ 不适用；`typecheck` → 由 ruff 与运行时 Pydantic 共同承担，项目未引入 mypy，类型注解靠评审与测试保证。参照规范的「配置读写用临时目录验证」→ 对应本项目是测试夹具创建隔离数据库，不触碰开发库。
