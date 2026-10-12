# 无限画布基础存储迁移

新增 `projects.workspace_mode`，默认 `standard`，并增加限定取值的 CHECK。旧项目与现有分集数据保持原有语义。当前新增47张画布表，包含项目主画布设置、节点、连线、历史、写回执、私人投影与模型选择偏好、媒体引用、时间线、导演场景、资源上传/副本、删除回执、首次创建准备、绘图版本/历史绑定、私人素材库、本人项目文件夹、私人任务/产物、供应商正文增量、加密渠道目录以及本地渲染、媒体任务和生成媒体回收。基础表定义见 [001-canvas-core.sql](001-canvas-core.sql)，任务、文本流和模型目录分别见 [007](007-canvas-task-bindings.sql)、[008](008-canvas-text-stream.sql)、[009](009-canvas-model-catalog.sql)，本地任务见 [013](013-canvas-render-jobs.sql)、[014](014-canvas-local-jobs.sql)，生成媒体回收见 [015](015-canvas-generation-media-deletions.sql)，已有本地任务处置升级见 [016](016-canvas-local-disposal.sql)。

应用不自动执行 DDL。停止相关写入并确认可恢复备份后，在 `backend/` 执行：

```powershell
uv run python scripts/canvas_migration.py
uv run python scripts/canvas_migration.py --apply
uv run python scripts/canvas_migration.py
```

默认只预检；`--apply` 获取迁移锁后按缺项新增。脚本逐项核对字段、类型、默认值、主键、索引、外键、CHECK 表达式与执行状态、引擎和字符集；除下面明确识别的旧 copy CHECK 外，已存在但不匹配的结构会拒绝继续。允许中断后重新预检并安全重入，不把 MySQL DDL 当作可整体回滚的事务。较长数据库名使用固定长度锁名摘要，避免 MySQL 64 字符锁名称限制。

`--write-sql` 只同步增量 SQL，不执行 DDL。完整 SQL 使用 `scripts/export_schema.py` 同步。新库只使用[完整建库脚本](../../schema.mysql8.sql)；已使用本次早期结构的数据库再次执行 `--apply`，增加所缺的表和索引，并升级明确识别的旧 CHECK。当前完整 schema 共 99 张表。

资源副本增量见 [002-resource-copy.sql](002-resource-copy.sql)：新增 `canvas_resource_copy_sources`、`idx_canvas_copy_origin`，将 `ck_canvas_upload_mode` 从已执行的 `mode IN ('multipart','chunked')` 扩展为 `mode IN ('multipart','chunked','copy')`。该旧表达式是唯一允许自动升级的已存在差异；缺失约束、未启用的约束或其他表达式仍拒绝，不能借升级放宽异常结构。纯 SQL 只用于完整安装了前版且尚无副本表的库；已有部分改动使用脚本预检并补所缺项，不能直接重复执行 CREATE。

2026-10-05 已在 `short_drama` 实际执行以上 CREATE TABLE、CREATE INDEX 和一条原子 `ALTER TABLE ... DROP CHECK ..., ADD CONSTRAINT ... CHECK ...`；执行前仅缺新表和已知 CHECK 升级，`changed=[]`。执行后及再次只读检查均为 `ready`、`missing=[]`、`changed=[]`。没有复制或删除业务资源；真实文件验证使用隔离测试库和桶。

2026-10-06 首次完整创建增量见 [003-canvas-creation.sql](003-canvas-creation.sql)。已在当前 `short_drama` 实际执行 `CREATE TABLE canvas_creation_attempts`、`CREATE INDEX idx_canvas_creation_expiry ON canvas_creation_attempts (status, expires_at)`、`CREATE TABLE canvas_creation_resources`。预检只缺这两张表，没有结构漂移；执行后再次预检为 `ready`、`missing=[]`、`changed=[]`。此次未改写现有业务行，未复制或删除业务文件。

两表仅本人可见，固定原始创建请求、幂等键、来源快照及预留资源 ID。来源外键在复制期间保护原文件；复制确认后释放，目标文件仍属私人准备状态。最终事务同时创建项目/画布、规范文件、共享图、本人投影和写回执；失败不留下可见的空项目或半张图。准备记录不是第二份可修改的权威文档。90 分钟过期准备通过同一复制锁清理，删除字节前先取消暂存完成标记，保留原请求与目标 ID 供重试。创建与清理均不在数据库行事务内执行 MinIO I/O。

2026-10-06 绘图增量见 [004-canvas-drawings.sql](004-canvas-drawings.sql)。已在当前 `short_drama` 执行 `CREATE TABLE canvas_drawings`、`CREATE INDEX idx_canvas_drawing_active ON canvas_drawings (canvas_id, archived_at, updated_at)`、`CREATE TABLE canvas_drawing_versions`、`CREATE TABLE canvas_drawing_media_references`。预检仅缺三表，`changed=[]`；执行后和再次只读核验为 `ready`、`missing=[]`、`changed=[]`。没有改写原业务行或删除文件。

绘图身份表仅保存稳定键、当前版本和删除墓碑；每次成功保存新增不可变完整版本，资源引用随该版本保存。版本和引用都校验同画布复合外键，图片外键为 RESTRICT；版本拥有的引用可 CASCADE。删除绘图保留历史版本及其文件，不以旧客户端 revision=0 自动重建。新写入采用项目锁与绘图版本 CAS，保存作品按成员权限共享，本机草稿仍按账号隔离。跨项目副本与完整归档导入仍需后续接入，不能仅凭三表存在宣称完整绘图迁移。

2026-10-06 历史绘图增量见 [005-canvas-drawing-history.sql](005-canvas-drawing-history.sql)。已在当前 `short_drama` 实际执行 `CREATE TABLE canvas_revision_drawing_references`。执行前只缺这张表、无结构漂移；执行后 `ready`、`missing=[]`、`changed=[]`，完整结构为 84 表，其中画布 32 表。没有改写既有作品、历史行或媒体字节。

新快照通过该表保护明确的绘图版本，并持久稳定预览定位。历史拥有绑定的 CASCADE，绘图版本保持 RESTRICT；历史恢复同时校验图版本和全部绘图当前版本/墓碑，在同一事务备份当前图、追加恢复后的绘图版本、更新图并记录回执。前版快照不做猜测性回填，只按其已有明确 drawingId/drawingRevision 兼容读取；由于当前仍保留全部绘图版本，未来启用版本回收前必须完整核对并回填这些旧历史引用。

2026-10-06 项目文件夹增量见 [006-canvas-project-folders.sql](006-canvas-project-folders.sql)。已在当前 `short_drama` 实际执行：

- `CREATE TABLE canvas_project_folders`。
- `CREATE INDEX idx_canvas_project_folder_list ON canvas_project_folders (user_id, tombstoned_at, updated_at, id)`。
- `CREATE TABLE canvas_project_folder_items`。
- `CREATE INDEX idx_canvas_project_folder_items ON canvas_project_folder_items (user_id, folder_key, canvas_id)`。

执行前只缺两表，`changed=[]`；执行后及再次只读核验为 `ready`、`missing=[]`、`changed=[]`，共 86 表，其中画布 34 表。预检确认当前画布及历史共享 JSON 中非空旧 folderId 均为 0，因此不需要猜测性归属回填，未改写已有业务行或文件。旧版无成功的项目文件夹接口；旧共享 JSON 的该字段不再向成员投影，也不自动认领为当前请求者的分类。本机未提交的原文件夹意图仍由源恢复机制保留。

文件夹与归属仅本人可读写，复合外键固定作者；删除保留墓碑，封面清空后释放媒体引用，移动/删除只改本人分类。API 删除清除归属并保存可访问画布的历史/版本；撤权项目只清除本人关系，不改作品。原界面先将其中项目逐张归档，再删除文件夹，仍保留原操作顺序。升级新增表后应部署同版 API；应用仍不自动建表。

在早期 15 表基础上，资源阶段新增 `canvas_binary_resources`、`canvas_resource_uploads`、`canvas_resource_chunks`、`canvas_binary_references`、`canvas_user_binary_references`、`canvas_library_assets`、`canvas_library_asset_references`、`canvas_library_folders`、`canvas_library_folder_items`。这些表已实际在 `short_drama`（MySQL 8.4.11）执行 CREATE TABLE 和相应 CREATE INDEX；最近预检返回 `ready`、`missing=[]`、`changed=[]`。

图片/视频/音频复用 `media_files`；通用文件使用独立二进制资源表，不放宽标准媒体 MIME 约束。分片会话、预留资源 ID、内容摘要及回执持久化，MinIO 写入响应未知时保留同一资源身份。私人素材文档、分类和上传状态仅本人可见。分类归属由关系表维护，删除分类仅清除归属，不删除素材；未授权项目内容不会被读取或改写。

2026-10-05 资源删除阶段再次在 `short_drama` 执行 `CREATE TABLE canvas_resource_deletions` 和 `CREATE INDEX idx_canvas_deletion_due`。执行前只有这张表缺失，`changed=[]`；执行后 `ready`、`missing=[]`、`changed=[]`。未变更既有上传 CHECK，也未删除业务资源。删除回执保留已移除的资源/上传 ID 和原请求摘要，不再以外键指向这些已删除记录；本人可读，HTTP 会话不可改写或删除回执。

上传和创建暂存清理由 `backend/scripts/cleanup_canvas_uploads.py` 显式运行：默认只检查，`--apply` 执行完成分片、过期上传及过期创建准备的清理。资源永久删除回收由 `backend/scripts/cleanup_canvas_resources.py` 检查/执行。升级后需重启 scheduler：启动时识别新表，随后每 10 秒重试资源删除、每小时清理上传和创建暂存。脚本限定画布路径及配置的桶，保留被任一账号规范文件记录引用的对象。删除失败保留 durable outbox 并退避重试；文件成功删除后仍保留原上传键的墓碑，不能由同键重试复活资源。

共享作品和本人创作过程分表保存。媒体引用必须同项目且已发布，私人引用仅允许本人或有权访问的项目文件。快照引用独立保留，归档不删除媒体文件。未接通的生成、媒体处理和助手能力不因基础表存在而视为可用。

任务绑定阶段的 [007-canvas-task-bindings.sql](007-canvas-task-bindings.sql) 增加 `canvas_task_bindings`、`canvas_task_media_references`、`canvas_results`。当前业务库已有这三表及对应约束，任务准入、冻结来源、真实归档产物和写回执绑定由同版服务使用；它们仅本人可见，不能依项目成员权限读取他人的创作过程。

2026-10-06 文本流阶段的 [008-canvas-text-stream.sql](008-canvas-text-stream.sql) 已在当前 `short_drama` 实际执行 `CREATE TABLE canvas_task_text_deltas`，以及 `CREATE INDEX idx_canvas_text_author`、`CREATE INDEX idx_canvas_text_expiry`。执行前仅缺新表和两索引，执行后 `ready`、`missing=[]`、`changed=[]`。没有改写原业务数据。该表以任务绑定/序号唯一、真实调用记录外键和作者范围保存供应商增量；普通 HTTP Session 不能改写或删除既有增量，Worker 写入必须通过执行租约校验。

成功增量保留 24 小时，失败/取消保留 7 天；最终正文保留在生成记录。部署同版 scheduler 后每小时清理到期增量，原版 SSE 按序号续读。新表不代表完整协议、RunningHub 或付费供应商已经验收。

2026-10-06 模型目录阶段的 [009-canvas-model-catalog.sql](009-canvas-model-catalog.sql) 已在当前业务库 `short_drama` 实际执行 `CREATE TABLE canvas_model_catalogs`、`CREATE TABLE canvas_channel_models`，含其唯一键与外键。执行后及随后只读复核为 `ready`、`missing=[]`、`changed=[]`。完整 SQL 与 Domain 的 `export_schema.py --check` 已实际通过，当前 **92 表 / 40 张画布表**。本次没有重写既有模型配置、业务行或媒体文件。

`canvas_model_catalogs` 每人一行，公开源渠道目录与独立加密凭据信封分别保存；`canvas_channel_models` 以本人、源渠道键、模型键唯一绑定真实 AIModelConfig。伪造执行 ID 不被采用，删除后重加复用原身份。目录、偏好和 backing 配置共用本人版本事务与 CAS；源设置界面冲突保留草稿，不自动覆盖。

2026-10-06 BeefAPI 连接增量见 [010-canvas-beefapi-connection.sql](010-canvas-beefapi-connection.sql)，已在当前业务库 `short_drama` 实际执行 `CREATE TABLE canvas_beefapi_connections`、`CREATE INDEX idx_canvas_beefapi_poll ON canvas_beefapi_connections (next_poll_at, lease_until, id)`。执行后及再次只读核验为 `ready/missing=[]/changed=[]`，未改变既有业务行或媒体文件。真实数据库表数为 **93 表 / 41 张画布表**；ORM、账号作用域、010 和完整 SQL 已同步，`export_schema.py --check` 实际通过。首次尝试因 Docker WSL 引擎停止而在 MySQL 连接握手超时；恢复引擎后完成本次 DDL，不将首次失败计为成功。

公开状态与密文设备码/API Key 分开保存；每人一行，版本、到期时间和数据库租约支持重启恢复。官方目录复用现有两表，托管 Key 不重复写入目录信封；本人 headers 仍加密保存并按源在断开时保留。升级后必须同步部署 API/Worker/scheduler，并运行 scheduler 继续页面关闭后的设备轮询。应用不自行建表，GET 只读状态，不用两秒页面轮询发起上游令牌或确认请求。

独立模型测试复用现有 `async_tasks`、`ai_generation_records`、媒体表与加密配置，不新增虚构的画布或 binding。测试配置不出现在正常目录或默认选择，执行器只接受服务端标记且本人/版本/凭据身份一致的测试。自定义 headers 冻结在执行记录加密信封，公开快照只带版本与来源标记；标准模式的 key 信封与停用检查继续保留。部署时须同时更新 API 与 Worker，旧 Worker 不认识新信封，不能混用版本。

## 2026-10-08 审美工具租约与阶段批准

[011-canvas-tool-runs.sql](011-canvas-tool-runs.sql)与完整SQL、ORM同步。已在当前业务库`short_drama`实际执行：

- `CREATE TABLE canvas_tool_runs`。
- `CREATE INDEX idx_canvas_tool_run_author ON canvas_tool_runs (user_id, updated_at, id)`。
- `CREATE TABLE canvas_tool_submissions`。

执行前仅缺两表、无漂移；执行后与再次只读核验均为`ready/missing=[]/changed=[]`，共95表、43张画布表。未改写既有表、业务行或媒体；本次仅新增两表和索引，保留正在运行的旧API/Worker。本机证据在`backend/.runtime/beeftv-tool-runs-db-{precheck,apply-a}.log`，不提交运行产物。

两表服务原可达审美工具及已有分镜节点生成，不启用旧proposal、独立创作或旧助手写图接口。应用仍不自动迁移，其他环境先执行预检，再按缺项升级并部署同版API；Worker复用现有工具执行记录。DDL通过只证明结构，阶段执行和原UI恢复按后续测试记录，不代表真实供应商验收。

## 2026-10-09 分镜运行来源

[012-canvas-storyboard-run.sql](012-canvas-storyboard-run.sql)允许分镜运行没有图片来源，现有011库只需执行该列增量。新库完整SQL及当前011已包含可空定义；`canvas_migration.py --apply`仅识别原正确类型/默认值的非空列为可升级项，其他漂移仍拒绝。

当前业务库`short_drama`已实际执行`ALTER TABLE canvas_tool_runs MODIFY COLUMN source_media_id BIGINT UNSIGNED NULL`。执行后`ready/missing=[]/changed=[]`，`executed=[upgrade:canvas_tool_runs.storyboard_source]`；无新表、无业务数据改写。日志`backend/.runtime/beeftv-storyboard-ddl-a.log`不提交。部署须同时更新API与Worker，结构就绪不代表分镜生成、角色路径或真实供应商已经验收。

## 2026-10-09 画布本地渲染任务

[013-canvas-render-jobs.sql](013-canvas-render-jobs.sql)新增`canvas_render_jobs`与
`canvas_render_media_references`，与ORM、完整SQL及001保持同步。已在当前`short_drama`
实际执行`CREATE TABLE canvas_render_jobs`、`CREATE INDEX idx_canvas_render_author`、
`CREATE INDEX idx_canvas_render_schedule`及`CREATE TABLE canvas_render_media_references`。
预检仅缺两表、无漂移；执行后`ready/missing=[]/changed=[]`，未改写既有业务数据。
原始执行日志在`backend/.runtime/beeftv-timeline-job-ddl-a.log`，不提交。

两表只保存本人的非AI渲染及冻结媒体引用，不伪造模型、分集或AI生成记录。作者/项目/
画布使用复合外键，来源被引用时不可删除；HTTP普通ORM写入不能伪造租约、快照或完成
回执。任务中心继续使用原字段，新增结果仍为私人作品，明确附加后才按项目权限共享。
该阶段完整SQL为97表，旧库按预检缺项执行013；应用不自动迁移。当前准入、编码Worker、
MinIO及独立Celery/AMQP专项已执行，同版52项通过；实际画布浏览器FFmpeg入口及
完整对照仍在验证，不将DDL或后端专项通过称为完整导出验收。

## 2026-10-09 画布本地媒体任务

[014-canvas-local-jobs.sql](014-canvas-local-jobs.sql)新增 `canvas_local_jobs`，固定本人、
原请求、来源媒体定位/SHA/长度和操作键，来源外键保护排队与历史输入。私人素材任务可
不属于项目；项目任务须同时属于原画布。HTTP写入不能伪造租约、快照或完成回执。

当前 `short_drama` 已实际执行 `CREATE TABLE canvas_local_jobs`、
`CREATE INDEX idx_canvas_local_author ON canvas_local_jobs (user_id, updated_at, id)`、
`CREATE INDEX idx_canvas_local_schedule ON canvas_local_jobs (status, next_run_at, id)`。
预检只缺此表，无结构漂移；执行后及再次只读核验均为 `ready/missing=[]/changed=[]`。
未改写既有业务行或文件；执行报告在忽略的 `backend/.runtime/beeftv-local-ddl-report.json`。
完整SQL现为98表/画布46表，ORM、001及014同步；应用不自动迁移。旧库按预检缺项补014，
API、scheduler和本地执行Worker须一并部署，DDL就绪不代表执行或原界面验收已经通过。

## 2026-10-10 已处置画布生成媒体回收

[015-canvas-generation-media-deletions.sql](015-canvas-generation-media-deletions.sql)新增
`canvas_generation_media_deletions`，ORM、001与完整SQL同步。当前业务库`short_drama`
已实际执行`CREATE TABLE canvas_generation_media_deletions`及
`CREATE INDEX idx_canvas_generation_delete_due ON canvas_generation_media_deletions
(status, next_attempt_at, id)`。预检仅缺新表、无漂移，执行后及再次只读核验均为
`ready/missing=[]/changed=[]`；表数98→99，画布表46→47。没有修改既有业务行或媒体。
执行报告在忽略的`backend/.runtime/generation-media-ddl-report.json`，不提交运行产物。

新表固定本人、精确处置回执、任务、调用、原产物与规范媒体ID、输出槽、校验和、
扩展名及永久定位；不要求或伪造上传身份。退役事务只在引用检查通过后删除本人
结果行、产物索引及规范媒体，写入pending记录，提交失败全部回滚；任务/hash、
归档清单和公开回执继续保留。独立素材、其他作品/历史、标准/Agent审计、来源
任务和复制pin继续保护媒体，引用释放后重新检查。

同版scheduler识别此表，每10秒接续退役及独立删除重试。删除器再次校验处置作者与
冻结来源、真实归档清单和`generations/{task_id}/{record_id}/{asset_id}-{checksum}.{ext}`；
路径中的asset_id不是MediaFile ID。任意账号规范记录仍保护定位或资源ID，保留为
pending后退避重试；删除成功但响应丢失也按原定位重试，不触发新的供应商请求。

在`backend/`运行`uv run python scripts/cleanup_canvas_generation_media.py`仅检查，
添加`--apply`执行退役和字节删除，`--limit`限定单轮字节任务数（默认32，1–1000）。
该脚本只接续已完成图/执行载荷清理的精确处置代次，不将归档等同永久处置。
本片未在业务库运行`--apply`清理或重启服务；其他环境按缺项升级并部署同版scheduler。
015只处理生成出处；当前本地/渲染正式上传沿原资源队列，历史创建出处升级见017。
独立素材不随源画布自动删除。

## 本地任务处置引用升级

[016-canvas-local-disposal.sql](016-canvas-local-disposal.sql)更新两张已有任务表，
不增加表。优先在 `backend/` 运行 `uv run python scripts/canvas_migration.py` 预检，
再用 `--apply` 在命名互斥锁内升级；脚本只认可精确旧版输出 CHECK、来源可空性及
完整 RESTRICT 外键签名。变更过/未强制执行的 CHECK、来源列类型/可空性或外键漂移会在执行前
拒绝。新库直接使用完整SQL，无需重复执行016。

两表分别以一条原子 ALTER 新增 `disposal_receipt_id`、对应 RESTRICT 外键与索引，
增加处置 CHECK 并替换成功输出 CHECK；本地表另将来源设为可空，并增加普通任务
来源必填 CHECK。原来源外键改名为 `fk_canvas_local_source`，避免 MySQL 在同一 ALTER
中重建同名外键的1826错误。处置出处只能用于无租约的终态、空请求/快照和
`canvas_work_disposed`；渲染还需空计划。正常任务仍必须保留来源与成功输出，
HTTP Actor 的既有冻结字段守卫不允许伪造处置或释放媒体引用。

2026-10-10 08:58（UTC+8）已在当前 `short_drama` 实际执行016的两条 ALTER。
预检仅缺 `upgrade:canvas_local_jobs.disposal` 和 `upgrade:canvas_render_jobs.disposal`，
没有漂移；执行后再次回读 `ready/missing=[]/changed=[]`。表数99→99，两张任务表
行数各0→0，没有业务数据/字节清理或服务重启。具体语句和列/CHECK/外键/索引回读
在忽略的 `backend/.runtime/local-disposal-ddl-report.json`，不提交运行产物。
带旧任务数据的随机库专项核对全部身份/请求/快照/计划/结果及三份媒体记录保留，
这是DDL验证，不是实际ASR/深度模型或真实供应商验收。

同版回收服务按精确作者/归档代次释放来源与输出，旧v2已清图但未释放的本地引用
会补处理。当前深度/渲染产物的正式分片上传仍走原资源删除队列，素材和其他作品/
作者引用继续保护媒体。其他环境需按缺项升级并部署同版API、Worker和scheduler。

## 早期创建媒体删除出处升级

[017-canvas-creation-media-deletions.sql](017-canvas-creation-media-deletions.sql)以一条
原子 ALTER 扩展已有 `canvas_resource_deletions`，不增加表。优先用
`uv run python scripts/canvas_migration.py` 预检，再用 `--apply` 在命名互斥锁内升级。
只认可旧版三个上传身份字段的非空性、类型、默认值及全部已有约束；部分升级、
可空上传 ID、变更 hash 类型或未强制执行/漂移的 CHECK 都在执行前拒绝。

上传 ID/hash 改为可空，但新增出处 CHECK 保持原上传记录三项必填。历史创建路径
必须同时持有 `creation_resource_id` 和 `disposal_receipt_id`，二者为 RESTRICT 外键，
创建准备 ID 唯一，处置 ID 有索引；上传字段全空且分片数为0，不补造上传回执。
普通上传墓碑与创建原键重放规则保持。完整SQL和001同步，新库无需重复执行017。

2026-10-10 09:53（UTC+8）已在当前 `short_drama` 实际执行017的一条原子 ALTER：
修改三个上传身份字段可空性、新增两个创建出处列及RESTRICT外键、出处CHECK、创建
出处唯一索引和处置索引。预检仅缺 `upgrade:canvas_resource_deletions.creation`，
无漂移；执行后及独立回读均 `ready/missing=[]/changed=[]`。表数99→99，该队列表
行数0→0，原列内容摘要相同；没有业务行/媒体清理或服务重启。语句与列/CHECK/
外键/索引回读在忽略的 `backend/.runtime/legacy-creation-ddl-report.json`。

同版图回收服务核对处置作者/归档代次、原创建公开回执、冻结资源映射及规范目标的
项目/定位/元数据，保留准备与祖先快照；无外部引用才同事务删除规范目标并入队。
旧v2已清正文以及被独立素材/其他作品/复制pin保护的历史目标会重新检查，引用
释放后接续退役。删除器再次校验创建出处，按原永久定位重试失败或丢失响应；
任意账号的规范记录继续保护相同定位或资源ID。该阶段不运行供应商或自动迁移。
其他环境须按缺项升级，并部署同版API和scheduler；当前业务服务尚未重启部署本片。

可信业务项目来源增量见 [018-canvas-source-project-binding.sql](018-canvas-source-project-binding.sql)：只增加 `project_canvases.source_project_id BIGINT UNSIGNED NULL`、`idx_canvas_source_project` 和命名 RESTRICT 外键 `fk_canvas_source_project`。原 `project_id` 继续表示画布所属工作区；新列不改变画布权限范围。自由创建、ZIP 导入和既有未映射画布均保持 NULL，不从 properties、私人状态或历史 JSON 回填。

旧库先执行脚本只读预检，再用 `--apply` 补所缺的列、索引、外键，支持仅列或列加索引已完成时重入。字段类型、NULL/default、同名索引/FK或额外 source FK 漂移会停止执行。018 原始 SQL 只适用于三项完全缺失的旧库，不可在部分完成后整段重复执行。

本段随隔离候选准备：当前业务库 `short_drama` 尚未执行 018 DDL，旧来源映射也未实施。应用候选时必须在该库实际执行并记录准确语句、执行后结构及既有 NULL/行数核验；在此之前不能部署需要新列的 API/Worker，也不能把隔离模拟合同或待执行的集成测试写成真实迁移验收。


## 2026-10-11 当前业务库 018 实际执行记录

此前“018 尚未执行”为候选准备时的历史快照。来源绑定代码已应用后，2026-10-11 10:14（UTC+8）
在当前 `short_drama`/MySQL 8.4.11 实际执行以下三条语句：

```sql
ALTER TABLE project_canvases ADD COLUMN source_project_id BIGINT UNSIGNED;
CREATE INDEX idx_canvas_source_project ON project_canvases (source_project_id);
ALTER TABLE project_canvases ADD CONSTRAINT fk_canvas_source_project FOREIGN KEY(source_project_id) REFERENCES projects (id) ON DELETE RESTRICT ON UPDATE RESTRICT;
```

执行前只缺这三项，changed=[]；apply 与后续独立 verify 均 OS0，ready/missing=[]/changed=[]。
原 1 canvas 的编辑号 30/正文摘要不变，4 角色素材及 0 项目素材关系计数不变，新来源全部 NULL；
未推断/回填旧来源、未重启用户服务。准确前后状态与实际 SQL 在
[执行收据](../../../../.runtime/beeftv-source-binding-application-v1/business-apply-v1.json)及
[独立回读](../../../../.runtime/beeftv-source-binding-application-v1/business-verify-v1.json)。
随机 MySQL 专项 25 项和绑定后完整 unit/API 2567 项已实际通过；它们不替代旧源映射或部署验收。
首轮完整快测的音频失声与后续 PCM 缺尾仍另行处理，不把重测通过写成修复。
其他环境按所缺结构显式迁移并同步部署 API/Worker；角色 019/回填仍未执行。

## 019 角色不可变作品版本（隔离候选，尚未执行）

[019-asset-versions.sql](019-asset-versions.sql) 新增 `asset_versions`、`asset_version_media_references`，以及 `assets.current_version_id BIGINT UNSIGNED NULL`、复合索引、非角色 NULL CHECK 和命名复合外键。版本属于 Asset，媒体引用以 `(asset_id, version_id)` 固定所属，真实 MediaFile 外键均 RESTRICT。当前指针通过命名 `use_alter` 外键化解循环；不得关闭 `FOREIGN_KEY_CHECKS`。

原始 SQL 仅适用于版本表和当前指针完全缺失的旧库；已完成一部分或重复执行时，必须用脚本逐项核对后仅补缺件。脚本拒绝错误类型、NULL/default、索引、FK、CHECK 表达式或未启用 CHECK、未知额外结构漂移，不自动修改已存在的字段或历史。DDL 每步 autocommit，不能描述为整个迁移可事务回滚。

在 `backend/` 下按以下顺序准备与实施；本段命令尚未在真实业务库执行：

```powershell
uv run python scripts/asset_version_migration.py
uv run python scripts/asset_version_migration.py --apply
uv run python scripts/asset_version_migration.py --verify
uv run python scripts/backfill_asset_versions.py
uv run python scripts/backfill_asset_versions.py --apply
uv run python scripts/backfill_asset_versions.py --verify
```

默认结构检查只读；`--verify` 结构不完整时返回非零。默认 backfill 只读取、锁定及验证，不调用追加写入口；`--apply` 显式建立缺少指针且没有历史的角色 v1，已有有效指针只核验并计为无变化，已有不一致指针或缺失媒体不会静默改历史。失败角色返回可定位的 ID 和受控错误码；应用时该角色的 SAVEPOINT 回滚，其他成功数单独汇总，存在失败时非零退出，不能称全部完成。

应用 `session_factory` 创建 `AppSession`，角色回填也显式使用该类型。它只拒绝 Asset、AssetVersion、AssetVersionMediaReference 的 `bulk_save_objects`、`bulk_insert_mappings`、`bulk_update_mappings`，普通 mapper 沿用 SQLAlchemy 行为；混合对象在任何写入前全部检查。Session 事件另行保护历史实体和指针的 ORM/Core DML，包括 ordered/multi/from-select、upsert、别名及包装语句。该边界不扩展为所有 SQLAlchemy Session 或独立 Connection 的全面保护；可信维护脚本不得通过 plain Session 的 legacy bulk 修改角色历史。

实施前停下 API、Worker、导入等所有旧写者，确认没有活动或受理情况未知的任务。结构核验通过后才回填；主图、参考及表示图须实际存在、为图片且属于角色相同范围，未知作者保持 NULL。建版时间记录实际 UTC，来源旧时间只保留为审计；来源 `row_version=N` 只建立一个 v1，不构造 N 个历史版。数据 verify 只核验当前内容、历史 hash、连续版本号、全部用途槽位及媒体所属，不写数据。

完成后须在此追加真实业务库名、执行的 CREATE/ALTER、backfill 成功/失败/无变化数及结构和数据 verify 结果。当前 `short_drama` 未执行本 019 DDL/backfill，本轮仅准备隔离候选和离线编译证据。该增量不建立 `project_assets`、不回填 `source_project_id`、不连接声音绑定，也不移除 `canvas_storyboard_character_unsupported`；018 来源绑定及后续可信角色 resolver 仍分别验收。


## 2026-10-11 当前业务库 019 实际结构执行记录

上节“隔离候选，尚未执行”及命令未执行的文字是准备时的历史快照。角色 v4 已应用并完成后端测试与静态门禁后，2026-10-11 11:30（UTC+8）在当前 `short_drama`/MySQL 8.4.11 实际执行 7 条结构 DDL：`CREATE TABLE asset_versions`、`CREATE TABLE asset_version_media_references`、`CREATE INDEX idx_asset_version_media_file`、`ALTER TABLE assets ADD COLUMN current_version_id BIGINT UNSIGNED NULL`、`CREATE INDEX idx_assets_current_version`、新增 `ck_assets_character_version`、新增 `fk_assets_current_version` 复合 RESTRICT 外键。没有关闭 FOREIGN_KEY_CHECKS、DML 或数据清理；每条 DDL 独立隐式提交。

执行前 `changed=[]`、六个结构缺件（新增表的独立索引另计为第七条实际 DDL）；apply OS0/7DDL，随后独立 verify OS0/0DDL、ready/missing=[]/changed=[]。全部原资产/画布指纹相同，4 角色、0 project_assets、source 全 NULL；当前指针、版本及版本媒体均 0。实际七语句及准确结构在[执行收据](../../../../.runtime/beeftv-character-version-application-v4/business-apply-v1.json)和[回读](../../../../.runtime/beeftv-character-version-application-v4/business-verify-v1.json)。完整 schema 已同步 101 张表。

本次只完成结构阶段。原 API、scheduler、text、image、video、render、agent 七角色未停止或重启；未执行角色 backfill、来源绑定补造或声音接入。回填前仍须全停旧写者、核无 active/unknown、使用显式维护入口，之后部署全部同版角色并记录数据 verify；不能把本段结构通过描述为该完整运行流程已通过。免费真实 I 媒体验收失败与离线目录候选另记，不由 019 推断角色菜单可用。
