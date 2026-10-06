# 无限画布基础存储迁移

新增 `projects.workspace_mode`，默认 `standard`，并增加限定取值的 CHECK。旧项目与现有分集数据保持原有语义。当前新增 40 张画布表，包含项目主画布设置、节点、连线、历史、写回执、私人投影与模型选择偏好、媒体引用、时间线、导演场景、资源上传/副本、删除回执、首次创建准备、绘图版本/历史绑定、私人素材库、本人项目文件夹、私人任务/产物、供应商正文增量及加密渠道目录。基础表定义见 [001-canvas-core.sql](001-canvas-core.sql)，任务、文本流和模型目录分别见 [007](007-canvas-task-bindings.sql)、[008](008-canvas-text-stream.sql)、[009](009-canvas-model-catalog.sql)。

应用不自动执行 DDL。停止相关写入并确认可恢复备份后，在 `backend/` 执行：

```powershell
uv run python scripts/canvas_migration.py
uv run python scripts/canvas_migration.py --apply
uv run python scripts/canvas_migration.py
```

默认只预检；`--apply` 获取迁移锁后按缺项新增。脚本逐项核对字段、类型、默认值、主键、索引、外键、CHECK 表达式与执行状态、引擎和字符集；除下面明确识别的旧 copy CHECK 外，已存在但不匹配的结构会拒绝继续。允许中断后重新预检并安全重入，不把 MySQL DDL 当作可整体回滚的事务。较长数据库名使用固定长度锁名摘要，避免 MySQL 64 字符锁名称限制。

`--write-sql` 只同步增量 SQL，不执行 DDL。完整 SQL 使用 `scripts/export_schema.py` 同步。新库只使用[完整建库脚本](../../schema.mysql8.sql)；已使用本次早期结构的数据库再次执行 `--apply`，增加所缺的表和索引，并升级明确识别的旧 CHECK。当前完整 schema 共 92 张表。

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
