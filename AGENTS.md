# 项目协作指南

## 最高原则

1. **先理解，再动手**：修改前先读相关模块、调用链和测试，不要基于猜测改代码；不确定的 API 或库用法去查文档，禁止凭记忆臆造。
2. **最小改动**：只改当前任务涉及的文件，不做"顺手重构"；需要重构先说明理由并征得同意。
3. **代码能跑才算完成**：提交前执行下方"验证命令"中相应范围的验证，实际跑过的才算数。
4. **不留残留**：不引入 TODO、被注释掉的死代码和 `console.log`/`print` 调试输出；日志用项目统一 logger。
5. **不夸大验证**：不要将测试替身、跳过或未执行的部分描述为真实供应商验收。

## 沟通与工作方式

- 默认使用中文回复、说明修改和编写新增维护文档；代码标识符、接口字段和命令沿用现有命名。
- 开始修改前检查 `git status --short`，保留已有未提交改动；只修改当前任务涉及的文件。
- 改动超过 3 个文件或涉及跨层（API → Service → DAO → ORM + 前端 DTO）时，先给出改动计划（改哪些文件、为什么），再动手。
- 先阅读相关模块及测试，沿用现有分层和实现方式。维护说明与代码不一致时，核对实际配置、路由和测试，并同步更新受影响的说明。
- 完成后说明具体改动、已执行的验证和未验证的部分；不要将测试替身通过描述为真实供应商验收。
- 报错要完整：贴关键 stack trace 和复现步骤，不要只说"报错了"。
- 本文件适用于整个仓库；子目录如有 `AGENTS.md`，同时遵循其针对该目录的约定。

## 项目结构

AI 短剧工作台支持账号协作、小说与剧本、素材、分镜、异步生成、Agent 创作及成片合成。业务数据以 MySQL 和 MinIO 为准。

| 目录 | 职责 |
| --- | --- |
| `frontend/src/app` | React 入口、路由、主题及全局样式 |
| `frontend/src/pages` | 页面组合与分集阶段入口 |
| `frontend/src/features` | 业务组件、编辑会话、保存与生成状态 |
| `frontend/src/api` | 统一 HTTP 客户端、请求模块和 DTO |
| `frontend/tests`、`frontend/e2e` | Node 测试与 Playwright 浏览器测试 |
| `frontend/canvas` | 独立画布子包、HTML 入口、依赖锁与源交互实现 |
| `backend/src/short_drama/api`、`schemas` | FastAPI 路由、依赖注入及 Pydantic 契约 |
| `backend/src/short_drama/service`、`dao`、`domain` | 业务事务、数据访问及 SQLAlchemy ORM |
| `backend/src/short_drama/ai`、`agent`、`tasks` | 模型协议与提示词、Agent 执行及异步任务 |
| `backend/src/short_drama/db`、`storage`、`core` | 数据权限、数据库、MinIO、配置及基础设施 |
| `backend/scripts`、`backend/tests` | 运维与迁移脚本、分层测试 |
| `docs`、`docker`、`compose.yaml` | 维护文档、数据库 SQL 及本机基础设施 |

技术栈：React 19、TypeScript strict、Vite 7、Ant Design、Axios、React Router；Python 3.12+、FastAPI、Pydantic 2、SQLAlchemy 2；MySQL 8.0.21+、RabbitMQ、Celery、MinIO。两套前端统一运行推荐 Node.js 22.18+ 的 22.x 或 24.x LTS；画布测试使用 Node 类型剥离，nanoid 6 不支持 Node 20。

## 开发与启动

命令以 Windows PowerShell 为例。后端 Settings 从当前工作目录读取 `.env`，后端进程和脚本应在 `backend/` 执行。

| 工作目录 | 命令 | 用途 |
| --- | --- | --- |
| 仓库根目录 | `docker compose up -d` | 启动本机 MySQL、RabbitMQ 和 MinIO |
| `backend/` | `uv sync --locked` | 按 `uv.lock` 准备 Python 环境 |
| `backend/` | `uv run uvicorn short_drama.main:app --host 127.0.0.1 --port 8000` | 启动 API |
| `backend/` | `.\scripts\start_generation.ps1 -Role scheduler` | 启动 Publisher 与 Recovery |
| `backend/` | `.\scripts\start_generation.ps1 -Role text` | 启动文本 Worker；其他角色见下文 |
| `frontend/` | `npm run install:all` | 分别按宿主与 `canvas/` 的锁文件安装依赖 |
| `frontend/` | `npm run dev` | 一个进程启动标准工作台 8080 与画布 8082 |

- 仅在配置文件不存在时从 `.env.example` 创建本机配置，保留已有 `.env`。前端代理使用 `.env.local` 的 `API_PROXY_TARGET`，默认后端为 `http://127.0.0.1:8000`。
- `npm run dev:standard`、`npm run dev:canvas` 可单独启动；统一 `dev` 退出时关闭自己创建的两台 Vite 服务器。统一 `build` 分别检查和构建两包，再将画布静态产物复制至 `frontend/dist/canvas-app/`；`preview` 使用该统一产物，并支持画布独立 HTML 回退。
- 只启动 API 不会执行生成任务；按使用范围启动 `text`、`image`、`video`、`render`、`agent`，启用音频时增加 `audio` Worker。启动脚本依赖现有 `.venv`，支持 `-DryRun`。
- 每个并行运行的后端进程必须使用不同的 `SNOWFLAKE_WORKER_ID`；启动脚本为各角色分配节点。不同开发环境共用 RabbitMQ 时使用不同 `GENERATION_QUEUE_NAMESPACE`，所有相关进程的配置须一致。
- 账号认证和 Agent 默认开启；Agent 依赖认证、显式数据库迁移和独立 Worker。未完成 Agent 升级的环境按部署文档显式设置 `AGENT_ENABLED=false`，不要为绕过检查而修改默认值。
- 成片渲染依赖 FFmpeg/ffprobe；完整启动、数据库初始化和配置见 [开发指南](docs/development.md) 与 [Agent 部署](docs/agent-deployment.md)。

## 验证命令

根据修改范围选择验证。纯文档变更核对内容、命令和链接即可；代码变更先执行相关测试，再完成相应检查。

| 工作目录 | 命令 | 用途 |
| --- | --- | --- |
| `backend/` | `uv run pytest tests/unit tests/api -q` | 不依赖业务数据库或真实模型的快速验证 |
| `backend/` | `uv run ruff check src tests scripts` | Python 静态检查 |
| `backend/` | `uv run ruff format --check src tests scripts` | Python 格式检查 |
| `backend/` | `uv run python scripts/run_integration.py` | 使用随机临时数据库执行 MySQL 集成测试 |
| `frontend/` | `npm test` | Node 内置测试运行器 |
| `frontend/` | `npm run typecheck` | 快速 TypeScript 类型检查 |
| `frontend/` | `npm run build` | 两包类型检查与生产构建，组合为 `dist/` |
| `frontend/canvas/` | `npm test`、`npm run test:e2e`、`npm run source:check` | 画布合同、浏览器与源哈希检查 |
| `frontend/` | `npm run test:e2e` | Playwright 浏览器测试，可追加具体测试路径 |

- `build` 已包含类型检查，成功后无需重复执行 `typecheck`。浏览器测试使用独立端口 4175、API 测试替身和 Chromium；首次准备浏览器可执行 `npx playwright install chromium`，或设置 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`。
- MySQL 集成测试需要服务器 CREATE/DROP DATABASE 权限，创建并清理随机 `_test` 数据库；不要将应用数据库作为测试库。未配置集成环境时的跳过不代表集成验证通过。
- RabbitMQ、MinIO 和 Agent 基础设施测试按 [测试分层](docs/development.md#测试与验证) 在隔离环境显式启用；默认测试不调用付费模型。
- 修 bug 时优先先写一个能复现该 bug 的测试，再修复，确保测试由红转绿；新功能的关键业务路径至少覆盖正常路径和边界/异常路径，不追求覆盖率数字。

## 实现约定

### 后端与数据

- 遵循 API → Service → DAO → ORM。路由负责 HTTP 与依赖注入，Service 管理业务规则和事务，DAO 不自行提交；独立调用 Service 时传入尚未开启事务的 Session。
- 保留可信 Actor、项目归属及资源范围检查。系统 Session 仅用于内部认证、Worker 和运维等明确场景，不能由 HTTP 输入开启。
- 请求与输出通过 Pydantic 验证，结构化 AI 输出也必须校验；不能以提示词约束代替服务端验证。
- 所有 I/O（网络、数据库、消息队列、存储）和外部调用的错误路径必须处理，错误信息包含上下文（操作什么、失败原因）；异步任务必须处理异常，不吞异常、不让任务静默失败。
- 数据库 ID 与版本对外使用十进制字符串，前端禁止转为 `Number`；剪辑片段使用稳定 UUID，时间使用 UTC。
- 表结构变化必须三步齐全，缺一不可：① 同步修改 ORM 模型；② 真实在当前使用的数据库中执行 DDL（新增/变更表后立即生效，不只改文件）；③ 更新 `docs/数据库模型/schema.mysql8.sql` 及旧库增量迁移与数据库说明。新库使用完整 SQL，旧库使用所缺迁移。应用代码不自动建表或迁移，但执行 DDL 是修改任务的一部分，完成后须说明已在哪个库执行及执行了哪些语句。
- HTTP 契约变化同步 Pydantic 模型、前端 DTO 与接口文档。Python 使用类型注解、snake_case，遵循 Ruff 配置的 100 字符行宽及现有风格。

### 前端与编辑会话

- 通过 `src/api/http.ts` 和 `src/api/modules` 发起请求，DTO 保留服务端 snake_case；不要在组件中另建 Axios 客户端。
- 保留串行自动保存、版本校验和导航保护。生成前先完成来源保存；旧响应不能覆盖新输入，409 冲突保留草稿并等待明确处理，不自动覆盖服务端内容。
- 沿用相邻模块的 TypeScript/React 写法、业务 Hook 和公共组件。界面修改先阅读 [产品范围](frontend/PRODUCT.md) 与 [设计约定](frontend/DESIGN.md)，复用现有主题和样式变量，保持冷黑、紧凑的中文工作台风格。
- 页面提供就近错误反馈、键盘操作及窄屏行为；候选、已保存、已确认和当前采用须表达清楚。每个异步操作必须有明确的 loading 态，每个数据区域必须有 loading / 空状态 / 错误状态三态。

### 生成、Agent 与媒体

- AI 输出先保存为候选，由用户明确采用；生成成功不能直接替换正文、素材、分镜或当前媒体。采用时保留版本、上下文及共享影响检查。
- 生成提交遵守 `Idempotency-Key`：同一不确定请求复用原键，内容变化或明确新建任务才使用新键。恢复与重试遵循 `can_resume`、`can_retry` 等服务端许可，`unknown` 不能自动重新提交。
- Agent 对话、全部创作候选及生成历史仅本人可见，只有明确采用后的作品按项目权限共享。统一消息由 Agent 判断问答、单项执行或多步骤计划；明确单项媒体任务在参数唯一确定时直接执行，多步骤/批量仍需计划批准，候选另行采用。会话按阶段、稳定对象和任务范围隔离，模型选择不要求能力验证；保留授权范围与来源版本校验，项目权限不能代替候选作者校验。
- 持久化稳定媒体定位值及元数据，临时签名 URL 只用于展示和下载；过期后重新读取，不将签名 URL 或 Blob URL 作为持久化身份。
- 密钥只放环境或服务端加密存储，不写入日志、测试快照、浏览器存储或 `VITE_*` 配置；不覆盖已有加密主密钥，不在对话中读取或输出 `.env` 的实际内容。

## 文档与提交

- 常用入口：[系统架构](docs/architecture.md)、[接口约定](docs/api/README.md)、[前端 API 约定](frontend/src/api/README.md)、[数据库迁移](docs/数据库模型/migrations/README.md)、[账号协作部署](docs/collaboration-deployment.md)。历史计划和验收记录用于背景参考，当前行为仍需核对代码与维护文档。
- 保留 `uv.lock`、`package-lock.json` 和 `.env.example`；依赖变更同步相应锁文件。不提交凭据、依赖目录、下载工具、缓存、构建产物或 `.runtime` 日志与截图。
- 需要提交时沿用现有 Conventional Commits 风格，例如 `feat:`、`fix:`、`docs:`；提交范围与任务保持一致。一个 commit 只做一件事，不主动执行 `git commit`，除非用户明确要求。

## 完成定义

一个任务只有同时满足以下条件才算完成：

- [ ] 相关验证命令已实际执行并通过（测试跳过或未运行的部分已在总结中声明）
- [ ] 无调试残留、无敏感信息、无未声明的新依赖
- [ ] 改动文件与任务范围一致，未顺手重构无关代码
- [ ] 用户能从总结中知道：改了什么、怎么验证的、还有什么没验证

## 无限画布迁移的专用边界

- 用户明确要求布局、交互、操作和功能一比一保留 BeefTV 固定版本；实施依据为 [2026-10-05 迁移方案](docs/plans/2026-10-05-beeftv-infinite-canvas-migration.md)，进度见[实施记录](docs/plans/2026-10-05-beeftv-implementation-log.md)。标准模式不改变。
- `frontend/canvas/` 作为独立子包保留自己的 `package.json`、锁文件、HTML、React root、Provider、CSS、字体、组件和交互实现；宿主统一安装、启动、构建与预览入口。按用户 2026-10-06 的调整，共同声明但版本不同的直接依赖采用宿主依赖版本，源独有依赖继续独立锁定，并处理其兼容依赖；不为画布升级或替换标准前端依赖。依赖对齐后的视觉和行为仍需对照，不能沿用旧版本验收结果作为本次证明。数据库与服务边界接入当前 Python/MySQL/MinIO，源 HTTP 请求复用宿主客户端。
- 无限画布生成回填与助手图操作以源既有直接应用/提议确认为准，不插入标准模式的候选采用步骤。这是用户一比一要求的限定例外；私人参数、任务、凭据、生成历史及对话仍按本人隔离，已进入共享图的作品按项目权限共享。
- 只有全部布局、操作、媒体、生成、助手与真实运行对照通过后才能声明完整迁移。阶段完成、哈希检查、构建或 API 夹具测试不等于一比一验收；源已禁用的入口保持源状态。
