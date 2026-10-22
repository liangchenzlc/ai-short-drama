# 全局 Agent 会话、任务与记忆迁移

当前还包括 [009-task-compaction.sql](009-task-compaction.sql)：扩展 `agent_memory_jobs.ck_agent_memory_job_kind`，允许 `task_state` 约束整理作业，无新增表或列。旧库在 001–008 后补齐 009，并同步升级 API、scheduler、Agent Worker 与前端；新库使用完整 SQL。

当前还包括 [008-media-undo.sql](008-media-undo.sql)：允许角色音色绑定成对为空，保留首次采用撤销后的递增版本行。旧库在 001–007 后补齐 008，并同步升级 API、scheduler、相关 Worker 与前端；新库使用完整 SQL。

当前还包括 [007-batch-undo.sql](007-batch-undo.sql)：新增本人独占、请求幂等且不可任意改写的 `agent_batch_undos`，保存原操作身份、明确选择的范围及逆向差异。旧库在 001–006 后补齐 007，并同步升级 API、Worker 与前端；新库使用完整 SQL。

当前还包括 [006-discussion-revisions.sql](006-discussion-revisions.sql)：增加消息版本、同消息多次运行的修订身份及不可变讨论修订快照。旧库在 001–005 后补齐 006，并同步升级 API、Agent Worker 和前端；新库使用完整 SQL。

当前还包括 [005-skill-packages.sql](005-skill-packages.sql)：创建不可变 `agent_skill_versions` 与本人 `agent_skill_settings`，给 `agent_skills` 增加 `create_key/create_hash`、账号复合唯一键及配对 CHECK。先完成 001–004，再执行缺少的 005；新库仍只用完整 SQL。迁移脚本会为现有个人当前版本、个人设置及内置版本作幂等回填，保留旧 ID、内容版本及已保存的个人设置。

本次增量解除 V3 会话的项目强绑定，新增逻辑任务、不可变任务输入、有序控制及操作回执四张表，并为消息和运行增加可空 `task_id`。旧 V0/V1/V2 的项目字段、消息、Run、候选及授权保持原样，不能将旧授权映射为新任务许可。

新库使用[完整 SQL](../../../database/schema/schema.mysql8.sql)；旧库按缺少部分执行 [001-global-tasks.sql](001-global-tasks.sql)、[002-memory.sql](002-memory.sql)、[003-preferences.sql](003-preferences.sql) 和 [004-media-requests.sql](004-media-requests.sql)。001 增加任务／输入／控制／操作四表，放宽会话项目列与 CHECK、增加账号索引；002 增加六张记忆表、会话摘要指针／覆盖位置／代次及复合外键；003 增加账号助手偏好表；004 增加私人媒体请求账本。增量均不删除或合并作品及历史。MySQL DDL 隐式提交，部署时应核对目标库并备份，协调 API、scheduler 和 Worker 升级。

002 包含 `agent_memory_items`、`agent_memory_revisions`、`agent_memory_sources`、`agent_memory_forget_rules`、`agent_conversation_summaries`、`agent_memory_jobs`。记忆 scope CHECK 区分账号、会话和作品；内容身份、版本、来源、遗忘规则及后台租约均持久化。`idx_agent_memory_fulltext` 使用 MySQL `FULLTEXT ... WITH PARSER ngram`；短词和别名另有受限兜底，不依赖默认分词器覆盖所有中文输入。

在 `backend/` 执行 `uv run python -m scripts.agent_system_migration --apply`，脚本逐项检查已完成的 DDL，恢复部分执行；最后以 ORM 对照字段、CHECK、索引和外键，核对会话数量不变。结构不符必须向前修复，不通过重建表或删除业务记录处理。应用启动不运行迁移。

实际业务库的执行结果、表结构与隔离 MySQL 验证在[实施记录](../../../plans/2026-10/2026-10-21-AI创作助手实施与验证记录.md)中追加。这里的迁移脚本与 SQL 本身不证明其他部署环境已升级。

## 本机实际执行证据（2026-10-21）

已在当前配置的业务库 `short_drama` 执行迁移脚本的 `--apply` 路径，实际完成上述四张任务表、六张记忆表、`agent_messages/agent_runs.task_id`、会话可空项目及相关 CHECK／索引／外键，以及三个摘要字段和 `fk_agent_conversations_summary`。完整语句对应 001、002；不是仅生成 SQL 文件。

随后只读复核：业务库共 111 表，Agent readiness 为 `ready`、gaps 为空；`@@ngram_token_size=2`，会话数量迁移前后均为 0。隔离 MySQL 测试使用随机 `_test` 库，不将业务库用于测试。删除 purge 复用 `agent_memory_jobs.kind='purge'`，不增加新表；可空 JSON 的 `None` 改为 SQL NULL 是 ORM 序列化修正，不改变 DDL。

后台摘要／抽取复用 Agent 队列，清理作业由 scheduler 本地可靠执行。停止 Agent 生成不等于停止删除清理；备份及 MinIO 对象版本遵循既有保留周期，不能将清理回执描述为即时擦除全部备份。

## 账号助手偏好（2026-10-22 接续）

003 在当前业务库 `short_drama` 实际执行一条 `CREATE TABLE agent_preferences`，包含本人唯一键、账号外键、严格开关及正版本 CHECK；默认开关 1、保存版本 1。缺少设置的账号由服务端读取默认 `true/"0"`，不会因 GET 自动建行；已有关闭保持原值，迁移不运行重置 UPDATE。

执行后完整结构 112 表、二十张 Agent 表，readiness 为 `ready`、gaps 为空，会话数量仍为 0→0。完整 SQL 和旧 Agent 基线导出已同步。隔离 MySQL 验证与前端验证结果在实施记录单独追加；本条真实 DDL 不代表媒体供应商或后台派发已验收。

## 私人媒体请求（2026-10-22 接续）

004 已在当前业务库 `short_drama` 实际执行一条 `CREATE TABLE agent_media_requests`。表包含任务／操作／原生生成任务外键、操作内图项和原生任务唯一键、冻结目标／请求、数量预占、明确失败与受理不明状态、结果及时间。私人关联不进入共享生成记录；业务图片组继续由原生服务维护。

实际执行命令为 `python -m scripts.agent_system_migration --write --apply`（使用当前后端虚拟环境）。回执 `executed=["table:agent_media_requests"]`，会话 0→0、readiness 为 `ready`、gaps 为空；该阶段完整结构为 113 表、二十一张 Agent 表。已同步完整 SQL、004 和 Agent 基线导出。此 DDL 不调用模型、媒体供应商或 MinIO，也不能代替真实生成验收。

## Skill 内容包与个人设置（2026-10-22 接续）

005 已在当前业务库 `short_drama` 实际执行：两张 CREATE TABLE、`agent_skills` 两列和两个唯一键及配对 CHECK，并向前修复版本身份 CHECK 为 `CONCAT('', skill_id)`。ORM、完整 SQL、005 和旧 Agent 建表基线同步；没有通过放宽 readiness 掩盖 MySQL 反射差异。回填八个内置版本，原库没有个人 Skill，因此个人版本／设置回填均为零。再次执行 `python -m scripts.agent_system_migration --write --apply` 返回 `executed=[]`、回填均零，readiness 为 `ready`、gaps 为空，会话仍为 0→0；当前共 115 表、二十三张 Agent 表。

资料正文不存入目录行：版本 manifest 引用已保存、读回核验的不可变 MinIO 对象。MySQL 与 MinIO 不是一个原子事务，未引用对象回收尚未实现。随机 MySQL 库的十项专项及真实 MinIO 旧／新资料版本测试通过；没有模型或媒体供应商调用。详见实施记录中的独立报告。

## 纯讨论历史修订（2026-10-22 接续）

006 已在当前业务库 `short_drama` 实际执行：`agent_messages.row_version` 及正版本 CHECK、`CREATE TABLE agent_discussion_revisions`、`agent_runs.trigger_revision`、替换 `uk_agent_runs_trigger` 为 `(trigger_message_id,trigger_revision)` 及修订必须属于逻辑任务的 CHECK。普通发送修订值为 0；重答以修订请求 ID 区分同一来源的不同 attempt，不能沿用原单列唯一键。

修订表保存原请求键／哈希、来源消息版本、原回复快照及新运行身份，带会话内请求唯一键、复合消息归属和运行外键。业务读取按本人隔离，修订行不可任意更新；会话删除清理可擦除其内容。

实际执行回执分别为 `.runtime/assistant-history-migration.json` 和 `.runtime/assistant-history-migration-final.json`。再次执行 `python -m scripts.agent_system_migration --write --apply` 返回 `executed=[]`；回执 `.runtime/assistant-history-migration-idempotent.json`。会话均为 0→0，readiness `ready`、gaps=[]；完整结构 116 表、二十四张 Agent 表。完整 SQL 和 Agent 基线导出已同步核对。隔离 MySQL 用随机 `_test` 库，测试替身不代表真实模型验收。

## 批次撤销回执（2026-10-22 接续）

007 已在当前业务库 `short_drama` 实际执行一条 `CREATE TABLE agent_batch_undos`，含会话内请求唯一键、任务／会话复合归属外键、原操作外键、索引及 JSON 数组 CHECK。回执保存原请求键／哈希、预览哈希、用户明确选择、逆向前后差异与结果引用；原工具回执不改写。会话删除清理会擦除撤销正文，作品及仍被引用的媒体按原规则保留。

实际回执 `.runtime/assistant-undo-migration.json` 为 `executed=["table:agent_batch_undos"]`，会话 0→0、readiness `ready`、gaps=[]；再次执行的 `.runtime/assistant-undo-migration-idempotent.json` 为 `executed=[]`。完整结构已导出为 117 表、Agent 基线为 25 张表，两个导出一致性检查均通过。标准素材／分镜、画布节点／连线、旧声音文本与媒体采用逆向均复用本表；首次音色撤销另需下述 008。真实 DDL 不代表真实模型或供应商验收。

## 媒体采用回退（2026-10-22 接续）

008 已在当前业务库 `short_drama` 实际执行三条 ALTER：将 `character_voices.record_id`、`media_id` 改为可空，并增加 `ck_character_voice_binding_pair`，要求两列同时有值或同时为空。首次音色撤销清空绑定但不删除版本行，防止旧版本在再次采用时复用；普通读取返回真实递增版本且没有当前音色，原生有声生成仍要求有效绑定。

实际回执 `.runtime/assistant-media-undo-migration.json` 包含两项 `nullable` 和一项 `add_check`；再次执行 `.runtime/assistant-media-undo-migration-idempotent.json` 为 `executed=[]`。会话 0→0，readiness `ready`、gaps=[]。完整结构仍为 117 表、Agent 基线 25 表，ORM、完整 SQL、008 及两个导出一致性检查均已同步。媒体文件与历史候选保留，撤销仅恢复经过版本核对的绑定／轨道；隔离测试及真实 DDL 均不能代替供应商验收。

## 长任务有效约束整理（2026-10-22 接续）

009 已在当前业务库 `short_drama` 实际执行一条 ALTER：替换 `agent_memory_jobs.ck_agent_memory_job_kind`，允许 `summary/extract/task_state/purge`，保留消息范围约束。实际回执 `.runtime/assistant-task-compaction-migration.json` 为 `executed=["check:agent_memory_jobs.ck_agent_memory_job_kind"]`，会话0→0、readiness `ready`、gaps=[]；结构仍为117表、Agent25表。 再次执行 `.runtime/assistant-task-compaction-migration-idempotent.json` 为 `executed=[]`；完整 SQL 与25表 Agent 基线均已重新导出并通过一致性检查。

任务约束写入 `agent_tasks.effective_state.input_compaction`，含作业身份、覆盖输入修订及有界事实。作业 JSON 保存原始前缀哈希、父作业身份／覆盖修订、新来源与模型；不改变控制状态、工具权限或原始消息。每次注入及结算核对整个前缀，纠正／遗忘不能由旧父事实复活；会话删除沿用现有任务 JSON 和全部整理作业清理。真实模型约束保真质量须单独验收。
