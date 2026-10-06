# 无限画布接口

当前已接通双模式项目、画布文档、历史、个人状态、加密渠道目录与独立模型测试、资源上传下载、私人素材、绘图基础、本人项目文件夹及生成任务准入/基本回填。完整媒体加工、生成参数与协议、绘图工具、导演、助手和插件执行仍在迁移中，不能把本文列出的接口视为完整 BeefTV 功能验收。进度见[实施记录](../plans/2026-10-05-beeftv-implementation-log.md)。

统一前缀 `/api/v1`。认证、Origin、CSRF、项目成员检查沿用现有中间件。`X-Canvas-Actor` 是画布打开时的账号 ID，仅用于校验当前会话是否发生切换，不能授予身份或权限；不匹配返回 `409 canvas_actor_changed`，不撤销其他窗口刚登录的有效会话。

创建项目接受 `workspace_mode: standard | infinite_canvas`，默认 `standard`。无限画布创建必须提供 `Idempotency-Key`；在同一事务中创建项目、主画布和回执。列表与详情新增 `workspace_mode`、`primary_canvas_id`、`canvas_count`。模式创建后固定，PATCH 不接受该字段。

## 文档和历史

下表 `C` 表示 `/projects/{project_id}/canvases/{canvas_id}`。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/canvas-workspace` | 分页返回本人有权访问的活动画布，包含摘要、预览节点与可选完整文档；见下方分页约定 |
| POST | `/canvas-workspace` | 源画布的“新建项目 / 复制项目”入口；原子创建宿主项目和指定 source_key 的首画布；要求写入幂等键 |
| GET | `/canvas-workspace/resolve/{source_key}` | 将源稳定键解析成规范的画布与项目 ID，仍需权限检查 |
| GET / POST | `/projects/{project_id}/canvases` | 列举 / 新建画布；新建接受 title、可选 source_key/source_document/drawing_documents，要求写入幂等键 |
| GET | `C` | 共享作品文档，不含他人私人提示词、任务参数和对话 |
| GET | `C/my-document` | 共享作品叠加本人私人编辑字段 |
| POST | `C/commits` | `{expected_row_version,schema_version:1,source_document}`，要求写入幂等键；冲突保留客户端草稿 |
| DELETE | `C` | 请求体 `{expected_row_version}`，要求写入幂等键；归档主画布后选择剩余画布，归档最后一个画布时归档项目 |
| GET | `/canvas-write-receipts/{key}` | 本人操作回执；归档后的删除回执仍可恢复，撤销成员权限后不可读取 |
| POST | `/canvas-runtime/canvas-projects/{source_key}/recycle-restore` | `{archive_key}` 和写入幂等键；本人删除回执限定作品、项目和已归档版本，返回 `CanvasSummaryRead`，原子恢复原项目与画布 |
| GET | `/canvas-runtime/recycle-bin` | 本人仍有权限且处于当前归档代次的删除记录，最多 200 条；每项含 `source_key/project_id/archive_key/deleted_at/source_document` |
| POST | `/canvas-runtime/canvas-projects/{source_key}/recycle-purge` | `{archive_key}` 和写入幂等键；不可恢复地处置确切归档代次，返回 `source_key/archive_key/state=purged`；不等于立即擦除全部物理字节 |
| GET | `/canvas-runtime/canvas-projects/{source_key}/recycle-status?archive_key=...` | 只读核对本人原删除是否仍为当前归档代次；返回 `CanvasRecycleStatusRead`，不重发删除、不读取子文档 |
| GET / PATCH | `C/user-state` | 本人 viewport/preferences；PATCH 带独立 expected_row_version，不推进共享图版本 |
| PUT | `C/viewport` | `{expected_viewport,viewport}`，只比较并保存本人视口字段；相同目标位置可恢复丢失的回执，不推进共享图版本 |
| PUT | `C/view-preferences` | `{expected_preferences,preferences}`，只比较并保存本人 appearance/backgroundMode/showImageInfo；响应 `{row_version,preferences}` |
| GET | `C/revisions` | `{items,row_version,drawing_heads}`，快照摘要包含版本、节点数、连线数和 payload_bytes；drawing_heads 是当前绘图键到 `{revision,deleted}` 的映射 |
| GET | `C/revisions/{revision_id}` | 返回共享快照叠加该快照中本人的私人投影 |
| POST | `C/revisions/{revision_id}/restore` | `{expected_row_version,expected_drawing_heads?}` 和幂等键；恢复产生新版本并保留恢复前快照，绘图与图在同一事务恢复 |
| GET | `/canvas-runtime/canvas-projects/{source_key}/events?actor_id=...` | Cookie 鉴权的短连接 SSE，发出 canvas.updated；会话失效或账号变化发出 session.expired，画布撤权/归档发出 canvas.unavailable 并结束；data 仅含错误 code，不含作品 |

规范画布 ID 与所有数据库版本均为十进制字符串；源 `source_key`、节点键、边键保持原版不透明字符串。`source_document` 内使用源 camelCase 合同，外层 DTO 使用 snake_case。`workspaceProjectId` 是宿主项目归属；源 `projectId` 是另一个短剧绑定语义，尚未验证的非空绑定会拒绝，二者不得混用。

本人文档与历史读取增加信封字段 `resource_aliases: {"副本资源ID":["来源资源ID", ...]}`。只返回当前请求者本人已完成、仍有访问权限的副本来源关系，且仅包含该文档实际引用的副本；共享 `GET C` 返回 `{}`，其他成员不会获得作者的来源链。工作区 `include_documents=true` 按页批量查询并分发到各文档，不逐画布查询；摘要列表省略此字段。所有键和值均为十进制字符串，来源关系不写入 `source_document`、共享节点或素材 payload。

前端在把当前文档、历史或分页文档交给源修复流程前恢复来源关系，运行时缓存按账号及登录 epoch 隔离。保存前归一化取得的来源关系先写入本人、当前画布的本机提交日记 `resourceAliases`，再投影副本 ID；重新认证后恢复同账号草稿时读取并校验该日记，避免图提交失败后刷新把副本误认成新素材。本机 `resourceAliases` 不直接上传，不进入图请求或共享文档；创建回执自身包含服务端计算的 `resource_aliases`。来源关系只用于素材身份匹配，不授予来源文件读取权限，不改原素材 storageKey。

当前文档校验节点/边唯一键、父子循环、端点归属、有限几何值、正尺寸、凭据字段、32 MiB 上限和嵌套深度。文档分解到关系表；不同时维护第二份可独立修改的当前全图 JSON。共享 metadata 使用显式字段清单，新字段默认进入作者私人投影。

图保存、首次创建/复制和历史快照按字段语义规范媒体展示值：image/video/audio/model/panorama 的 content/url/dataUrl 有稳定 resource 身份时使用同源受鉴权文件地址，保留播放 variant、proxy 和片段，不持久化原 origin 或签名参数。drawingPreviewUrl、directorCoverUrl 使用各自的预览资源键；没有稳定身份的临时派生预览省略，不能把待生成预览指向旧原图。文字正文、提示词和未知字段不做通用 URL 替换。

新写入的主要媒体为 Blob/data URL 且没有稳定资源身份时返回 `422 canvas_media_not_persisted`，保留草稿。旧图和历史在私人字段投影完成后兼容读取；下一次成功保存会真实清理旧图行，不能仅在返回值掩盖旧数据。前端内容比较和三方合并使用同样的规范值，避免同一文件的 Blob 与文件 URL 产生假冲突或假未保存。已持久化、结果未知的首次请求和在途提交仍按原键、原正文重放，不回写规范后的正文；服务端原始请求审计可能保留旧客户端提交值，不能据此改变幂等哈希。没有稳定身份的外部 URL 导入仍未实现。

分镜、导演、时间线等嵌套私人数组按条目稳定 `id` 对应，成员插入、重排、删除不改变私人字段的归属，也不复活已删除的父对象。无 `id` 的旧格式条目只在公共内容完全相同时匹配；含私人字段的重复身份返回 `422 canvas_private_identity_required`。内部投影标记 `$canvas_projection` 不接受客户端输入，也不会出现在作者文档中。无锚点的旧位置数组返回 `409 canvas_private_projection_upgrade_required`，保留原数据并要求显式迁移，不能猜测其归属。本次升级核验当前 `short_drama` 的三类画布私人状态表均为 0 行，没有需要转换的已有数据；本次无 DDL 变更。

同一幂等键与相同请求返回原回执；同键异参返回 `409 canvas_idempotency_conflict`。图版本冲突返回 `409 canvas_revision_conflict` 和十进制字符串 `details.current_version`。首次创建在浏览器持久化原请求，网络重试复用原键和原请求；后续输入在创建确认后另行提交。

两个画布 POST 创建接口返回 `CanvasCreationRead`：原摘要字段加 `resource_map: {"来源ID":"目标ID"}` 和本人 `resource_aliases`，无媒体时两者为空。完整图所用的私人/跨项目文件先按固定请求准备独立副本；已有目标项目内的文件原样复用。全部媒体准备好后，一次事务保存项目（新工作区）、画布、规范资源、完整图、私人投影及同一份回执。复制失败返回 `503 canvas_resource_copy_failed`，保留请求和预留资源 ID；响应未知时必须重放原请求，不能先保存空图再补媒体。

首次创建可同时携带 `drawing_documents`，每项沿用绘图 PUT 的 drawing 字段：`drawingId,engine,revision:"0",snapshot,shapeCount,pageCount,previewResourceId?,render?`。节点引用的新绘图版本必须为 `"1"`；清单不允许重复或游离 ID，有已保存版本/非零图形数的节点不能省略其笔画。图与全部笔画请求合计限制 32 MiB。省略与空清单保持旧无绘图请求的摘要输入，既有幂等回执仍可重放。

创建服务将笔画内的稳定图片、预览和成品一起纳入原媒体准备流程，在同一发布事务写绘图、精确快照、媒体引用、图和回执；任何一项失败全部回滚。`resource_map` 包含图及子文档的全部资源身份，`files.*.dataURL` 仅按资源字段语义映射，不替换文字内容或内嵌字节。来源作者的私人媒体仍由原资源权限过滤，跨项目媒体成为目标副本，同项目媒体复用原资源。

项目库整组复制、顶栏“复制画布”和只读页“复制项目”已接入：先冻结节点及一次读取所得的笔画/派生图，再持久化本人首次请求中的绘图清单。清单只放在创建请求外层，不进入共享图或普通提交；源几何和节点 ID 保持其原复制语义。整组包含多张画布时依然逐张原子创建，不能宣称整个多画布项目在一个数据库事务中完成。

原版项目库 ZIP 入口中的绘图导入也使用上述合同。先在本人资源作用域上传 ZIP 图片（明确不使用当前页面的画布归属），将节点版本改为 `"1"`，再把初始 `"0"` 笔画清单与首次图请求一起冻结、发送。上传键同时包含字节摘要、类型、文件名和 MIME，避免同字节不同声明请求冲突。创建回执的资源映射通过校验后，以映射后的已确认完整图和服务端读回结果比较，不能直接比较尚含来源资源 ID 的导入草稿。

普通媒体的素材库记录需要已存在的目标画布，因此放在首次图/笔画发布成功之后建立，再以正常版本提交节点及时间线的素材绑定。本人首次请求保留 `initialArchiveBindings` 恢复标记，该字段和绘图清单都从共享图剥离；绑定及实时草稿落盘之前不清除原请求，丢失创建回执后的刷新也会继续绑定。异步绑定期间若节点或时间线已被编辑则拒绝覆盖，保留草稿。素材库绑定不属于图与笔画的首个数据库事务。

绘图导出一次读取笔画记录，并读取该记录引用的预览/成品；本机草稿则在同一代缓存中捕获。`snapshot.files.*.dataURL` 引用的托管图片在 ZIP 中转换为内嵌图片，文字及精确浮点坐标保留。预览/成品缺失时停止导出。保留原版 v4 manifest 和原 UI 入口，不把同步元数据或源资源 ID 作为绘图归档身份。

归档创建结果不明时保留草稿、文件和首次请求；现有刷新/保存恢复重放同一请求，不自动删除可能已成功的作品。ZIP 缺少声明文件时在上传前拒绝。当前真实归档验证覆盖无文件夹的可编辑绘图及其派生图片、PNG/WAV/H.264 MP4 混合节点与私人素材绑定、跨账号传递和单画布未知回执；视频缩略图 `sourceKey` 与主文件同步映射，缩略图字节随 ZIP 保留。文件夹、其它格式/时间线/导演台、整组中断后的完整恢复、历史导出仍需继续验证，不能据此声称全部归档功能完成。

创建准备仅本人可见，来源未确认复制时始终复核访问权限；已确认的独立暂存副本在来源撤权后仍可完成原请求，但目标项目撤权仍拒绝。准备超时的字节按复制锁清理，原请求和预留 ID 保留，重新复制前再次核验来源。前端先持久化来源关系，再将映射应用于确认快照、实时图及历史；不改原首次请求，不覆盖等待时的新编辑。后续插入同一来源复用 `canvas-normalize:v1:{source_key}:{source_id}` 对应文件，不重复复制。源多画布复制使用第一张源键分组时，服务适配层解析其宿主项目 ID，保持原分组操作。

首次创建在发送前同时持久化本机草稿与不可变原请求；刷新恢复和重新打开草稿均先重放该账号已存的原请求，再合并服务端图，避免把媒体 ID 映射误判成外部编辑。映射后的实时草稿落盘后才清除首次记录，等待期间的后续编辑另行按版本提交。仅 `revision=0` 或 GET 返回 404 不构成自动新建的依据。列表多画布复制在首个 POST 前冻结整组草稿，恢复先处理主画布再处理其余画布，保留原分组和每张画布的幂等键；并非跨多个 POST 的整组数据库原子事务。

整组复制先以本人命名空间的 `canvas-initial-group:{主画布源键}` 保存一份完整记录，再在现有账号存储锁内建立各画布的首次请求并写入普通画布缓存。逐张写入失败或普通缓存未写完时保留整组记录；下次启动只按这份明确的复制意图补齐原请求与缺失草稿，保留已经存在的后续编辑，不重新生成 ID。各请求和缓存全部落盘后才移除整组记录，随后按依赖调用服务端。只有本机失败恢复记录，不增加服务端工作区表或整组事务。

列表复制结束时校验发起账号及页面是否仍有效。离开列表后可在后台完成已发出的保存，旧回调不再导航或显示旧页面提示；账号变化后旧回执不能投影到新账号状态，原账号的未确认请求保留，重新认证后可用原键恢复。源只读页的“复制项目”沿用原入口，创建成功后进入可编辑副本。

工作区分页参数为 `page`（从 1 起）、`page_size`（默认 50，上限 100）、`q`（标题字面包含匹配）、`project_id`、`sort=updated|created`、`include_documents=false|true`。响应为 `{items,page,page_size,total,has_more}`；每项增加 `canvas_title`、`node_count`、`preview_nodes`。请求完整文档时每项返回 `source_document`，`preview_nodes` 为空，由适配层复用文档节点生成预览，避免重复传输。列表与计数都按成员权限筛选；节点、连线、时间线、导演和私人状态按页批量查询。启动恢复逐页读取完整文档，不再为每项重复请求详情。

视口采用字段级比较更新，避免正文保存和个人偏好保存相互产生假冲突；其他窗口已移动视口时返回 `409 canvas_viewport_conflict`，前端保留本机位置并停止重试，须显式重新加载。进行中的视口请求串行执行，网络结果不明时先重试原请求再保存后续位置。作品图提交忽略所携带的旧 viewport；首次创建仍接受初始位置。视口保存不生成作品历史版本。

外观使用相同的独立保存边界，设置变化 500 ms 后串行提交，显式保存等待服务端确认。比较的是三项外观设置，不受视口、对话或共享图版本变化影响；其他窗口修改外观返回 `409 canvas_view_preferences_conflict`，保留本机设置并冻结自动覆盖。旧图提交与历史恢复不会重置已经保存的本人外观，首次创建接受导入的初始外观。`appearance.mode` 为 `light|dark|custom`；custom 的色码为六位十六进制，明亮度范围 -30～30，网格强度 0～100；`backgroundMode` 为 `dots|lines|blank`。

保存进度和错误进入原版顶栏的保存状态与弹层；源样式会隐藏全局 Toast，不能依赖 Toast 表达托管端失败。图提交的 saving/done/error/conflict 和个人设置的 pending/error 分别保留，图保存成功不能清掉个人外观冲突。错误弹层保留当前内容下载与显式重新加载入口。

编辑器接收撤权通知或画布本体读取的 403/404 后，保留本机草稿、停止该账号当前编辑会话对该画布的后续写入，并使用原错误层提示重新加载。历史条目、媒体文件或首次复制来源的 404 不等于整张画布被撤权；此区分不会清除已经确认的撤权状态，每次原请求重放仍由服务端检查权限。SSE 更新只经过原有刷新合并入口，避免二次直接替换当前节点。

## 回收站恢复

原版回收站保留卡片、多选、全选、恢复、彻底删除确认及 Escape 操作。打开时读取服务端目录，同一归档代次保留本机完整快照和原删除时间，新设备使用服务端快照。目录按删除时间和 ID 倒序，最多 200 条，仅返回删除者本人、当前仍有项目权限、归档代次匹配且未永久处置的作品。读取过程中发生本机恢复或删除时，过期目录响应不能覆盖新列表；读取失败保留本机快照并提供重试。

归档事务把本人删除前文档冻结在既有 `canvas_write_receipts.result_json` 的内部 `$archive_document` 字段，无新增表。普通删除响应和通用回执 GET 均过滤这个字段；不能绕过回收状态读取快照。旧删除回执没有冻结文档时，仅在确切归档画布作用域读取仍存在的文档表，私人字段继续按当前作者过滤，不读取其他成员的参数。快照、回执与媒体保留策略后续还需物理生命周期专项，目录列举不等于完整垃圾回收。

回收图片/视频预览使用原 `/canvas-runtime/resources/{resource_id}/file` GET/HEAD，并同时提供 `recycle_source_key`、`recycle_archive_key`。服务端重新核对本人回执、当前成员权限、当前归档代次，以及资源是否属于该删除快照；错误来源或无关资源返回 404，缺少成对参数返回 422，已处置返回 410。支持原 HEAD/Range/no-store 行为。授权查询参数只存在于展示副本，不写入可恢复文档；普通资源接口的归档访问限制保持有效。

删除前将原作品快照、删除前版本、操作时间及固定 `archiveKey` 写入本人 IndexedDB；写入失败不发送删除。请求结果未知时原记录保留，刷新通过只读 `recycle-status` 核对。它重查回执作者、当前成员权限及确切画布源键，返回 `source_key/project_id/archive_key/expected_row_version/committed_row_version/state`，所有数据库 ID 和版本仍为十进制字符串。`state=archived` 表示作品仍处于该回执的已归档版本，`superseded` 表示已有后续恢复或归档操作，`purged` 表示此代次已永久处置；404 也可能是失去权限，不代表可以重新创建或自动重发删除。

确认 `archived` 后，前端先等待回收快照落盘，再移除活动缓存，最后清除删除日记；中断可继续。`superseded/purged` 只清理该旧请求及匹配的旧回收条目，不改变服务器当前作品。未确认请求保留快照和草稿，明确再次点击删除才按原键、原版本重试；服务端明确返回旧版本 409 时，保留冲突草稿并放弃已被拒绝的删除请求。本机已有新编辑时不移除草稿。此机制处理本人浏览器中的未知删除；已提交删除通过服务端目录跨设备读取，但尚未接入的全部未保存子文档恢复仍不在已验证范围。

恢复请求必须引用当前账号的 `canvas.archive` 回执；其他成员的回执不能代替本人的删除记录。本人仍须拥有项目或保持活动成员身份，撤权后连回执重放也返回 404。优先使用服务端目录提供的 `archive_key`；旧本机记录兼容 `canvas-delete:{source_key}:{revision}`。恢复键为 `canvas-recycle:{source_key}:{revision}`，结果未知时不换键、不新建作品。

服务端只在该事务内、对回执指向的确切项目和画布开放归档读取，账号与成员谓词仍生效。它不打开其他归档项目、标准项目、私人执行数据或全局系统 Session。先锁项目，再锁画布并读取当前幂等回执；已归档版本必须等于删除回执的 committed_row_version。版本不符返回 409；活动画布返回 `canvas_not_archived`。旧恢复回执的重放只返回原结果，不能再次激活后来重新归档的画布。

成功时在同一事务清除画布归档标记、推进图版本，必要时恢复原项目并建立主画布关系；项目已有主画布时保持它。节点、绘图笔画及其版本、时间线/导演记录、媒体稳定 ID、个人参数和历史保持原记录，不从浏览器复制作品。已被删除的项目文件夹不会复活；分类以当前本人归属表为准。回执写入或任一后续步骤失败，项目和画布恢复一起回滚。

前端重新读取本人规范文档，写入原提交日记和本机草稿后才移除回收卡片。服务器已恢复但响应丢失时，当前弹窗保留卡片；明确重试仍使用原键，重新打开或刷新目录会移除已恢复记录，不自动重发恢复。如果本机已经出现未保存编辑，保留编辑并提示处理，不用旧回收快照覆盖新内容。验证结果和范围见[实施记录](../plans/2026-10-05-beeftv-implementation-log.md)。

彻底删除使用 `canvas-recycle-purge:{source_key}:{revision}` 固定键，服务端先锁项目、画布，再核对回执和当前归档代次，写入私人 `canvas.recycle.purge` 回执。与恢复竞争只允许一方成功：恢复先成功时旧处置返回 409，处置先成功时恢复返回 `410 canvas_recycle_purged`。同键重放返回原处置结果，没有再次修改作品的副作用；失败回滚回执，并释放窄归档作用域。带 Actor 的业务 Session 禁止改写或删除既有画布写回执，批量写同样受保护。前端移除卡片落盘失败时恢复原列表，保留原快照与 `archiveKey`；如果期间已有新列表，则不以旧列表覆盖。这一接口阻止正常业务恢复和回收预览，尚未物理擦除冻结文档、历史、绘图以及仍被引用的 MinIO 文件；不能宣称全部数据已经物理删除。

## 项目文件夹

`F=/canvas-runtime/canvas-folders`。这是本人项目工作区的文件夹，与 `/canvas-runtime/asset-folders` 素材分类独立。文件夹及画布归属只按当前账号读取，不通过项目成员权限共享。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `F` | `{folders:[{id,name,coverResourceId?,createdAt,updatedAt}]}`；排除删除墓碑，按更新时间、创建时间倒序 |
| PUT | `F/{key}` | `{folder:{id?,name,coverResourceId?,createdAt?,updatedAt?}}`，返回 `{folder}`；不要求源前端不存在的 folder revision |
| DELETE | `F/{key}` | `{id}`；保留删除墓碑，重复删除成功，未知/其他账号文件夹返回 404 |

key 是最多 80 字符的不透明源 ID，去除首尾空白，正文 id 若提供必须匹配路径。名称去空白，空值使用“未命名文件夹”，最多 80 Unicode 字符；允许同名。首次创建接受有效客户端 createdAt，无效值回落服务端 UTC；更新不修改创建时间，updatedAt 由服务器决定。封面资源 ID 为十进制字符串，必须是本人创建且仍可读取的规范资源。前端上传封面明确使用本人作用域，封面外键参与真实媒体回收保护；替换或删除文件夹释放旧引用，不直接删除字节。

删除后 PUT 返回 `409 canvas_folder_deleted`，适配为源 `failed_precondition`，源待提交修改保留在本机并停止自动重新导入。移动画布或恢复历史时，指定缺失/已删除文件夹返回 `422 canvas_folder_missing`，消息“画布文件夹不存在”，整次作品事务回滚。历史读取仍保留作者当时的分类键，不把已删除文件夹自动恢复。

`source_document.folderId` 从 `canvas_project_folder_items` 投影，仅在本人文档/本人历史中出现；不写入共享根 JSON 或可独立修改的个人偏好 JSON。`GET /canvas-workspace` 的摘要信封增加 `folder_id`，源适配层转回 `folderId`。同一画布的不同成员可有不同归属。移动使用现有图版本与幂等提交，推进图版本并按原有节流规则保存历史；源项目库会对项目内所有画布分别保存，不能宣称整组移动是一个事务。

底层 DELETE 文件夹接口清除本人归属，保留画布；可访问的活动画布按最新已锁版本保存历史并推进版本，旧保存请求返回 409。已经撤权或归档的作品仅清除本人分类记录，不读取或修改作品。原版项目库点击“删除文件夹”另有上层流程：先逐张将其中项目画布归档并登记本机回收站，再删除文件夹；该界面流程保留原实现，与底层接口分别验收。

图创建/提交/恢复与文件夹变更共用按账号划分的文件夹 advisory lock，再按项目顺序取得行锁；它不与私人素材库共用集合锁。涉及副本素材时，原全局副本锁在文件夹锁之前取得。删除先查询画布 ID，取得项目锁后再读取画布实体，避免旧 ORM 缓存覆盖其他成员刚提交的版本。封面写入先锁资源再锁文件夹行，与媒体回收的引用检查保持一致顺序。

ZIP 沿用源 v4 文件夹清单与封面文件；导入先创建本人新文件夹和独立封面，再按 ID 映射恢复画布归属。文件夹、封面和各画布是多个可恢复操作，不是整个 ZIP 一个数据库事务。已验证两张画布、一个自定义封面的跨账号传递；原文件夹删除后的双画布恢复见上节，其余归档组合及完整删除生命周期仍需后续验收。

## 绘图文档

`D=/canvas-runtime/canvas-projects/{source_key}/drawings`。读取和写入都重新核对活动画布、无限画布模式和当前成员权限。绘图 ID 是源不透明键；revision 和资源 ID 是十进制字符串。保存作品共享，本机未提交草稿仍按账号隔离。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `D` | `{drawings}`，仅活动绘图摘要，不含 snapshot |
| GET | `D/{drawing_id}` | `{drawing}`，含当前 snapshot、引擎、版本、图形数、页数、预览及成品 |
| PUT | `D/{drawing_id}` | `{drawing:{drawingId,engine,revision,snapshot,shapeCount,pageCount,previewResourceId?,render?}}`；revision 为已读版本，首次为 `"0"` |
| DELETE | `D/{drawing_id}` | `{id,revision}`；写删除墓碑，revision 为已确认删除版本，十进制字符串；已删除记录的重复删除返回当前墓碑 |
| GET | `D/{drawing_id}/versions/{revision}` | 读取不可变保存版本，仍须当前项目权限；供子文档冻结接入，不是新的 UI 操作 |

引擎为 excalidraw，pageCount 按源单页语义规范为 1。snapshot 保留原版 Excalidraw 结构及内嵌图片字节，限制 32 MiB、64 层并拒绝凭据字段、非有限数字和 `files.*.dataURL` 中本页 Blob 引用。预览与成品必须是同项目中本人未发布图片或已经发布的图片，保存成功时发布并建立版本资源外键。render 的 resourceId 与 storageKey 必须一致；省略 render 保留已有成品，`{resourceId:""}` 清空当前成品。绘图版本的旧媒体引用仍保留。

保存沿用原版 CAS 重放：相同请求的预期版本恰为当前版本减一且正文一致，返回同一版本；其它旧版本返回 `409 canvas_drawing_revision_conflict`，保留本机草稿。窗口使用打开绘图时的版本，不能借用另一窗口刚写入共享缓存的新版本覆盖旧画面。版本内 snapshot 使用唯一的精确 JSON 字符串封装保存，读取后还原原结构；不经 MySQL JSON 的浮点转码，避免坐标末位漂移和原请求假冲突。内部编码标记不出现在源 DTO 中。

删除后 PUT 返回 `409 canvas_drawing_deleted`，前端映射原版 failed_precondition，不能自动重新导入。绘图上传的身份键包含画布键、绘图键及文件摘要，并显式捕获目标画布，切换路由不能改变文件归属。

整图快照按节点声明的 drawingId/drawingRevision 绑定已保存绘图版本，并写入该版本的稳定预览定位。历史读取只查询明确版本，不把当前绘图或本机草稿拼入旧图；前版历史缺少绑定时，仅按已有明确版本兼容读取，不猜测最近版本。原版历史预览界面仍只显示预览图，不开放绘图编辑；下载历史仍沿用源不包含本机笔画的行为。

原版版本列表同时捕获 `drawing_heads`，恢复提交为 `expected_drawing_heads`，所有版本仍是十进制字符串。即使图版本未变，绘图另存、删除或新增也会返回 `409 canvas_drawing_restore_conflict`，要求刷新列表并重新确认。无任何绘图的旧请求允许省略此字段；已有绘图时缺失不能绕过检查。相同幂等键和完整原正文可以重放已成功回执。

恢复先冻结当前作品，再在同一事务中恢复图及其绘图；不同内容或已删除绘图新增单调递增版本，内容完全相同且活动时复用当前版本。目标历史缺少所声明的版本或绑定不一致时返回 `409 canvas_drawing_history_missing`，不能用最新笔画代替。绘图版本、引用、图及回执的任一写入失败全部回滚。绘图版本与历史绑定不可原地改写；历史引用通过外键保护版本，历史淘汰只级联清除自己的绑定，不清除绘图及媒体。

恢复确认并重新读取图后，前端解除本次恢复涉及的本机删除标记。其它浏览器持有已确认的删除版本时，只有服务端活动绘图的版本严格大于该版本才解除标记；旧版本或相同版本的 GET 不能复活已删除内容。旧缓存没有删除版本或删除回执未知时不猜测解除，仍需明确恢复。私人未提交笔画保留自己的来源版本，读取最新作品不能把旧草稿自动升级成可覆盖的新版本。

节点剪贴板在账号隔离的缓存中记录来源画布键。原节点变体、同项目另一画布及跨项目粘贴从来源读取完整笔画，通过目标画布上传身份保存独立预览/成品和绘图子文档；不把项目权限放宽成跨项目资源直用。单次复制与 ZIP 导出共用整包读取：服务端只捕获一次绘图记录，按该记录的稳定资源 ID 取预览/成品，本机草稿按同一缓存代读取。资源读取前后均检查本机删除标记；旧响应不能越过已确认删除，只有明确恢复的更新版本可以读取。异步回填复核账号、目标 drawingId 和版本，不覆盖已更新的副本元数据。已有不带来源键的旧剪贴板仍仅按原同画布规则处理。

DELETE 尚无 CAS 前提或独立幂等回执，不能将重复删除已删除记录等同于并发保存/删除/恢复的完整重放保证。未知删除回执、上述并发竞态、版本保留/回收，以及导入导出、完整绘图工具和生成引用仍需专项接入和验收。整画布/项目复制现在通过上述首次创建事务交付子文档。

## 画布生成任务与文本流

`T=/canvas-runtime/tasks`。任务接口返回源 camelCase 裸对象，列表返回裸数组；不再包一层 `code/data/msg`。所有读取与操作都要求本人任务、当前项目权限和活动无限画布。项目成员不能查询另一成员的参数、草稿或未绑定产物。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| POST | `T` | `CanvasRuntimeTaskCreate`；首次 202，相同操作/完整正文重放 200 |
| GET | `T` | `projectId?`、`activeOnly?`、`pageSize=1..100`、`clientOperationId?`、`sourceNodeId?` |
| GET | `T/{id}` | 本人任务、真实许可 `canRetry/canResume/canCancel`、结果交付状态与原输出槽 |
| GET | `T/{id}/logs` | 已保存模型调用的状态摘要；当前不是源完整事件日志 |
| POST | `T/{id}/cancel`、`T/{id}/resume` | 复用宿主安全取消/恢复，不创建新供应商请求 |
| POST | `T/{id}/query-provider` | 取回本人失败视频的原供应商任务；有条件地查询并归档已有产物，不提交新生成 |
| GET | `T/{id}/text-deltas?after=0` | `{deltas,textDraft,finalText,complete,status,stage,progress,error}`，每页最多 1000 个增量 |
| GET | `T/{id}/text-events` | SSE `progress/delta/terminal/error`；`after` 与 `Last-Event-ID` 取较大非负整数 |

创建正文包含 `projectId`（画布不透明源键）、`type=canvas_text|canvas_image|canvas_video|canvas_audio`、`operation`、`prompt`、真实十进制字符串 `logicalModelId` 和 `input`。`input.metadata` 必须提供目标 `nodeId`、稳定 `clientOperationId`，可提供 `sourceNodeId/retryOf/attemptGroupId`。来源和目标节点均须先通过真实画布保存确认。可选 HTTP `Idempotency-Key` 必须等于正文操作键；同键不同正文返回 409。服务端冻结请求、来源节点与授权模型，同事务登记任务、绑定和私人任务状态；不会推进共享图版本。

前端保存占位、来源及连线后才准入，按账号/会话代保存固定请求。回执未知时重放相同正文与原键；刷新按操作身份发现同一任务。Config 来源节点按自己批次关联子任务收尾，不把子任务身份写到来源。旧响应不能覆盖换过任务/操作的目标。模型凭据仅由服务器读取，画布请求不能指定密钥、地址或任意供应商参数；尚未实现的参数在准入时明确拒绝，不能静默丢弃。

成功媒体首先由现有执行器检验并归档 MinIO，再交付真实存在的私人素材。`outputs[].outputIndex` 及结果内容项的 `outputIndex` 保留原槽位，不能把稀疏的 0、2 压成 0、1。稳定素材身份为 `generation_` 加 `SHA256("materialize:<taskId>:<outputIndex>")`，重复读取保留用户后续编辑；既有旧身份继续兼容。原版消费器随后调用 `canvas.task.bind`，通过同一写回执直接回填作品并发布媒体；标准模式的候选采用流程保持原样。

文本增量来自供应商实际 SSE，Worker 在短事务中检查执行租约后写入 `canvas_task_text_deltas`。每条最多 64 KiB、每任务最多 2 MiB/4096 条、本人有效增量总量最多 64 MiB；成功保留 24 小时，失败/取消保留 7 天，最终正文仍存任务记录。scheduler 每小时清理过期增量，启用新表后应重启同版 scheduler。`textDraftSequence` 表示详情中完整草稿已包含的最大序号，前端从该游标续读以免重复拼接。客户端不能 POST 增量或直接把执行中任务标成成功。

SSE 每次读取重新核对会话、作者及当前成员权限；撤权/账号变化会关闭观察。断开浏览器连接只停止观察，不取消供应商任务。供应商断流保留实际已存草稿并标记未知提交，不能据此重新提交收费任务。当前尚待完整渠道/RunningHub/协议、所有参数、结构化产物、完整任务日志与一比一操作验收；专项使用受控供应商不代表真实供应商验收。

开启 `onTextDelta/useTextEvents` 的调用者才观察正文 SSE。固定源普通画布文字 executor 未传这两个
选项，保留加载态并在终态后由原结果消费者回填；服务端私人增量可恢复，不表示普通画布节点逐字展示。

OpenAI 图片编辑支持已保存的同项目 `mask`，准入和实际执行均检查资源类型与权限；蒙版须为有透明像素的 PNG，且与首张源图尺寸一致。Canvas 的 multipart 保留源重复 `image` 字段和 `mask`；标准接口继续使用其原字段。图片参考与蒙版在执行内存中读取真实 MinIO 字节，不依赖临时签名 URL。总字节预算与部分源动态限制尚有差异，不能声明完整参考合同已经一致。

### 画布文字图片引用

普通 `canvas_text / operation=text` 节点目前接通 `openai_chat.v1` 的已保存图片引用。
`input.referenceImages[].storageKey` 必须为当前项目可授权的 `resource:<十进制字符串>`；
不接受未保存 URL、Blob 或浏览器内嵌图片。标准模式 `TextInput.messages[].content` 继续为字符串，
不能通过标准接口提交任意多模态 JSON。

准入从本人已保存渠道 profile 的 `capabilityConfig.text` 冻结 `canvas_text_capability`，
不采用本次请求声明的视觉能力，也不按模型名猜测。没有配置时沿用源文字默认最大图片数 0；
图片数、单图字节和提示词长度检查保存的 `references.maxImages/maxImageBytes/promptMaxChars`。
当前画布非视频 DTO 最多 16 张图片，这个阶段限制尚未代表源全部可配置范围。
准入校验实际资源范围、永久 MinIO 定位、MIME 与大小，用闭合 `canvas_text_references` 内部信封保存；
浏览器提供的类型、大小、尺寸与展示 URL 不作为可信媒体元信息。

Worker 沿源对象存储分支在执行时签发短期读取 URL，按原顺序构造当前 user 的
`[{type:text,text}, {type:image_url,image_url:{url}}, ...]`，system 与已有文本历史保持字符串。
签名 URL 只用于本次供应商请求，不写入任务身份、请求归档或画布。MinIO 的供应商读取地址须可由
所选供应商访问；本机受控 HTTP 能读取图片不代表远程供应商已经验收。
PNG、JPEG、WebP、GIF 为本片接通的 MIME；其他图片、文字视频/音频、Responses/Claude/Gemini
多模态、工具和 thinking 仍单独实施。

`textOptions.stream=false` 或保存的 `text.streaming=false` 使用非流请求；Chat 流式请求沿源
增加 `stream_options.include_usage=true`。正文仍使用已有 delta/SSE、本人权限、游标恢复和直接
结果绑定；普通画布文字节点按源等待终态回填，未新增逐字展示。
不会把 reasoning 加入作品。同键图片顺序或内容变化返回 409，读取/重放不提交新生成。

已有绑定模型在新任务准入、选择 adapter 前，按服务器保存的渠道/profile 刷新派生能力缓存。
缓存变化只推进执行配置版本，不修改目录正文、凭据或个人偏好；相同缓存完全无操作。
旧任务继续使用原冻结配置，同键重放直接返回原任务；旧版本画布任务的完成回写不能覆盖新配置缓存。
未绑定的标准配置、停用/已删除模型与不支持的协议继续遵循原权限和拒绝行为。

### 画布视频协议

| 源协议或渠道 | Python 合同 |
| --- | --- |
| `newapi`、`openai-video`、`openai-videos` | `canvas_openai_videos.v1`；`POST /v1/videos` multipart，只将排序后的首张非蒙版图片作为 `input_reference` |
| `newapi-channel-2` | `canvas_newapi_video_generations.v1`；`POST /v1/video/generations` JSON，按合同传递图片、视频和音频参考 |
| 官方 BeefAPI + Seedance 模型 | `canvas_beefapi_seedance_video.v1` 优先于旧协议字段；`POST /videos` flat JSON，参考媒体先完成受控预上传 |

视频请求仍使用上述任务接口。客户端提交稳定 `resource:<id>`；服务端读取当前项目内媒体的真实类型、字节、尺寸和时长，不采用客户端伪造元信息，不把临时 URL 作为媒体身份。画布视频请求最多容纳 30 张图片；实际准入取协议与冻结能力上限，Seedance 2.0 为 9 图/3 视频/3 音频，2.5 为 30 图/10 视频/10 音频，标准 `VideoInput` 仍最多 9 图。编辑、延长等操作须由明确的元数据意图与模型能力共同允许，不能从提示词推断。

自定义 Channel 2 的本地参考在创建任务前返回 `422/reference_media_requires_url`，此时没有任务、执行记录或供应商请求；原界面替换为有效 HTTPS 链接后可用原操作键重新提交。该准入只校验 HTTPS 语法，供应商获取链接；后端实际读取远程媒体时仍使用既有受限 transport。官方 `wan3.0-video` 保留单图 inline 例外，不因协议元数据扩大其他参考类型。

选定服务器目录模型后，视频配置与合法能力选项按规范冻结；`duration/aspectRatio/resolution` 对应 `videoSeconds/size/vquality`。空参数取冻结模型能力默认值；时长枚举/range step、画幅及分辨率按该快照校验，官方单规格分辨率固定到声明值。原请求正文继续决定幂等身份，规范后的 config、能力选项和私有布尔参数决定显示及实际执行；换正文不能重用原键。原全局 `videoArkPrivateAssetUpload=true` 在这三个非 Ark 协议中不触发 Ark 上传。

Seedance 预上传顺序为创建上传、PUT 原字节、完成确认，再提交生成。只有首个上传创建返回 404/501 才允许 inline 降级；中途失败不会继续生成或降级。图/视频/音频分别限 30/200/15 MiB，inline 总载荷含编码开销限 64 MiB。收费 Seedance 提交键由稳定任务/执行身份生成；未知受理不重发，任务投影为 `stage=submission_unknown`，恢复已有供应商 ID 时只查询和下载，不重新上传或创建。

上述三个协议默认首次及后续轮询均为 30 秒，尊重更长 `Retry-After`。连续 404/明确未就绪和损坏响应分别累计三次即停止；成功或另一类响应按源规则重置计数，计数保存在执行记录中。预算耗尽后不再发送轮询。实际下载只对暂时性错误重试，最多三次；非暂时性错误立即停止。下载终止投影为原界面可识别的 `download_failed`；已有确认生成记录和供应商 ID 的画布视频不能通过 `retryOf` 新建付费任务，仍按真实归档清单提供恢复或原 ID 查询。本地取消不补发源实际调用链未使用的供应商 DELETE。受控 HTTP、MySQL、MinIO 与原 UI 验证记录见[实施记录](../plans/2026-10-05-beeftv-implementation-log.md)；完整错误分类、视频供应商兼容与全部 M2 协议仍未验收。

## 本人模型选择与偏好

`GET /canvas-runtime/workspace/model-config` 返回 `{models,channels,preferences,row_version}`。`models` 为本人宿主 AIModelConfig 的安全投影；已绑定渠道的配置不再重复投影为宿主渠道。`channels` 保留源自定义渠道与模型 profile，执行模型的 `logicalModelId` 由服务器分配。宿主渠道使用 `host-<config_id>` 和 `host:<config_id>`，自定义渠道使用自己的稳定 `id` 和 `host:<channel_id>`。两类引用均不携带密钥。

`PUT /canvas-runtime/workspace/model-config` 接受 `{expected_row_version,preferences,channels?}`，返回与 GET 一致的完整回执。省略 `channels` 只保存偏好，空数组清空自定义目录。源 camelCase 字段保留；自定义渠道不可伪造 `host-`、system 或 pinned。每个选中的模型须有明确 capability profile；未选中的额外 profile 可以保留，但不创建执行配置。伪造的 `logicalModelId` 不被采用，删除后重加复用稳定身份。

官方 `beefapi` 是固定的 `scope=user/pinned=true` 服务端渠道，未连接也返回空目录。外部保存只能改变其 `enabled/headers`；地址、模型、profile 和执行身份均取服务端当前目录。省略该渠道或清空自定义数组不会删除它。托管 API Key 只由本人连接密文读取；转录等源目录非生成项保留空能力，不创建可执行配置。源同账号目录合并、换账号替换，手工渠道保留。

渠道公开字段保存在本人 `canvas_model_catalogs.channels_json`，API Key、Secret Key 和全部 header 值保存在独立加密信封；响应返回空值、`hasApiKey/hasSecretKey` 和 `credentialRef`。空秘密值保持旧凭据；明确清除使用 `clearCredentials: ["apiKey","secretKey","headers"]` 中的目标字段。偏好、目录与 backing 配置在同一用户锁和版本事务保存。原版“保存并返回”等待服务端回执，409 保留当前草稿并停止自动覆盖；旧 ACK 只补上与原保存快照仍匹配的服务端身份和脱敏标记，不能覆盖保存期间的新输入。

每个 key 最多 16 KiB，目录请求及合并旧秘密后的保存快照最多 2 MiB。Header 按源规则最多 32 项、名称 128 字节、值 4096 字节、合计 16 KiB，禁止认证覆盖、Cookie、hop-by-hop、代理身份及 `x-canvas-*` 等保留字段。渠道必须使用没有 userinfo、查询、fragment、空白或非法端口的 HTTP(S) 地址；空地址可以作为未执行草稿保存。执行仅消费服务端冻结的加密凭据，在内存中按源自定义 headers 后 Bearer 的顺序传入受限 transport；同源媒体下载可带鉴权，跨源下载不带凭据。签名协议及未实现操作继续明确拒绝。

## BeefAPI 企业连接

`B=/canvas-runtime/beefapi/connection`，只作用于当前账号。生产固定企业源 `https://enterprise.beefapi.com`，HTTP 输入不能改企业地址。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `B` | 返回公开状态、企业根、账户和目录状态；不调用上游、不返回 API Key/deviceCode |
| POST | `B/start` | 开始源设备授权，或复用待完成的密文授权；pending 提供 `userCode/verificationUri/expiresAt` |
| POST | `B/cancel` | 取消当前待授权并清设备码；已连接状态按源保留 |
| POST | `B/disconnect` | 清本人托管 Key/模型及执行许可，尝试上游撤销；保留手工渠道、官方空入口和本人 headers |
| POST | `B/open-wallet` | 返回 `{enterpriseOrigin,walletUrl}`；由网页校验固定根与 `/console/topup` 后打开 |

公开 `state` 采用 `disconnected/pending/connected/cancelled/expired/rejected/revoked/catalog_failed/store_error`。`connected` 必须在密钥已加密落库、企业完成确认和模型目录持久化全部成功后产生；目录失败保留已确认凭据，重试不创建新的设备授权。确认最多五次短暂故障重试；pending/slow_down/拒绝/过期分开处理。设备码及 Key 分离于公开 JSON，ID 始终使用十进制字符串。

必须部署并运行同版 scheduler；它按数据库租约继续授权轮询、确认和目录恢复，页面关闭及进程重启不丢失待处理状态。GET 只读立即返回，原页面两秒轮询不能承担后台授权。新的授权只对新获取目录中的可用 `gpt-6-astra` 初始化助手默认；即使该授权没有 Astra 也记录已处理，后续刷新不能覆盖用户选择。余额按固定源保持 `unknown`，不构造余额探测接口或生成错误后的自动余额变更；源余额辅助函数在该固定版本没有实际调用。目录读取及定时连接核验的 401/403 会标记 revoked，完整运行证据见实施记录。

测试可通过服务端 `CANVAS_BEEFAPI_TEST_ORIGIN` 显式使用 loopback，默认空；非 loopback、路径、查询或片段会被拒绝。前端生产只允许固定官方根，开发测试根须在测试构建中显式指定。测试本机 HTTP 不代表真实企业登录或付费模型验收。

## 协议目录与模型目录读取

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/canvas-runtime/plugins/catalog` | `scope=user.custom-channel` 或源允许 scope，可选 `capability`；返回 `{providers}` |
| POST | `/canvas-runtime/ai/models` | `{baseUrl,apiFormat,apiKey?,headers?,channelId?,credentialRef?}`；返回 `{models}`，不保存配置、不推理 |

官方目录保留固定源 commit 的 85 个包、86 个 provider 的原 manifest、路径及 SHA-256。`catalogAvailable` 表示元数据存在，`executionSupported/enabled` 表示已接入当前 Python 适配器，未接入项带 `unavailableReason`。该标记不保证该协议全部参数与操作已经验收。模型探测通过有限、拒重定向的实际 GET 获取 `/models`；最多 2 MiB、12 秒，只使用模型发现 allowlist。脱敏渠道可复用本人的同地址凭据，换地址不能带走旧密钥。Claude 无目录能力时明确返回 422。目录返回的可见字段按源投影并剔除凭据，不把“能读模型列表”当作生成验收。

## 独立模型测试

`M=/canvas-runtime/model-tests`。原设置页的模型测试不要求项目、画布或节点，不生成虚构的作品与 binding。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| POST | `M` | `{channel,model,mode,prompt,config,textOptions:{stream:false,thinking:false},clientOperationId}`；必需匹配的 `Idempotency-Key`；首次 202、重放 200 |
| GET | `M/{id}` | `{id,status,result?,error?,errorCode?,canCancel}`；成功结果必须来自实际执行与归档 |
| POST | `M/{id}/cancel` | 本人安全取消；终态继续可读取 |

`channel` 为原 ModelChannel draft，可测试尚未保存的渠道。草稿凭据只经加密 test-only AIModelConfig/执行记录冻结供真实执行器使用；测试配置从普通模型列表和默认选择排除。仅服务端标记的本人 personal 测试可执行这种配置，并核对冻结版本与凭据身份；普通停用配置继续拒绝提交。宿主只读投影仅能读取本人的确切模型、key 和实际同地址，跨账号或换地址拒绝。

同操作键异正文、异秘密返回 409。本人 `/tasks` 列表、详情、日志、取消和许可的恢复包含这些测试，省略 `projectId`，`clientContext.source=model-connection-test`；结果媒体使用真实 `resource:<id>` 和受鉴权文件地址。其他账号无法读取测试或下载媒体，测试不会发布到共享画布。原设置按钮停止观察不等于取消已受理任务。完整 RunningHub、签名/多模态协议、所有参考与高级操作、完整日志及真实收费供应商仍待后续验收。

## 资源上传、复制与读取

下表 `R` 表示 `/canvas-runtime/resources`。响应兼容源 camelCase，资源/上传 ID 保持十进制字符串。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| POST | `R` | multipart `file`、`kind=image|video|audio|file`，可选 width/height/durationMs；返回 `{resource}` |
| POST | `R/uploads` | JSON `{fileName,kind,size,width?,height?,durationMs?}`；返回 uploadId/chunkSize/chunkCount/expiresAt |
| PUT | `R/uploads/{id}/chunks/{index}` | 原始字节，单片最多 8 MiB；重复分片须长度与 SHA256 相同 |
| POST | `R/uploads/{id}/complete` | 校验全部分片、重新探测媒体、归档 MinIO 后返回 `{resource}`；重复完成返回相同结果 |
| POST | `R/copies` | `{source_resource_id,canvas_key}`，必须带 `X-Idempotency-Key`；创建目标画布所属项目的独立资源，返回 `{resource}` |
| POST | `R/normalize` | `{canvas_key,resource_ids}`；1–200 个十进制资源 ID，返回 `{resource_map,resource_aliases}`；同项目直用，跨范围按稳定身份创建或恢复独立副本 |
| GET | `R/{id}` | 按资源范围鉴权返回 `{resource}` |
| GET / HEAD | `R/{id}/file` | 鉴权读取真实字节，支持源单 Range 的 200/206/416 语义；file 为附件 |

上传首次请求可带 `X-Idempotency-Key`；前端对原 Blob/源本地存储键保持同一个键。本人同键异参、同分片异内容返回 `409 canvas_upload_conflict`，未知结果用原键重试。超过 50 MiB 的文件走分片；会话保留 90 分钟，单人最多 32 个活跃待完成会话。上传最终对象采用预留资源 ID 构造固定路径，存储响应未知不另建资源。

`X-Canvas-Key` 为上传开始时捕获的 source_key，服务端校验成员和画布归属；没有该头时为本人私人资源。图片/音视频尺寸和时长以 Pillow/ffprobe 的实际探测为准，不相信客户端尺寸。源 `resource:<id>` 保存为稳定身份，响应不暴露 MinIO 内部定位值、凭据或临时签名 URL；文件下载端点每次鉴权。

本人同项目资源进入共享图时直接发布，符合画布原版操作步骤；只进入私人参数不会发布。私人全局文件或其他项目文件须通过独立副本接入共享作品，不能直接放宽资源范围。已有画布的源保存流程已接入自动映射，首次整图创建接入原子准备与回执映射；原版素材选择、图片托盘点击/拖入、图片及连线的跨画布粘贴已通过真实操作验证，其它媒体和绘图子文档仍需专项验收。`variant=playback` 当前仍返回原文件，H.265 播放代理尚未迁移。

`normalize` 仅接受已存在且本人可访问的目标画布。同项目资源返回原 ID；其他资源调用既有复制服务，按当前作者、目标 source_key、输入资源 ID 使用固定键 `canvas-normalize:v1:{canvas_key}:{resource_id}`。请求顺序、批次、重试和刷新不改变该身份；已完成副本在来源撤权后仍可恢复，目标撤权仍拒绝，副本删除墓碑返回 410。中途失败可能已完成部分独立文件，但图尚未提交；重试复用这些文件。操作完成后再次检查目标及全部结果资源的可访问范围。

前端先重放提交日记中原有的在途请求；只有准备下一次新请求时才冻结当前图、每批最多 200 个资源调用归一化。映射和来源全部校验成功后才建立新的不可变提交。实时图只替换仍匹配的资源定位值，复制期间的拖拽、删除和新输入保留；编辑器观察基线及撤销/重做补丁使用同一次映射，不增加用户历史步骤、不清空重做栈。归一化不会改写在途请求的键或正文。首创 `revision=0` 使用上文完整创建的媒体准备事务及恢复流程，不能先保存空图后冒充原完整请求成功。

复制请求的 `source_resource_id` 是十进制字符串，`canvas_key` 是捕获的目标画布 source_key；请求键长度为 1–512 字符且不能全为空白。服务端验证目标为本人可访问的无限画布，来源为本人私人文件或有权读取的项目作品；项目其他成员的未发布文件不可复制。即使来源已经在目标项目，显式复制仍创建独立文件；前端接入时须避免不必要的重复复制。当前来源必须有真实文件类型和非零字节数，缺失时返回 `422 canvas_resource_invalid`。

同键同参恢复相同资源身份，同键异参返回 `409 canvas_upload_conflict`。复制时不持有项目行事务，来源由持久外键保护，完成前重新核验来源、目标权限与来源元数据。`503 canvas_resource_copy_failed` 表示对象存储结果未确认，调用方保留原键与原请求重试；`409 canvas_resource_copy_changed` 表示来源或复制结果已变化，停止旧请求并由用户明确重新复制。并发复制忙时返回 409，待完成复制与上传共享每人 32 个会话限额。

新副本最初仍为私人文件，进入共享图才按原有规则发布；尺寸、时长、MIME、字节数和 SHA256 来自原文件，不带入可能指向原项目的预览清单。完成后释放来源外键，副本具有独立生命周期：原文件被删除或原项目撤权，不影响仍有目标权限的本人重放已完成请求。删除副本后原复制键返回 `410 canvas_resource_deleted`，不能自动换键复活它。

复制使用专用 `copy` 会话和来源快照。到期暂存清理与复制共用有界数据库互斥锁，不能删除正在复制的文件；失败副本到期后可回收未引用字节并释放来源外键，仍保留原请求身份。若来源仍存在且未变化，原键可以恢复到原预留资源 ID。来源快照仅本人可见，不因副本发布而向目标成员公开。

新复制在不可变来源快照中保存当时本人可见的 `resource_ancestors`，副本再次复制后仍可匹配原素材；中间项目撤权不会抹去最终副本已确认的来源。早期没有此字段的记录在本人可访问范围内沿来源继续解析。完成的副本被节点或时间线 directMedia 使用且仍声明原 `assetId` 时，画布保存校验本人素材与副本来源一致，并登记该素材到实际副本的引用；不新增素材，也不修改原素材的项目归属、payload 或 storageKey。不相关的素材/副本配对返回 `409 canvas_asset_resource_conflict`，整个保存回滚；普通跨项目资源引用继续拒绝。

完成后先提交资源元数据再清理分片。清理失败保留记录并记统一日志；`cleanup_canvas_uploads.py` 默认检查，`--apply` 清理已完成分片和到期暂存。已升级资源删除表的环境由 scheduler 每小时执行暂存清理；启动后首次调度也会执行。清理 helper 已在隔离 MinIO 验证，未在业务库执行批量清理。URL 导入、OSS/Ark 协议与完整存储用量接口尚未完成。

彻底删除资源后，原上传身份进入私人删除回执：重复使用原键/原内容，或对原上传会话继续分片、完成，返回 `410 canvas_resource_deleted`；同键异内容仍返回 `409 canvas_upload_conflict`。只有明确的新上传键才会建立新身份。回执在文件回收成功后也保留，防止网络重试复活已删除资源。

## 私人素材库和分类

`A=/canvas-runtime/assets`，`F=/canvas-runtime/asset-folders`。素材提示词、标题和内容仅本人可见；来源项目撤权后该项目素材不可读取。数字形式源素材键仍是不透明 source_key，不能进入标准素材路由。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `A` | 无 page 返回本人摘要 `{assets}`；有 page 返回完整分页及分类计数 |
| POST | `A/batch` | `{ids}`，最多 100 个源素材键；仅返回本人可见项 |
| GET / PUT | `A/{key}` | 读取 `{asset}` / 保存 `{asset:源素材文档}`；PUT 返回摘要回执 |
| DELETE | `A/{key}` | 永久删除，返回 `{id}`；可选 `expectedStatus=archived`，已恢复时返回 409，其他非空条件返回 422 |
| PATCH | `A/folder` | `{assetIds,folderId}`；去空白去重后 1–200 项，全部可见才原子移动；folderId 空串为未分类 |
| GET / POST | `F` | 读取 `{folders}` / 创建 `{name}` 并返回 `{folder}` |
| PATCH | `F/{id}` | `{name}`；名称去首尾空白、1–40 字符，同用户按小写名称唯一 |
| DELETE | `F/{id}` | 返回 `{id}`；清除分类归属并保留素材 |

保存前按原版 [parseAssetRecord](../../frontend/canvas/src/lib/asset-record.ts) 校验六类 `data`，不能只提供 `storageKey`。字段名保留源 camelCase；原版解析器与 Python 共用[合同样本](../../frontend/canvas/tests/fixtures/asset-contract.json)进行回归。

| kind | 必填 data 字段 | 可选 data 字段 |
| --- | --- | --- |
| text | `content` 字符串，可为空 | 无 |
| entity | `definition` 对象 | 无 |
| image | `dataUrl` 字符串、正数 `width/height`、非负 `bytes`、具体 `mimeType`、`storageKey` | 无 |
| video | `url` 字符串、非负 `width/height/bytes`、具体 `mimeType`、`storageKey` | 非负 `durationMs`、布尔 `hasAudio` |
| audio | `url` 字符串、非负 `bytes`、具体 `mimeType`、`storageKey` | 非负 `durationMs` |
| model | `url` 字符串、非负 `bytes`、具体 `mimeType`、非空 `fileName`、`storageKey` | 无 |

数字必须有限，拒绝数字字符串和布尔值；保留源允许的小数，以及视频尺寸为 0 的“未知”语义。MIME 去首尾空白并转为小写；图片、视频和音频须匹配所属种类，也允许源明确支持的 `application/octet-stream`。可选字段省略表示未知，显式 `null` 不合法；未知 `data` 字段按源解析器忽略。`source/note/primaryVersionId/arkAssetId` 若提供须为字符串，`portraitCertified` 须为布尔值；素材 ID 不可全为空白，分类接受五种标准值和源历史别名。

宿主媒体身份仍要求 `resource:` 后接有效非零 uint64 十进制 ID，并由 Service 验证实际资源及权限；此时 `dataUrl/url` 可以是空字符串。仅外部 URL 或浏览器本地键的导入尚未实现，这是已记录的接入差距，不计为完整源合同兼容。后端补齐默认封面、标签和分类，时间戳以服务器为准；不补造缺失尺寸、MIME 或字节数。非法请求返回 422，不能写入新素材、覆盖原内容或改变原资源引用。

分页参数与源一致：page、pageSize（默认 40，最多 120）、kind、category、folderId、uncategorized、status、q、favorite、recent、project、generated。实体类型不进入普通素材分页；active 排除 archived，recent 为最近 30 天。generated 仅图片/视频/音频且来源为“生成任务”或具有字符串 generationEffectKey。分类计数按 status，收藏/最近/项目/生成快捷计数按 active。

分类关系是持久化权威，folderId 在素材读取/分页/回执时投影，不保存在可独立修改的 payload JSON 中。分类与素材写入使用现有按账号划分的数据库 advisory lock 串行化；上传身份预留使用同一锁，不锁定所有审计外键共同引用的 users 父记录，避免与画布自动保存形成循环等待。

素材更新按 BeefTV `asset/library_canvas_guard.go` 校验本人当前画布投影：image/video/audio 节点取首个有效 storageKey/content，时间线 clips.directMedia 依次取 storageKey/url/dataUrl/content。仅当 assetId 与当前素材相同且新素材无法保留对应资源时返回 `409 canvas_asset_resource_conflict`；未使用的封面可移除，保留该资源的其他定位字段仍可通过。既有媒体结构无法解析时返回 `409 canvas_asset_reference_invalid`。检查持有项目锁并使用当前锁定读取，不能因请求较早建立了 MySQL 快照而漏掉刚提交的画布引用。

已登记的项目副本也参与同一素材的替换及删除检查。仅改标题等信息保留原素材；仍被本人当前图使用的副本不能通过更换原素材文件变成错误绑定。检查按项目 ID 顺序锁定原项目和本人可见的副本项目，并与画布副本绑定共用本人素材互斥锁。解除当前图绑定后允许更换来源；历史中的副本仍由真实资源引用保护。本人已撤权的副本关联予以保留，素材更新不能借撤权删除其他项目文件。

嵌套 assetId 与节点顶层 assetId 一样进入本人私人投影；成员共享实际作品和资源，不获得他人的个人素材库绑定。引用索引同时识别 resource 键、源/宿主资源端点 URL，以及源 resourceId/referenceResourceIds 等指定字段中的十进制字符串；不下载这些 URL，也不把任务 ID 或提示词中的普通 URL 当作资源。素材更新同步增删引用关系，移除关系不直接删除物理文件。

永久删除持有本人素材串行锁和所属项目锁，再检查当前资源引用。正在使用的共享图、任一成员私人投影、任务或标准业务引用返回 `409 canvas_asset_in_use`；当前作品已解除引用但保留在历史中的独占资源返回 `409 canvas_asset_history_referenced`。引用检查跨成员只返回是否占用，不返回他人的私人记录。其他素材复用同一资源时，仅删除当前素材并保留资源；非本人资源和标准模式来源文件沿用其原有生命周期，不交给画布清理器。

素材、分类归属、上传回执和可删除资源记录的移除，与 `canvas_resource_deletions` 持久回执同事务提交；任一外键或写入失败会整体回滚。物理文件异步回收，HTTP 成功不等待 MinIO 删除。scheduler 每 10 秒最多处理 32 个到期回执，按行锁跳过其他 Worker 已领取的工作；失败指数退避，最高间隔一小时，不丢弃回执。实际删除前再次检查全部账号、包括归档项目的规范文件记录；同物理位置仍有记录时标记 `retained` 并保留字节。

可在 `backend/` 用 `uv run python scripts/cleanup_canvas_resources.py` 只读检查待执行回执，增加 `--apply` 才执行回收，`--limit` 为 1–1000。清理器只处理可信上传命名空间和配置的 MinIO 桶。源回收站通过 `status=archived` 分页，恢复仍调用 PUT 设置 `confirmed`；清空回收站的逐项删除必须携带状态条件，避免并发恢复后误删。

原版素材托盘在挂载及重新打开时，通过上述接口按 `kind=image&status=active`、每页 100 条读取完整图片库，保留原搜索与点击/拖入操作。读取失败显示就近重试，不把缓存缺失或请求失败当空库。完整读取成功后移除已不存在的旧图片缓存，保留本机待提交编辑和读取期间新增的素材；账号切换或组件卸载会取消旧读取。真实浏览器已覆盖 101 项跨页搜索、服务端删除后的刷新以及一次 503 故障注入后的显式重试。

托盘插入与原快捷键跨画布粘贴，继续使用保存前资源归一化：同项目复用实际媒体，跨项目建立独立副本，原 `assetId` 和原素材文件保持。已验证图片节点、相对位置、真实拖出的连线、复制后的新节点/连线 ID、真实文件字节以及全新浏览器上下文恢复。节点剪贴板的 sessionStorage 键按账号划分，系统标记必须与当前账号所保存的标记一致，旧无归属槽不作为恢复来源；账号变化时清除内存剪贴板。该本机隔离不授予任何后端资源权限。

仅外部 URL 的素材导入、旧源 ID 映射以及尚未迁入的生成/绘图/工作流引用仍待补齐。已有画布的保存归一化及副本来源恢复已接入，原版选择弹窗、等待期间拖拽/删除/撤销/重做，以及提交失败后刷新重试已有浏览器验证；首次整图创建的验收范围见实施记录。上述托盘/剪贴板证据限于已列出的图片场景，尚未覆盖所有媒体类型、绘图子文档和特殊 URL 路径。新业务接入时必须同时建立资源引用与删除保护，不能仅凭当前删除链路测试通过认定完整素材功能已验收。
