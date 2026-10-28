# Agent 模式部署与排障

2026-10-26 起新 V3 任务使用 [Harness 运行机制](AI创作助手Harness运行机制.md)：LangGraph 控制工作流、LangChain 单次决策，工具自主选择，图片／方法按需加载。2026-10-27 起新任务另开放边界停止，改向与恢复中断仍不支持；下文旧句式授权和中断流程仅适用于冻结原引擎的历史任务。新增部署须先执行 [图检查点迁移](../database/migrations/2026-10-26-agent-harness/README.md) 与 [交付要求增量](../database/migrations/2026-10-27-agent-contract/README.md)，API、scheduler、Agent Worker 和前端同版升级。

Harness 可靠执行与上下文优化沿用 Task JSON 和既有账本。[交付要求增量](../database/migrations/2026-10-27-agent-contract/README.md)新增 `agent_task_requirements` 一张表，并为 `agent_operations` 增加两列及一个索引；任务创建时冻结 `agent_policy` 中的预算、停滞窗口和停止策略，使用 `AGENT_RUN_*`、`AGENT_PROGRESS_*`、`AGENT_REQUIREMENT_ATTEMPT_CAP`、`AGENT_REVIEWER_MODE`、`AGENT_STOP_AT_BOUNDARY` 环境变量。收尾增加[记忆抽取重试终态约束](../database/migrations/2026-10-26-agent-memory-retry/README.md)，已有库须先升级，允许运行时既有的 `abandoned` 状态。当前请求使用工具 v4、提示词 `assistant-harness-v10`，完成验收统一按需求表和证据包执行。API 与 Agent Worker 必须同版，以支持按需能力组、服务端交付要求 draft、发布守卫、证据包验收、工作笔记、Skill 释放和模型容量字段。候选最终答复在独立检查通过后发布，检查也会产生模型用量。

出现 `agent_progress_stalled` 时查看最近工具错误、重复结果和 progress 提示；出现 `agent_completion_stalled` 时查看 completion_feedback 的具体缺项。两者保留成果并暂停，可补充必要资料后发送“继续”，不能当作任务累计次数耗尽。图片裁剪错误应返回真实宽高及结构化失败，不应显示已看过像素。排查长任务数据库排序错误时，历史查询必须先筛有界 ID、再读 JSON；不要在 ID 排序阶段加入大 JSON 的选择或筛选。容量使用声明／平台默认值，实际 input_tokens、cached_tokens 和估算指标应分别查看，声明不等于供应商实测。

Skill 包接口与 AI 配置管理页支持在线新建、Markdown／ZIP 预览、不可变正文与资料、复制／导出及独立启停／自动设置。V3 的 `search_skills/load_skill/read_skill_resource` 已接入任务冻结、恢复和可见轨迹，输入区使用锚定 Skill 列表搜索、加载更多及直接多选，支持 `#` 同名身份选择；首批九类能力已适配为 12 个独立内置包。005 必须随 API 与 Worker 同版升级；在 `short_drama` 已真实执行并回填内置八个历史版本，当前 readiness 为 `ready`，12 个新内容版本也已显式发布。资料先写 MinIO 并核验，再以短 SQL 事务发布；全部历史版本的对象引用保留，未引用资料可用[显式回收脚本](内置创作方法维护.md#未引用资料回收)清理。自由文本的广义语义消歧与真实模型效果仍待验收。运行时沿用任务 JSON 与操作账本，无额外 DDL；配额和字段见[Skill API](../api/agent.md#内置与个人-skill-管理)与[任务方法工具](../api/assistant.md#任务创作方法与资料)。

Agent 与原生生成共用 MySQL、RabbitMQ、Celery 和 MinIO。当前入口是按本人项目隔离的 V3 会话与逻辑任务，共二十八张 Agent 表（含任务四表、记忆六表、私有附件、个人 Skill、不可变 Skill 版本及个人设置、账号助手偏好、私人媒体请求、讨论修订及批次撤销回执）及独立 Agent Worker。scheduler 负责运行与记忆整理的可靠发布／恢复、原生结果归档、V3 媒体批次结算及删除清理；前端不展示记忆管理或维护状态。Compose 只启动外部依赖，不启动 Python API、scheduler 或 Worker。

当前 V3 开放目标／主动主体／操作回执、本任务新建成果读取、四个记忆读取工具、四个显式记忆写工具、三个 Skill 工具、有界标准／画布结构批次、标准素材／分镜图片、普通／原生有声分镜视频、角色音色、旧声音文档台词配音及画布图片／视频／音频批次；标准素材／分镜、画布节点／连线、旧声音文本、主图及实际已采用媒体的批次撤销已接入明确预览与选择。写工具按真实用户指令裁剪，业务效果、版本和持久回执同事务结算；阶段摘要保留有界父摘要索引及来源链。标准批次复用已有素材、分镜、原生对白及声音文档表，不增加 DDL。原生分镜对白仍要求同时启用 `NATIVE_VIDEO_ENABLED` 与 `AUDIO_PRODUCTION_ENABLED`，结构工具只保存文本，不启动音频生成。画布结构批次复用原生四类节点、增量合并、保存历史和私人参数表，独立校验共享／私人版本，不新增 DDL 或发起媒体生成；明确媒体指令另走生成及完整结算。新建成果按真实回执读取保存快照，发送前复核可用性，新任务不继承正文。旧 V1 创作计划与候选、V2 项目纯聊天为兼容接口，不能据此宣称 V3 已开放同类能力。结构工具的测试替身不代表真实模型执行验收。实际范围见[助手 API](../api/assistant.md)与[实施记录](../plans/2026-10/2026-10-21-AI创作助手实施与验证记录.md)。

标准图片组增加冻结目录读取和明确主图设置，复用原生三版本、历史组激活及素材／分镜采用规则；私人提示词和签名地址不进入工具结果。标准图片生成使用私人请求账本，全批预占数量、逐项本地受理、原生图片 Worker 生成及 scheduler 完整结算。有效主图按稳定序号自动采用，其他图片和旧主图留历史；部分失败或采用冲突暂停，明确继续只处理缺失数量。停止／改向／删除须等全部请求和回填结果明确，受理不明保留原身份及预占。MySQL 与浏览器替身验证不能代替真实供应商或 MinIO 验收。

`read_image_content` 复用受管附件处理，在事务外读取获准图位或本任务真实生成图片，校验文件后内联发送，不能请求任意远程 URL。API／Agent 使用同版工具契约和内部检查点，前端显示独立读取轨迹；无需新增表或 DDL。运行环境需能访问原媒体 MinIO，文本模型需实际支持当前协议的图片输入；本地 Chat／Responses 协议测试不代表真实模型已支持视觉。读取失败保留任务与原身份，来源失效不自动换图。

标准普通分镜视频沿用已保存时长与分辨率，当前主图须主动选入或为本任务真实生成结果；不会自动提交旧正文、私人提示词或未选入角色。普通视频指令在原生有声模式下拒绝受理；明确有声指令另行校验可信对白和全部角色音色。助手任务永久归档前额外用 FFprobe 检查真实视频轨道、尺寸和时长，scheduler 复用原生采用及旧视频历史规则；来源变化保留产物并提示冲突。部署需同时提供 FFmpeg／ffprobe，不能用容器头检查替代轨道检查。

角色音色复用原生 `character_voice_design` 任务及锁内采用，需同时开启 `NATIVE_VIDEO_ENABLED/AUDIO_PRODUCTION_ENABLED`，配置支持音色设计的本人音频模型，并启动 audio Worker 与 scheduler。角色素材版本和独立音色版本分别冻结；旧引用缺少可信音色身份时要求主动刷新。Worker 领取及最终外发前再次核对角色／音色与功能开关，已外发请求继续归档，采用冲突保留样音。前端通过本人原生候选接口匹配生成任务和媒体身份试听，不改当前音色。无需新增 DDL，API、scheduler、Agent、audio Worker 和前端须同步更新；测试样音不代表真实音频供应商验收。

原生有声分镜视频另需用户明确要求有声生成、已保存原生模式、已确认对白，以及所有说话角色的可信音色引用。API、Agent、video Worker 与 scheduler 必须同版，Worker 使用实际运行配置复核双开关和冻结来源；原生请求保留 `native_speech` 及样音 checksum 身份。FFmpeg／ffprobe 检查真实音轨和可听信号，对白镜头静音候选不自动采用；无对白镜头也须已确认。技术检查不代替音色、表演或真实供应商验收。

旧声音文档台词配音要求 `AUDIO_PRODUCTION_ENABLED=true`、已保存的声音草稿和本人配音模型，以及明确选入或本任务新建的真实台词；启用原生有声视频模式的项目不接受独立配音。必须同版部署 API、Agent、audio Worker、scheduler 与前端：冻结台词身份、声音／画面双版本和时间线摘要，Worker 领取及最终外发复核，归档后通过原生锁内采用更新多轨声音并保留旧候选。总开关关闭不阻止已外发请求结算，原生功能开关关闭仍可能阻止采用。无需新表或 DDL；真实供应商与音频内容质量另行验收。

声音文本增量创建／修改同样要求 `AUDIO_PRODUCTION_ENABLED=true` 和已保存的成片草稿。API 在真实输入中仅冻结声音／画面版本，Agent 使用该指令目录；升级前缺少冻结版本的创建指令需重新明确发送，不能由模型自报当前版本补齐。适配层保留整份现有文档，在原生保存事务内只合并获准台词字段，不序列化未选正文或媒体地址给模型。可按真实回执继续读取、编辑或明确要求生成本任务新建台词配音；API、Agent、scheduler 与前端须同版，无新增 DDL。

AI 配置 → 创作助手提供本人媒体总开关，缺少设置默认开启，保存过的关闭不因升级重置。`get_generation_policy` 只读实时策略，不能由 Agent 打开开关；模型发送前版本变化会重新准备决策且不计调用。媒体在受理、Worker 领取和供应商提交前复核实时开关／本人权限，已外发继续结算。开启时 `available_media` 返回本人已配置的 image/video/audio，执行端另按目标模式限定：标准当前开放图片、普通／原生有声分镜视频、角色音色和旧声音文档台词配音，画布开放三类媒体。003 的 `agent_preferences` 与 004 的 `agent_media_requests` 必须随 API、scheduler、Agent 和对应 image/video/audio Worker 同版部署，不能只升级设置页面。画布复用原生任务、素材交付及绑定，必须保留 scheduler 完整结算；只启动 API 或 Agent Worker 不会完成媒体回填。

部署本轮项目助手时同步更新 API、Agent Worker、宿主及画布前端：会话创建和列表携带项目，任务自动绑定所属项目的保存目标与设定，画布新任务另指定当前画布，补充保持原目标。任务设置、上下文和记忆管理入口已移除，引用、附件选择及发送前的版本核对保留。输入字段复用既有 JSON，会话 CHECK 另需下文项目增量；混用旧 Worker 不能保证项目归属和原目标冻结。

批次撤销需要 007／008、API、scheduler、相关 Worker 和前端同版升级。008 允许角色音色成对空绑定，首次采用撤销后保留递增版本行；旧绑定文件及生成历史继续保留。007 阶段已真实创建 `agent_batch_undos`，当时结构为 117 表／二十五张 Agent 表；加图检查点与交付要求表后为 119 表／二十八张 Agent 表，readiness `ready`、gaps=[]。原工具回执不可改写，模型和前端另读撤销回执；同会话活动执行及作品原生任务结束后才允许明确逆向。详见[007 说明](../database/migrations/2026-10-21-agent-system/README.md#批次撤销回执2026-10-22-接续)。

纯讨论历史修订需要 006、API、Agent Worker 与标准前端同步升级。消息带版本，重答使用原任务的新 attempt；Worker 必须保留工具禁用和旧回复原位替换边界，不能混用旧的单列 `trigger_message_id` 唯一键。实际 DDL、幂等复核及006 阶段的 116 表／二十四张 Agent 表证据见[006 说明](../database/migrations/2026-10-21-agent-system/README.md#纯讨论历史修订2026-10-22-接续)。

## 配置与数据库

以下命令均在 `backend/` 执行。先更新代码并执行 `uv sync --locked`；已有库先按[迁移索引](../database/migrations/README.md)补齐原有结构和归属。备份 MySQL、MinIO 及独立保存的原 `ENCRYPTION_KEY`，迁移前显式设置 `AGENT_ENABLED=false` 并停止相关 API、scheduler 与 Worker 写入。

```powershell
# 只读检查；未迁移时返回非零并列出结构缺口。
uv run python scripts/agent_migration.py --precheck
# 明确补齐缺少的 Agent 表，成功后再次检查。
uv run python scripts/agent_migration.py --apply
uv run python scripts/agent_migration.py --precheck
```

已有七表的旧库需要补私有候选迁移及附件/Skill 两表，执行顺序与回填见[本次迁移说明](../database/migrations/2026-10-03-private-candidates/README.md)。新库不执行旧库 ALTER；Agent `--apply` 可补缺表，不能代替私有候选列和历史发布状态的回填。附件格式、输入能力和 Skill 版本规则见[Agent API](../api/agent.md)。

已有九表库还须执行[会话范围增量迁移](../database/migrations/2026-10-04-agent-creation-scope/README.md)：

```powershell
uv run python scripts/agent_scope_migration.py --precheck
uv run python scripts/agent_scope_migration.py --apply
uv run python scripts/agent_migration.py --precheck
```

本增量增加会话 stage、subject_type、subject_id、task_type、scope_version 及索引/约束，并将 Run 的活动会话唯一索引替换为普通索引以支持排队；实际执行仍由项目/会话锁和串行调度保障。保留旧记录为未分类只读，不推测对象归属。完整新库 SQL 已含这些定义，不重复执行 ALTER。

上述九表／范围迁移是旧库升级的历史前置步骤。当前代码的 readiness 还要求[全局任务与记忆迁移](../database/migrations/2026-10-21-agent-system/README.md)与[项目会话约束增量](../database/migrations/2026-10-22-project-assistant/README.md)。先执行缺失的 Agent 001–009，再执行项目增量中的单条 ALTER，最后 precheck；完整新库无需重复执行增量：

```powershell
# 在原有基线齐全后，逐项执行缺失的 001／002／003／004／005／006／007／008 增量，并核对完整 readiness。
uv run python -m scripts.agent_system_migration --apply
uv run python scripts/agent_migration.py --precheck
```

此增量增加四张任务表、六张记忆表、账号助手偏好、私人媒体请求、Skill 版本／个人设置、讨论修订及批次撤销回执表、消息／Run 的 task_id、消息版本、运行修订身份与会话摘要字段，解除 V3 会话项目强绑定，并允许角色音色成对空绑定；保留旧记录和授权。记忆索引使用 MySQL ngram；本机 `@@ngram_token_size=2`，其他环境须实际核对。完整新库 [schema.mysql8.sql](../database/schema/schema.mysql8.sql) 当前为 119 张表，包含全部二十八张 Agent 表，直接做 precheck 即可，不重复执行旧库 ALTER。

2026-10-27 的 [Agent 加固增量](../database/migrations/2026-10-27-agent-contract/README.md)新增 `agent_task_requirements`，并给 `agent_operations` 加 `result_digest/evidence_kind` 与索引；这是 119 表／二十八张 Agent 表的来源。已有库须停写后执行 `010-task-requirements.sql` 再 `agent_migration.py --apply`，新库直接用完整 SQL。升级前的任务没有 `agent_policy`，继续按旧契约与旧预算解释；新任务冻结契约、预算、停滞窗口、验收与停止策略，因此 API、scheduler、Agent Worker 与前端必须同版升级。

005 就绪后，另行发布当前部署携带的内置内容；不随应用启动或迁移自动发布：

```powershell
# 工作目录 backend；不带 --apply 只验证包并预览。
uv run python scripts/publish_builtin_skills.py
uv run python scripts/publish_builtin_skills.py --apply
```

发布器先验证全部包、保存并读回 MinIO 资料，再用短 SQL 事务发布整个批次。相同身份／版本／校验重跑复用；修改内容必须增加内容版本，同版本改写会拒绝且整批回滚。旧版本和个人启停设置保留；已运行任务继续读取冻结版本。wheel 携带全部资源，运行任务不依赖部署目录中的旧文件。身份、许可及维护规则见[内置方法维护](内置创作方法维护.md)。

应用启动不会自动迁移。MySQL DDL 会隐式提交；各迁移按自身检查规则重入，不删除历史数据，也不通过重建表修复不兼容结构。遇到 `partial` 应核对 precheck 缺口与对应 DDL，保留原数据后处理，不能仅凭同名表存在判定就绪。本机业务库的实际 DDL 证据见迁移说明，不能代替其他部署环境的升级。

| 设置 | 作用 |
| --- | --- |
| `AGENT_ENABLED` | 默认 `true`；启动要求完整二十八张 Agent 表 readiness 为 `ready`，且 `AUTH_ENABLED=true`；显式 `false` 关闭新决策和记忆模型整理，已开始媒体结算及删除清理仍由 scheduler 执行 |
| `AGENT_LEASE_SECONDS` | 默认 180 秒，允许 150–3600；执行与恢复租约 |
| `AGENT_POLL_SECONDS` | 默认 5 秒，允许 3–60；scheduler 收集原生任务结果的间隔 |
| `GENERATION_QUEUE_NAMESPACE` | 默认 `short_drama`；所有进程保持一致，不同数据库使用不同 namespace |
| `SNOWFLAKE_WORKER_ID` | 每个同时运行的后端进程唯一；下例 Agent 使用 7 |

设置在进程启动时读取。完成结构迁移后准备 Agent Worker，确认 `AGENT_ENABLED` 未被显式关闭（或设置为 `true`），并重启 API、scheduler 及所有已配置 Worker；不能只刷新页面或只重启 API。显式关闭时原生模式不要求 Agent 表；若结构完整，scheduler 仍归档旧结果和执行删除清理。默认开启不会自动验证模型或发出生成任务。

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

Agent 队列始终是 `<GENERATION_QUEUE_NAMESPACE>.tasks.agent`，默认也保留 `short_drama.` 前缀；自定义 `studio_dev` 时用 `studio_dev.tasks.agent`。记忆摘要和抽取复用该队列、独立持久作业与租约；凭据在实际发送前解析，事务外调用模型。删除 purge 由 scheduler 本地执行，不需要模型或 RabbitMQ。它与默认原生队列 `tasks.ai.text/image/video/audio` 的命名规则不同。脚本按 Settings 自动解析。线程共用进程内雪花生成器，不要直接改成共用节点号的 prefork 或多进程；重启前确认旧进程已结束。API、scheduler、原生 Worker 的启动见[开发指南](开发运行与验证.md#启动后端)。

记忆整理的完整回执优先本地重放，发送中或受理不明不换键重发。内部维护接口只允许对明确失败记录安排一个独立后继；来源、摘要代次及原模型配置须仍有效，原失败与供应商回执保留。长任务约束整理使用同队列的 `task_state` 作业，需补齐 [009 迁移](../database/migrations/2026-10-21-agent-system/009-task-compaction.sql)，仅扩展作业类型 CHECK；当前业务库 `short_drama` 已执行，009 阶段为117表、Agent25表；当前118表、Agent27表。作业冻结旧输入前缀，近期补充保留原文；原始前缀变化及父约束版本冲突会阻止激活，完成整理不恢复停止任务。抽取同键冲突保留为后台候选，版本和来源再校验后才应用，不能自动覆盖现有事实；前端不显示澄清或维护入口。总输入在冻结和实际发送前检查估算预算，超限先等待有效在途约束整理，无可推进作业或整理失败才以 `paused_budget` 保留输入暂停；平台估算不代表供应商 tokenizer 或模型窗口实测。

## 模型与流式代理

当前创作界面无手动能力验证和输入能力声明入口，选择模型不调用供应商，未验证配置也能发送。兼容旧 `/verify` 接口，但验证证据不再是准入条件。未知能力在用户实际任务中尝试；协议/能力不匹配写入中文对话反馈，认证、限流和受理不明分别反馈，不自动更换模型或丢附件。Agent 文本工具协议与原生图片/视频供应商兼容性需分别验收；自动测试、成功构建或 capability cache 不能替代真实模型结果。

既有特定配置的真实文本、图片和视频证据见[验收记录](../reviews/Agent模式实施与真实模型验收.md)，该历史记录不能证明当前新增配置或本次范围/队列改动已完成真实供应商验收。

对 Agent SSE 路由关闭代理缓冲、响应缓存和包含请求/响应正文的日志。后端返回 `Cache-Control: no-store`、`X-Accel-Buffering: no`，每 15 秒发送 heartbeat；代理读取空闲超时应大于 15 秒，并允许长连接。验证刷新/重连能按事件序号补读，登出或账户停用后不再显示新的私有事件。候选、生成历史、对话、记忆及模型配置仅本人可见，作品按所有者独占；附件与 Skill 归属个人，作品权限失效不能继续读取原主体资料。

V3 的事件游标越界或连续记录缺失会要求重新同步：初次 HTTP 409，流中 `resync`；客户端先重读一致快照再补文本。暂时数据库故障使用 `retry` 并保留客户端历史，认证／权限／删除等访问终止才用 `access-ended` 清空私人状态。三种控制帧不推进业务游标，不触发重新生成。不要提前清理活动回复的 delta；已丢失的模型输出不能由客户端凭空恢复，事件留存与原始消息归档须配套。

## 只读观测与故障处理

```powershell
uv run python scripts/check_generation_infra.py
```

检查显示 Agent 开关、认证开关、完整结构状态、聚合 Run/Turn 状态、unknown 数量、过期活动租约和原生等待数量，并对当前 namespace 的原生、渲染、Agent 队列做 RabbitMQ passive 查询。它不会创建队列、修改数据或调用模型；未迁移且关闭 Agent 时会跳过 Agent 聚合查询。输出仅含结构与数量，不含连接串、凭据、私有消息、工具参数或供应商正文。Celery remote control 已关闭，不能以 `celery inspect` 无响应判定 Worker 故障。记忆整理覆盖范围、失败及受理不明在本人会话记忆详情查看，原始供应商报文不通过此入口暴露。

| 现象 | 检查与处理 |
| --- | --- |
| Agent 一直排队 | 检查 scheduler、Agent 队列消费者、namespace 与雪花节点；再检查 Run 聚合和租约 |
| `waiting_generation` 不结束 | 检查对应原生 Worker、scheduler、任务保存状态与 MinIO；已受理任务可能仍在运行 |
| 旧 V1 `waiting_review` | 等待本人批准历史具体计划；修订后旧批准失效，补充不自动批准；不是 V3 工具授权方式 |
| `agent_acceptance_unknown` / unknown Turn | 保留记录，结合供应商结果人工核对；不得将 sent/unknown 改回 prepared、删除记录、整体重放 broker 消息或自动重发 |
| 媒体失败需要再生成 | 通过新的明确付费任务或审核计划重试；Agent 管理任务不能用原生通用重试绕过授权和额度 |
| 采用返回版本冲突 | 保留当前作品，重新读取候选与版本核对；不得自动覆盖或唤醒其他 Run |
| 启用后启动失败 | 先核对 `AUTH_ENABLED`、Agent precheck、统一代码/依赖版本；重启不能修复缺失结构 |

有完整持久化响应的执行可由 scheduler 重放安全的本地决策/工具步骤；已发送但缺少持久化响应的模型段会进入 unknown，恢复不会再次发送。日志保留安全错误码及必要状态，不记录模型请求 URL、正文、私有工具参数、API Key 或连接串。

## 关闭与回退

将 `AGENT_ENABLED=false`，重启 API、scheduler 和所有已配置 Worker，使新决策、记忆模型整理与新原生提交停止。已受理的原生媒体仍可由原生 Worker 继续轮询、保存，scheduler 在完整 Agent 表就绪时仍收集成果并执行已删除会话的 purge；保留这些进程直到归档和清理完成。本人候选仍须作者与作品双重校验，已采用作品继续按作品权限访问，原生模式继续可用。关闭开关不撤回已被供应商受理的付费请求。

回退保留全部表、候选、任务和记录，不删除队列或重置 sent 状态，不用旧版无鉴权应用访问当前多用户库。数据库与 MinIO 需要恢复时采用同一时间点的备份，并保留原加密主密钥。在线原文清理回执不表示备份或对象版本已同步物理擦除，备份保留由部署策略另行管理。
