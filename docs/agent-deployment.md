# Agent 模式部署与排障

Agent 模式与提示词模式共用 MySQL、RabbitMQ、Celery 和 MinIO。共九张 Agent 表（含私有附件和个人 Skill）与一个独立 Agent Worker；原生文本、图片、视频 Worker 仍执行各自任务，现有 scheduler 同时负责 Agent 发布、恢复与原生结果归档。Compose 只启动外部依赖，不启动 Python API、scheduler 或 Worker。

## 配置与数据库

以下命令均在 `backend/` 执行。先更新代码并执行 `uv sync --locked`；已有库先按[迁移索引](数据库模型/migrations/README.md)及[账户迁移](collaboration-deployment.md)补齐原有结构和归属。备份 MySQL、MinIO 及独立保存的原 `ENCRYPTION_KEY`，迁移前显式设置 `AGENT_ENABLED=false` 并停止相关 API、scheduler 与 Worker 写入。

```powershell
# 只读检查；未迁移时返回非零并列出结构缺口。
uv run python scripts/agent_migration.py --precheck
# 明确执行新增九表迁移，成功后再次检查。
uv run python scripts/agent_migration.py --apply
uv run python scripts/agent_migration.py --precheck
```

已有七表的旧库需要补私有候选迁移及附件/Skill 两表，执行顺序与回填见[本次迁移说明](数据库模型/migrations/2026-10-03-private-candidates/README.md)。新库不执行旧库 ALTER；Agent `--apply` 可补缺表，不能代替私有候选列和历史发布状态的回填。附件格式、输入能力和 Skill 版本规则见[Agent API](api/agent.md)。

完整新库 [schema.mysql8.sql](数据库模型/schema.mysql8.sql) 已含 52 张表，包括 Agent 九表，直接做 precheck 即可。应用启动不会自动迁移。MySQL DDL 会隐式提交；迁移可按已完成的表重入，不会修改已有表、归属或业务内容，也不会修复不兼容的既有 Agent 表定义。遇到 `partial` 应核对 precheck 缺口与对应 DDL，保留原数据后处理，不能仅凭同名表存在判定就绪。

| 设置 | 作用 |
| --- | --- |
| `AGENT_ENABLED` | 默认 `true`；启动要求九表 readiness 为 `ready`，且 `AUTH_ENABLED=true`；显式 `false` 可关闭执行 |
| `AGENT_LEASE_SECONDS` | 默认 180 秒，允许 150–3600；执行与恢复租约 |
| `AGENT_POLL_SECONDS` | 默认 5 秒，允许 3–60；scheduler 收集原生任务结果的间隔 |
| `GENERATION_QUEUE_NAMESPACE` | 默认 `short_drama`；所有进程保持一致，不同数据库使用不同 namespace |
| `SNOWFLAKE_WORKER_ID` | 每个同时运行的后端进程唯一；下例 Agent 使用 7 |

设置在进程启动时读取。完成结构迁移后准备 Agent Worker，确认 `AGENT_ENABLED` 未被显式关闭（或设置为 `true`），并重启 API、scheduler 及所有已配置 Worker；不能只刷新页面或只重启 API。显式关闭时提示词模式不要求 Agent 九表。默认开启仅改变执行开关，不会自动验证模型、发出生成任务或改变旧链接进入提示词模式的行为。

## 启动 Agent Worker

```powershell
# 先查看实际参数；不启动后台服务、不写 PID、不连接模型供应商。
scripts/start_generation.ps1 -Role agent -DryRun
# 后台启动，日志/PID 在 backend/.runtime/。
scripts/start_generation.ps1 -Role agent
```

也可在独立终端前台启动：

```powershell
$env:SNOWFLAKE_WORKER_ID = '7'
uv run celery -A short_drama.tasks.celery_app:app worker --pool=threads --concurrency=2 -Q short_drama.tasks.agent --hostname='agent@%h' --loglevel=WARNING
```

Agent 队列始终是 `<GENERATION_QUEUE_NAMESPACE>.tasks.agent`，默认也保留 `short_drama.` 前缀；自定义 `studio_dev` 时用 `studio_dev.tasks.agent`。它与默认原生队列 `tasks.ai.text/image/video/audio` 的命名规则不同。脚本按 Settings 自动解析。线程共用进程内雪花生成器，不要直接改成共用节点号的 prefork 或多进程；重启前确认旧进程已结束。API、scheduler、原生 Worker 的启动见[开发指南](development.md#启动后端)。

## 模型与流式代理

用户在个人模型设置中明确发起“验证 Agent 工具能力”。一次验证最多产生两次可能计费的文本模型请求；读取配置、打开页面和普通健康检查不会发起验证。证据绑定当前配置版本，修改地址、模型或凭据后必须重新验证。Agent 文本工具协议与原生图片/视频供应商兼容性需分别验收；自动测试、成功构建或 capability cache 不能替代真实模型结果。

本次实施已完成所选配置的真实文本、图片和视频联调，[验收记录](agent-verification.md)保留累计次数、采用与存储证据、实际视频元数据和环境范围。真实验证在隔离库进行，生产个人配置仍按上述流程显式校验后启用。

对 Agent SSE 路由关闭代理缓冲、响应缓存和包含请求/响应正文的日志。后端返回 `Cache-Control: no-store`、`X-Accel-Buffering: no`，每 15 秒发送 heartbeat；代理读取空闲超时应大于 15 秒，并允许长连接。验证刷新/重连能按事件序号补读，登出、成员移除或账户停用后不再显示新的私有事件。候选、生成历史、对话及模型配置仅本人可见，项目成员只共享明确采用的作品。附件与 Skill 归属个人，项目访问权撤销后不能继续读取该项目的私有对话资料。

## 只读观测与故障处理

```powershell
uv run python scripts/check_generation_infra.py
```

检查显示 Agent 开关、认证开关、九表结构状态、聚合 Run/Turn 状态、unknown 数量、过期活动租约和原生等待数量，并对当前 namespace 的原生、渲染、Agent 队列做 RabbitMQ passive 查询。它不会创建队列、修改数据或调用模型；未迁移且关闭 Agent 时会跳过 Agent 聚合查询。输出仅含结构与数量，不含连接串、凭据、私有消息、工具参数或供应商正文。Celery remote control 已关闭，不能以 `celery inspect` 无响应判定 Worker 故障。

| 现象 | 检查与处理 |
| --- | --- |
| Agent 一直排队 | 检查 scheduler、Agent 队列消费者、namespace 与雪花节点；再检查 Run 聚合和租约 |
| `waiting_generation` 不结束 | 检查对应原生 Worker、scheduler、任务保存状态与 MinIO；已受理任务可能仍在运行 |
| `waiting_review` | 等待对话拥有者批准当前版本的具体计划；继续对话不会自动批准 |
| `agent_acceptance_unknown` / unknown Turn | 保留记录，结合供应商结果人工核对；不得将 sent/unknown 改回 prepared、删除记录、整体重放 broker 消息或自动重发 |
| 媒体失败需要再生成 | 通过新的明确付费任务或审核计划重试；Agent 管理任务不能用原生通用重试绕过授权和额度 |
| 采用返回版本冲突 | 保留当前作品，重新读取候选与版本核对；不得自动覆盖或唤醒其他 Run |
| 启用后启动失败 | 先核对 `AUTH_ENABLED`、Agent precheck、统一代码/依赖版本；重启不能修复缺失结构 |

有完整持久化响应的执行可由 scheduler 重放安全的本地决策/工具步骤；已发送但缺少持久化响应的模型段会进入 unknown，恢复不会再次发送。日志保留安全错误码及必要状态，不记录模型请求 URL、正文、私有工具参数、API Key 或连接串。

## 关闭与回退

将 `AGENT_ENABLED=false`，重启 API、scheduler 和所有已配置 Worker，使新决策与新原生提交停止。已受理的原生媒体仍可由原生 Worker 继续轮询、保存，scheduler 在 Agent 表就绪时仍收集成果；保留这些进程直到归档完成。本人候选仍须作者与项目双重校验，已采用作品继续按项目权限访问，提示词模式继续可用。关闭开关不撤回已被供应商受理的付费请求。

回退保留九表、候选、任务和记录，不删除队列或重置 sent 状态，不用旧版无鉴权应用访问当前多用户库。数据库与 MinIO 需要恢复时采用同一时间点的备份，并保留原加密主密钥。
