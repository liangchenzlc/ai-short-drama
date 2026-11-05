# 项目结构与文件存放

> 本文件回答两件事：**去哪读**、**新文件放哪**。其余实现细节直接读代码。

## 文件地图

| 要做的事 | 先读 |
| --- | --- |
| 加改接口 | [接口约定](docs/api/README.md)、[前端 API 约定](frontend/src/api/README.md) |
| 改表结构、写增量迁移 | [表设计说明](docs/database/README.md)、[迁移索引](docs/database/migrations/README.md) |
| 改界面与交互 | [核心规则 · 前端设计原则](AGENTS.md) |
| 查历史决策与验收 | `docs/design/`、`docs/plans/`、`docs/reviews/`，只作背景参考，不代表当前行为 |

契约之外的问题直接读代码。项目不再维护"系统怎么跑、各层怎么分、某功能怎么实现"的说明文档；需要时看 `backend/src`、`frontend/src` 与测试。

## 文件存放规范

新增、移动、重命名文件前按此判定；发现现有文件不合规，在本次任务内归档到位再交付，不要只在回复里提醒。

### 根目录

| 类型 | 允许项 |
| --- | --- |
| 目录 | `frontend/`、`backend/`、`docs/`、`scripts/`、`docker/`、`skills/` |
| 文件 | `AGENTS.md`、`README.md`、`STRUCTURE.md`、`compose.yaml`、`test-map.json`、`mcp.example.json`、`.gitignore`、`.gitattributes`、`.env.example` |
| MCP | `.mcp.json`、`.cursor` 内的 `mcp.json`、`.codex` 内的 `config.toml` 是 bootstrap 生成的本机绝对路径，不提交；共享模板为 `mcp.example.json` |

- 根目录**只放三份 md**，职责固定：`AGENTS.md` 协作入口、核心规则与完成定义，`README.md` 对外说明，`STRUCTURE.md` 文件地图与存放规范。其余说明进 `docs/` 对应目录，**不再新增第四份根目录 md**。
- 禁止 `.log`、`.txt`、`.html`、截图、导出产物和一次性脚本：临时产物写 `.runtime/`（已忽略），需长期保留的进 `docs/data/`。
- `docker/` 是 `compose.yaml` 的挂载配置，留在根目录。以 `.` 开头的目录默认不提交，需共享时在 `.gitignore` 用 `!` 反向声明并在此登记。
- `skills/` 放可被 Agent 加载的技能包，每个子目录一个 `SKILL.md`；第三方来源与许可记在 `skills/UPSTREAM.md`。它与 `docs/skills/` 不同：后者是第三方方法论归档，不参与技能加载。

### docs

`docs/` 根目录不允许任何文件：

| 目录 | 放什么 | 命名 |
| --- | --- | --- |
| `api/` | 接口契约，可按模块建子目录 | `README.md` 总入口 |
| `database/` | `schema/` 全量建表、`migrations/YYYY-MM-DD-功能/` 增量、`README.md` 表说明 | SQL 保留英文 |
| `design/` | 设计方案、原型、视觉稿 | `YYYY-MM-DD-中文主题.md` |
| `plans/` | 计划与执行记录，按 `YYYY-MM/` 归档 | `YYYY-MM-DD-中文主题.md` |
| `reviews/` | 验收、复盘、审计 | `YYYY-MM-DD-中文主题.md` |
| `data/` | 素材、截图、对照数据、原型 | 原始名或中文名 |
| `skills/` | 第三方方法论归档 | 维持上游结构 |

- 新文档按序归位：本次计划 → `plans/`；长期设计 → `design/`；做完的验收 → `reviews/`；素材 → `data/`；接口 → `api/`；表结构 → `database/`。
- [计划总纲](docs/plans/总纲.md)是 `plans/` 根唯一允许的文件，新增计划必须加一行；状态只用 `草案`、`进行中`、`已完成`、`已废弃`、`已回滚`。
- 固定路径不改名：`docs/database/schema/schema.mysql8.sql`（由 `export_schema.py` 写入）、`test-map.json`。
- `docs/skills/` 整目录例外：第三方资产，保留其 `UPSTREAM.md` 与许可文件，不套用上述命名。
