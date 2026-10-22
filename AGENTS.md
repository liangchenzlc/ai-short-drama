# 项目协作指南

> **开始任何任务前先读[最高原则](PRINCIPLES.md)。** 那是每次执行都生效的最高优先级规则：编码前思考、简洁优先、精准修改、目标驱动执行。本文件只补项目特有约定，两者冲突时停下来指出冲突，不要自行取舍。

AI 短剧工作台：账号认证、小说与剧本、素材、分镜、异步生成、Agent 创作与成片合成。业务数据以 MySQL 和 MinIO 为准，后端分层 API → Service → DAO → ORM。

技术栈：React 19、TypeScript strict、Vite 7、Ant Design；Python 3.12+、FastAPI、Pydantic 2、SQLAlchemy 2；MySQL 8.0.21+、RabbitMQ、Celery、MinIO。前端统一使用 Node 22.18+ 的 22.x 或 24.x LTS。

本文件只写**项目特有的规则**和**去哪读细节**的地图，通用工作方法见最高原则，具体做法不在入口文件展开。

## 文件地图

| 要做的事 | 先读 |
| --- | --- |
| 查产品有什么、代码文件在哪一层 | [项目概览](docs/instruction/项目概览.md) |
| 了解系统怎么跑、数据怎么流、各层职责 | [系统架构与数据流](docs/instruction/系统架构与数据流.md) |
| 装环境、起后端 / 前端 / Worker | [开发运行与验证](docs/instruction/开发运行与验证.md) |
| 选测试、判断改动影响哪些功能 | [按功能执行测试](docs/instruction/按功能执行测试.md)、[测试文件清单](docs/instruction/测试文件清单.md) |
| 写代码：分层、接口契约、保存与反馈 | [实现约定](docs/instruction/实现约定.md) |
| 交付前确认结构门禁 | [代码质量规范](docs/instruction/代码质量规范.md) |
| 做代码审查 | 指定 `code-review-skill` 执行，分级与流程见[代码质量规范](docs/instruction/代码质量规范.md) |
| 改表结构、写增量迁移 | [表设计说明](docs/database/README.md)、[迁移索引](docs/database/migrations/README.md) |
| 加改接口 | [接口约定](docs/api/README.md)、[前端 API 约定](frontend/src/api/README.md) |
| 改界面与交互 | [产品范围](frontend/PRODUCT.md)、[设计约定](frontend/DESIGN.md)、[创作反馈规则](docs/design/创作反馈规则.md) |
| 改成片编辑器 | [标准成片编辑器维护说明](docs/instruction/标准成片编辑器维护说明.md) |
| 碰无限画布 | [画布迁移边界](docs/instruction/画布迁移边界.md) |
| 部署 Agent、排查模型调用 | [Agent 模式部署与排障](docs/instruction/Agent模式部署与排障.md) |
| 查调用关系、反向依赖 | [代码索引](docs/instruction/代码索引.md)，优先于全库 grep |
| 确认创作模式边界 | [创作模式维护说明](docs/instruction/创作模式维护说明.md) |

历史计划与验收记录只作背景参考；当前行为以代码和上表维护文档为准。

## 文件存放规范

新增、移动、重命名文件前按此判定；发现现有文件不合规，在本次任务内归档到位再交付，不要只在回复里提醒。

### 根目录

| 类型 | 允许项 |
| --- | --- |
| 目录 | `frontend/`、`backend/`、`docs/`、`scripts/`、`docker/` |
| 文件 | `AGENTS.md`、`PRINCIPLES.md`、`README.md`、`compose.yaml`、`test-map.json`、`mcp.example.json`、`.gitignore`、`.gitattributes`、`.env.example` |
| MCP | `.mcp.json`、`.cursor/mcp.json`、`.codex/config.toml` 是 bootstrap 生成的本机绝对路径，不提交；共享模板为 `mcp.example.json` |

- 根目录**只放 `AGENTS.md`、`PRINCIPLES.md`、`README.md` 三个 md**，其余说明进 `docs/instruction/`。
- 禁止 `.log`、`.txt`、`.html`、截图、导出产物和一次性脚本：临时产物写 `.runtime/`（已忽略），需长期保留的进 `docs/data/`。
- `docker/` 是 `compose.yaml` 的挂载配置，留在根目录。以 `.` 开头的目录默认不提交，需共享时在 `.gitignore` 用 `!` 反向声明并在此登记。

### docs

`docs/` 根目录不允许任何文件：

| 目录 | 放什么 | 命名 |
| --- | --- | --- |
| `instruction/` | 系统怎么运作、怎么开发测试部署 | 中文名 |
| `api/` | 接口契约，可按模块建子目录 | `README.md` 总入口 |
| `database/` | `schema/` 全量建表、`migrations/YYYY-MM-DD-功能/` 增量、`README.md` 表说明 | SQL 保留英文 |
| `design/` | 设计方案、原型、视觉稿 | `YYYY-MM-DD-中文主题.md` |
| `plans/` | 计划与执行记录，按 `YYYY-MM/` 归档 | `YYYY-MM-DD-中文主题.md` |
| `reviews/` | 验收、复盘、审计 | `YYYY-MM-DD-中文主题.md` |
| `data/` | 素材、截图、对照数据、原型 | 原始名或中文名 |
| `skills/` | 第三方方法论归档 | 维持上游结构 |

- 新文档按序归位：本次计划 → `plans/`；长期设计 → `design/`；做完的验收 → `reviews/`；现状说明 → `instruction/`；素材 → `data/`；接口 → `api/`；表结构 → `database/`。
- [计划总纲](docs/plans/总纲.md)是 `plans/` 根唯一允许的文件，新增计划必须加一行；状态只用 `草案`、`进行中`、`已完成`、`已废弃`、`已回滚`。
- 固定路径不改名：`docs/database/schema/schema.mysql8.sql`（由 `export_schema.py` 写入）、`docs/instruction/测试文件清单.md`（由 `test.mjs --inventory` 生成）、`test-map.json`。
- `docs/skills/` 整目录例外：第三方资产，保留其 `UPSTREAM.md` 与许可文件，不套用上述命名。

## 代码变更与文档同步

维护文档只要没人改就会过期。改完代码按下表找到对应文档，改完执行 `node scripts/check-docs.mjs`：

| 改了 | 必须同步 |
| --- | --- |
| 增删 `api/v1` 路由模块、改路径或契约 | `docs/api/README.md`、对应模块页、`项目概览` 的接口分组、`frontend/src/api`（DTO 变化时） |
| ORM 模型或表结构 | 重跑 `scripts/export_schema.py` 生成 `docs/database/schema/schema.mysql8.sql`、新增 `docs/database/migrations/<日期-功能>/`、两份数据库 README |
| 前端页面或 `features` 目录增删 | `docs/instruction/项目概览.md` 的代码地图 |
| npm 脚本、启动角色、`test.mjs` 参数 | `开发运行与验证`、`按功能执行测试` |
| 环境变量或配置项 | `backend/.env.example`、`开发运行与验证`；涉及部署的再加 `Agent 模式部署与排障` |
| 内置方法包或 `catalog.json` | `内置创作方法维护` |
| 功能退役或改名 | 相关 `instruction` 文档与 `docs/api`，旧内容不改写，补历史横幅 |
| 用户可见行为变化 | `README.md`、`系统架构与数据流` |

- `check-docs` 只判定机器能判的部分：链接与路径是否存在、当前表数量、路由模块清单、npm 脚本与启动角色。语义过期（描述对不对）仍需人读。
- 历史文档（`plans/`、`reviews/`、`design/`）不回填；被新改动牵动时补说明，不改写原记录。

## 协作与提交

- 中文回复与文档；代码标识符、接口字段和命令沿用现有命名。
- 动手前先看 `git status --short`，并读相关模块、调用链和测试；保留他人未提交改动。改动超过 3 个文件或跨层（API → Service → DAO → ORM + 前端 DTO）时，先说明改哪些文件、为什么。
- 不确定的 API 或库用法查文档，不凭记忆臆造。
- 改完先用 `node scripts/test.mjs --changed --dry-run` 预览影响范围，再跑对应级别；纯文档变更核对内容、命令和链接即可。
- 代码改动后执行 `node scripts/check-docs.mjs`，按上一节同步受影响的维护文档。
- 提交前执行 `node scripts/check-quality.mjs`；禁令、结构门禁与基线规则见[代码质量规范](docs/instruction/代码质量规范.md)。
- 报错贴关键 stack trace 和复现步骤，不要只说"报错了"。
- 调试输出走项目统一 logger，不留 `console.log`/`print`。
- 报告验证结果要实事求是：测试替身、跳过或未执行的部分，不得描述为真实供应商验收。
- 保留 `uv.lock`、`package-lock.json`、`.env.example`，依赖变更同步锁文件；不提交凭据、依赖目录、缓存和构建产物。
- 提交用 Conventional Commits（`feat:`、`fix:`、`docs:`），一个 commit 一件事；不主动 `git commit`，除非用户要求。
- 子目录如有 `AGENTS.md`，同时遵循其针对该目录的约定。

## 完成定义

- [ ] 相关验证已实际执行并通过；跳过或未运行的部分已在总结中声明
- [ ] 受影响的维护文档已同步，`node scripts/check-docs.mjs` 通过
- [ ] 无调试残留、无敏感信息、无未声明的新依赖
- [ ] 用户能从总结知道：改了什么、怎么验证的、还有什么没验证
