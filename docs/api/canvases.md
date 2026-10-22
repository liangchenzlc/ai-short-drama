# 无限画布接口

当前已接通双模式项目、画布文档、历史、个人状态、宿主统一模型配置与独立模型测试、资源上传下载、私人素材、绘图基础、本人项目文件夹及生成任务准入/基本回填。完整媒体加工、生成参数与协议、绘图工具、导演、助手和插件执行仍在迁移中，不能把本文列出的接口视为完整 BeefTV 功能验收。

统一前缀 `/api/v1`。认证、Origin、CSRF、项目所有者检查沿用现有中间件。`X-Canvas-Actor` 是画布打开时的账号 ID，仅用于校验当前会话是否发生切换，不能授予身份或权限；不匹配返回 `409 canvas_actor_changed`，不撤销其他窗口刚登录的有效会话。

独立画布 HTML 只渲染 `/canvas-app/canvas/{source_key}` 编辑器；整站首页、画布库、项目、资产、设置和外部 Agent 页面源码已移除，旧管理 URL 转宿主。编辑器退出回 `/projects`，模型配置进入 `/ai_config` 并携带安全画布返回地址；正常导航先等偏好和作品保存回执，409 保留草稿。当前 Python 将画布不存在、归档和无访问权限隐藏为 `404 not_found`；CSRF 或其他写规则 403 不表示整张画布撤权。自定义前端端口须与后端 `PUBLIC_ORIGIN` 一致，不能停用 CSRF 修复来源不匹配。

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
| GET | `/canvas-write-receipts/{key}` | 本人操作回执；归档后的删除回执仍可恢复，非所有者不可读取 |
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

规范画布 ID 与所有数据库版本均为十进制字符串；源 `source_key`、节点键、边键保持原版不透明字符串。`source_document` 内使用源 camelCase 合同，外层 DTO 使用 snake_case。`workspaceProjectId` 是宿主项目归属；退役的源 `projectId` 绑定字段不再接受；`workspaceProjectId` 继续用于原生画布归属。

本人文档与历史读取增加信封字段 `resource_aliases: {"副本资源ID":["来源资源ID", ...]}`。只返回当前请求者本人已完成、仍有访问权限的副本来源关系，且仅包含该文档实际引用的副本；共享 `GET C` 返回 `{}`，其他成员不会获得作者的来源链。工作区 `include_documents=true` 按页批量查询并分发到各文档，不逐画布查询；摘要列表省略此字段。所有键和值均为十进制字符串，来源关系不写入 `source_document`、共享节点或素材 payload。

前端在把当前文档、历史或分页文档交给源修复流程前恢复来源关系，运行时缓存按账号及登录 epoch 隔离。保存前归一化取得的来源关系先写入本人、当前画布的本机提交日记 `resourceAliases`，再投影副本 ID；重新认证后恢复同账号草稿时读取并校验该日记，避免图提交失败后刷新把副本误认成新素材。本机 `resourceAliases` 不直接上传，不进入图请求或共享文档；创建回执自身包含服务端计算的 `resource_aliases`。来源关系只用于素材身份匹配，不授予来源文件读取权限，不改原素材 storageKey。

当前文档校验节点/边唯一键、父子循环、端点归属、有限几何值、正尺寸、凭据字段、32 MiB 上限和嵌套深度。文档分解到关系表；不同时维护第二份可独立修改的当前全图 JSON。共享 metadata 使用显式字段清单，新字段默认进入作者私人投影。

`type=skill` 节点的 `content`、`previewContent`、`richText` 属于本人模板，和 `skillSnapshot` 一起保存到 `canvas_node_user_states`，不进入共享节点；普通 text 正文仍共享。只修改 Skill 私人正文不推进共享画布版本，不覆盖其他成员的本人模板。当前文档、工作区预览/完整文档及历史读取都先遮蔽旧共享 Skill 正文，再叠加当前账号可信的本人状态。

旧共享 Skill 正文没有可证明的作者身份，`created_by` 或 `updated_by` 不能作为归属依据。存在这些字段的旧当前文档、旧历史快照在保存、快照创建或恢复时返回 `409 canvas_legacy_skill_body_private`，整次事务回滚，保留原始记录和本人草稿；不能根据遮蔽后的读取结果自动覆盖旧正文。升级旧环境前须由管理员核对作者，将已确认正文迁至对应本人节点/历史状态，并在同一维护事务中清除共享字段、同步历史摘要及校验值；不得将未知正文分配给项目所有者或最后移动节点者。2026-10-09 已只读核对本机 `short_drama`，相关旧节点/历史数量均为 0，本次未执行数据改写或 DDL。

原画布保存仍按409暂停自动提交并保留草稿；历史恢复对这一特定原因保留后端作者确认说明，不将其改写为“当前画布已有更新”。普通版本409/428沿用原冲突提示，恢复失败不读取或替换当前作品。该错误反馈经过实际恢复函数的Node回归；未将旧数据管理迁移或缓存清理写成已完成。

图保存、首次创建/复制和历史快照按字段语义规范媒体展示值：image/video/audio/model/panorama 的 content/url/dataUrl 有稳定 resource 身份时使用同源受鉴权文件地址，保留播放 variant、proxy 和片段，不持久化原 origin 或签名参数。drawingPreviewUrl、directorCoverUrl 使用各自的预览资源键；没有稳定身份的临时派生预览省略，不能把待生成预览指向旧原图。文字正文、提示词和未知字段不做通用 URL 替换。

新写入的主要媒体为 Blob/data URL 且没有稳定资源身份时返回 `422 canvas_media_not_persisted`，保留草稿。旧图和历史在私人字段投影完成后兼容读取；下一次成功保存会真实清理旧图行，不能仅在返回值掩盖旧数据。前端内容比较和三方合并使用同样的规范值，避免同一文件的 Blob 与文件 URL 产生假冲突或假未保存。已持久化、结果未知的首次请求和在途提交仍按原键、原正文重放，不回写规范后的正文；服务端原始请求审计可能保留旧客户端提交值，不能据此改变幂等哈希。外部 URL 可显式调用下文 `resources/import` 取得永久身份；文档保存不自动下载任意外部 URL。

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

视口 CAS 将 `expected_viewport` 和目标 `viewport` 在当前已校验画布的 ORM 查询中转换为 MySQL JSON 的数值表示，再与持久化值精确比较。MySQL 的双精度 JSON 序列化可能改变末位数字，例如 `453.20626631853787` 读回为 `453.2062663185378`；这一表示差异不能误报其他窗口冲突。响应仍确认原请求目标，重复该目标不推进本人版本；实际不同的旧位置仍返回409，不引入浮点容差或自动覆盖。

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

前端重新读取本人规范文档，写入原提交日记和本机草稿后才移除回收卡片。服务器已恢复但响应丢失时，当前弹窗保留卡片；明确重试仍使用原键，重新打开或刷新目录会移除已恢复记录，不自动重发恢复。如果本机已经出现未保存编辑，保留编辑并提示处理，不用旧回收快照覆盖新内容。

彻底删除使用 `canvas-recycle-purge:{source_key}:{revision}` 固定键，服务端先锁项目、画布，再核对回执和当前归档代次，写入私人 `canvas.recycle.purge` 回执。与恢复竞争只允许一方成功：恢复先成功时旧处置返回 409，处置先成功时恢复返回 `410 canvas_recycle_purged`。同键重放返回原处置结果，没有再次修改作品的副作用；失败回滚回执，并释放窄归档作用域。带 Actor 的业务 Session 禁止改写或删除既有画布写回执，批量写同样受保护。前端移除卡片落盘失败时恢复原列表，保留原快照与 `archiveKey`；如果期间已有新列表，则不以旧列表覆盖。这一接口提交不可恢复处置，物理回收由后台分阶段执行，响应不表示全部数据或 MinIO 字节已擦除。

新版 scheduler 每 10 秒检查已处置的精确归档代次，按稳定回执 ID 每页 100 条扫描至末页；失败或延期项单轮只尝试一次，不挡住后页。清理器只允许内部维护 Session，重新按项目、画布、回执取得锁，核对处置作者的原归档回执、源键及版本；仅归档、已恢复、再次归档的新代次或标准模式不进入回收。从画布绑定与工具提交沿实际重试外键检查全部后代，包含没有画布绑定的通用重试。存在未结束生成/本地/渲染任务、持有执行者的工具租约，或任何 `sent/unknown` 生成记录时保留图载荷，不自行取消或重发任务。

清理节点、连线、时间线、导演子文档、绘图版本、整图快照及其共享/私人投影和引用，擦除回执内部的 `$archive_document` 恢复预览；画布身份和公开回执结果、请求 hash、版本保留，继续支持原处置重放、状态查询及恢复 410。内部 `$recycle_cleanup` 版本 2 标记代表图载荷及允许清理的本人执行载荷已同事务提交；旧版本 1 会再次进入检查，普通源文档中同名字段不能代替完成标记。这不是全部物理数据或字节已擦除的证明。

同一事务擦除处置作者本人已完成创建的 `canvas_creation_attempts.request_json` 正文副本。
准备记录没有 `canvas_id`，必须用冻结结果中的十进制 `project_id/id`、`source_key`、
创建操作及目标项目核对当前作品，再验证同作者、原键、原 hash 的公开创建回执与结果
保持一致。缺失或不一致的创建回执、未完成准备或未释放来源 pin 会使本次清理失败并
回滚。仅归档、已恢复、其他作者或冻结目标不匹配的请求保持原生命周期；尚未完成
创建的请求不被擦除。已标记版本 2 的旧图若仍有符合条件的创建正文，会再次进入
检查；正文清空且没有待处理的历史创建媒体后不再重复处理。此维护操作禁止带 Actor
的业务 Session 调用。

创建身份、请求 hash、状态、公开结果和准备资源的来源/校验和/祖先快照保留，
原键同内容继续重放原公开结果，同键更换内容仍冲突，不重新创建图或媒体。无公开
回执的已完成准备重放，按项目、画布、准备记录的顺序加锁；已处置目标不能复活。
当前创建流程建立正式的 copy 上传与已释放来源记录，图清理后符合原引用规则的
副本由原删除队列回收，独立素材和其他作品仍保护媒体。早期缺少上传身份的创建
媒体按同一作者、冻结资源映射、原准备记录和精确处置回执核对。目标必须保持同项目、
原校验和/长度/元数据及 `canvas/resources/{id}` 定位；无外部引用才在图清理事务内
退役规范记录。原 `canvas_resource_deletions` 以 `creation_resource_id` 和
`disposal_receipt_id` 保留 RESTRICT 出处，两者与上传身份互斥；上传 ID/hash 均为空，
不伪造上传或分片。已清正文的旧 v2 及仍被独立素材、其他作品/历史或复制 pin 保护的
历史媒体继续检查，引用释放后可接续退役。来源与祖先快照、创建 hash/公开回执保持，
原创建键仍重放原结果；该路径没有上传键的 410 墓碑语义。字节删除器再次核对创建
作者、原映射和公开回执，失败或丢失响应按原定位重试。

第二阶段只擦除处置者本人的终态执行载荷。生成任务及全部调用须具有匹配的项目、画布和 `canvas_node/canvas_tool` 冻结出处；其他作者、来源不匹配、Agent 管理或批次任务，以及被标准模式/Agent 审计外键引用的记录，保留原生命周期。允许清理的请求、来源快照、配置中的执行字段、正文/增量、诊断原文和冻结凭据被擦除；只保留任务与调用身份、hash、类型/模型标识、供应商任务 ID、状态、时间及数值用量。媒体归档清单保留稳定定位、ID、校验和及实际元数据，移除下载来源/内联密文；媒体产物索引及独立私人素材继续保护规范资源，媒体结果行仅擦除内容副本。已清理任务记录 `canvas_work_disposed`，停止调度，`can_retry/can_resume` 均为 false；原键可重放既有任务，异内容仍冲突，新键不能从该任务恢复或重新生成。最后一张图归档后项目不可读时，原通用 API 仍返回 404，不能把内部维护读取当成账号访问许可。已绑定结果的公开回执保持不可变。

本人没有执行租约的工具状态、来源快照及阶段请求被擦除；关联受保护任务的工具运行保留。本人终态本地/渲染任务按精确处置回执擦除请求、来源、计划、结果正文和诊断，保留身份、hash、状态和时间，并以 `disposal_receipt_id` 保留原处置出处。只有无租约的终态、已清空冻结载荷且记录 `canvas_work_disposed` 时，才释放本地输入、两类输出及渲染来源外键；普通任务继续要求来源和成功输出。其他作者和外部审计保护的任务保持原生命周期。当前深度/渲染产物已有正式分片上传，符合原引用规则时沿既有上传删除队列回收，不伪造上传身份。旧 v2 已清图却仍保留本地引用的记录会补处理。已处置任务的结果不可用，不能恢复或重试；归档画布下的原键和任务请求仍按活动画布权限返回 404，不据此新建任务。清理先 flush 本事务释放的引用，再核对是否可退役上传；提交失败整体回滚。

完成图/执行载荷清理后，scheduler单独检查本人已退役任务的生成媒体。以原处置代次、
任务/调用、产物及真实归档清单核对完整生成对象路径、校验和和实际元数据，不能用
MediaFile ID代替路径中的产物asset_id，也不能伪造上传身份。独立素材、其他作品/
历史、来源任务、标准/Agent审计和复制pin仍保护媒体；这些引用释放后继续检查。
无外部引用时，同事务删除本人结果行、产物索引及规范媒体，并写专用
`canvas_generation_media_deletions`，保留任务/hash、归档证据和公开绑定回执。
字节删除独立执行并重新校验可信出处，任何账号规范记录仍保护相同定位或ID；
保护、失败或删除后响应丢失均保留pending并按原定位重试，不提交新的供应商任务。
此阶段不改变HTTP处置响应，也不代表创建/本地渲染媒体或全部物理数据已经删除。

图清理同一事务退役本次删除者本人、归属该画布上传或上述真实创建准备、且仍在可信 `canvas/resources/{id}` 命名空间中的规范资源。其他画布/历史、任意账号私人素材、文件夹封面、复制/创建 pin、保留的生成/本地/渲染引用以及标准模式/Agent 的实际引用都继续保护资源。未结束上传沿用原过期回收，其他作者、私人范围及缺少上述可信出处的资源保持各自生命周期；已处置的本人生成媒体由上述独立阶段核对。上传身份或创建/处置出处及精确定位写入已有 `canvas_resource_deletions`，图清理失败整体回滚；MinIO I/O 由原删除队列独立执行，失败或删除成功但响应丢失仍按原定位重试，任意账号的规范媒体/二进制记录继续保护相同定位或 ID。受保护审计、其他缺乏出处的早期媒体及全套未保存子文档回收尚未完成，不能宣称整张作品的所有物理数据已经删除。

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

`T=/canvas-runtime/tasks`。任务接口返回源 camelCase 裸对象，列表返回裸数组；不再包一层 `code/data/msg`。所有读取与操作都要求本人任务、当前项目权限和活动无限画布。非所有者不能访问该项目、任务或产物。

任务列表和本人任务详情遇到合法保存引起的画布版本竞争时，最多执行两次完整事务尝试。只有项目与可信来源均未变化、仅画布版本变化时才重新读取；首轮事务完全回滚后，列表重新查询全部任务与画布，详情重新查询本人任务绑定与画布，均核验当前权限并重新规划项目的排序锁，不复用旧预检。第二轮仍有版本竞争则返回 `409 canvas_binding_changed`。来源变化、撤权和其他错误不被自动忽略；该重读不会重新提交生成任务或调用供应商。

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

表情编辑保留固定源的局部合成步骤：供应商产物只用于目标人脸区域，消费器以已保存的原图引用为底执行羽化合成，上传独立派生图并登记本人素材，再通过 `canvas.document.commit` 保存作品与 `attach-node` 效果标记。正常生成和关闭窗口后的恢复共用此路径，派生上传键按本人、任务及产物槽固定；供应商原图、裁切参考、任务和参数继续私人可见，只有进入共享图的合成作品按项目权限发布。来源删除、任务或输入变化、账号切换及未确认保存均不能用供应商整图代替合成结果。

文本增量来自供应商实际 SSE，Worker 在短事务中检查执行租约后写入 `canvas_task_text_deltas`。每条最多 64 KiB、每任务最多 2 MiB/4096 条、本人有效增量总量最多 64 MiB；成功保留 24 小时，失败/取消保留 7 天，最终正文仍存任务记录。scheduler 每小时清理过期增量，启用新表后应重启同版 scheduler。`textDraftSequence` 表示详情中完整草稿已包含的最大序号，前端从该游标续读以免重复拼接。客户端不能 POST 增量或直接把执行中任务标成成功。

SSE 每次读取重新核对会话、作者及当前成员权限；撤权/账号变化会关闭观察。断开浏览器连接只停止观察，不取消供应商任务。供应商断流保留实际已存草稿并标记未知提交，不能据此重新提交收费任务。当前尚待完整渠道/RunningHub/协议、所有参数、结构化产物、完整任务日志与一比一操作验收；专项使用受控供应商不代表真实供应商验收。

开启 `onTextDelta/useTextEvents` 的调用者才观察正文 SSE。固定源普通画布文字 executor 未传这两个
选项，保留加载态并在终态后由原结果消费者回填；服务端私人增量可恢复，不表示普通画布节点逐字展示。

OpenAI 图片编辑支持已保存的同项目 `mask`，准入和实际执行均检查资源类型与权限；蒙版须为有透明像素的 PNG，且与首张源图尺寸一致。Canvas 的 multipart 保留源重复 `image` 字段和 `mask`；标准接口继续使用其原字段。图片参考与蒙版在执行内存中读取真实 MinIO 字节，不依赖临时签名 URL。总字节预算与部分源动态限制尚有差异，不能声明完整参考合同已经一致。

### 画布内置 RunningHub

三个内置协议使用`canvas_runninghub_workflow_{image,video,audio}.v1`，仅本人画布及独立
模型测试可用；标准生成、Agent不借此开放协议。节点准入要求本人插件
`runninghub-workflow-provider`已启用。`input.execution`只能传所选
`provider/kind/interfaceType/name/workflowId/webappId`，字段值通过
`input.metadata.workflowParameters`提交；身份须匹配保存的`runtime_profile.runninghub_workflow`。
字段、随机值、本人凭据和来源版本在准入时冻结，浏览器不能提供地址、密钥或替换结构。

根地址去除尾部`/openapi/v2`或`/openapi`并保留代理路径。Workflow提交
`POST /task/openapi/create`，App提交`POST /task/openapi/ai-app/run`；积分Key放JSON正文。
轮询为`POST /task/openapi/outputs`并保留原`taskId`。参考图片、视频、音频和蒙版先校验
授权永久资源及字段槽位，再读真实字节，由企业级上传Key经`POST /task/openapi/upload`
multipart上传。缺Key、非法槽位或资源不能触发收费create。

缺回执或断连为unknown，同键重放不重新create。已知任务的临时查询错误按预算和服务端
许可恢复原ID；视频保留连续错误计数和有限下载重试。归档失败恢复save，不重新生成。
产物下载不附两个Key或自定义鉴权头，即使同源。实际MIME决定类型，仅空/通用MIME按源
后缀表回落；不支持MIME和空字节持久略过，全无有效媒体终态
`workflow_output_type_unsupported`。交付全部图片、最后video及最后audio，mode按
audio/video/image优先；保留原槽位，早期未选video/audio不进入画布产物、私人素材库或绑定。

固定源list菜单隐藏工作流创建入口，本包保持隐藏；原UI专项从已保存Config开始。
声明式协议的独立合同见下节。原UI专项使用受控HTTP、真实MySQL/MinIO、直接执行器
和Chrome；消息投递另由`test_canvas_runninghub_broker.py`使用随机队列命名空间、
真实RabbitMQ和独立Celery进程验证。收费供应商和企业授权按用户指示暂缓，
本机受控供应商证据不替代这两项或固定源完整视觉对照。

### 画布声明式 RunningHub

本人已保存模型的`runtime_profile.protocol=runninghub-workflow`选择独立的
`canvas_runninghub_workflow.v1`，仅支持视频及画布/独立模型测试。鉴权为Bearer；
地址遵循源APIURL规则，保留代理及显式版本前缀，缺省补`/v1`。
创建为`POST /task/openapi/create`，查询为`GET /task/openapi/status`，
固定manifest没有任务ID查询参数、路径占位或正文，宿主不额外添加。

扩展参数使用`input.metadata.providerOptions["runninghub-workflow"]`；允许
`version/model/input/workflow/client_id/webhook/webhook_events_filter`七个键。
`input/workflow`中的公开JSON保持完整结构，不裁剪动态字段，也不允许凭据或非有限数值。
空值按源模板省略/回落，0和false保留。`workflow`映射到供应商`prompt`；未指定非空
`input`时使用提示词和三组参考数组，媒体顺序保持。持续时间、比例、分辨率、音频和
水印没有manifest映射，不添加到创建正文。原UI把本人保存的模型`default_options`
合入本次命名空间；服务端冻结实际提交参数，不从新配置覆盖已受理任务。

默认能力仍是9张图片、无参考视频/音频、文生与图生视频；本人保存的能力可明确覆盖。
永久资源先核对项目/作者及真实元数据，执行期内联原字节；HTTPS引用保持URL。
数据请求总量沿源64MiB限制。查询保留`id/request_id/prompt_id`及原任务ID，媒体只读取
`response.data`，支持源URL别名与dataURL/裸base64；pending加媒体升成功，processing
仍等待。`error.code`按源错误语义处理，成功无媒体失败，不伪造`/content`。
全部返回视频先归档，交付第一个视频；其他槽位不能绑定或进入私人素材/画布产物。
同源下载携带冻结Bearer和自定义头，跨源不携带。视频轮询保留连续404/格式错误三次
停止及有限下载重试；失败后使用`query-provider`查询原任务，归档失败按许可恢复save。
未知提交同键不重新create，不能自动或显式重试收费请求。

归档插入媒体的数据库事务先锁项目再锁任务，避免与本人任务列表的相反锁顺序
形成死锁；外部下载和MinIO写入仍在该事务之外。确定时序并发专项同时检查
画布列表和全局列表，不以自动重试409替代无死锁断言。

### 画布 Claude 普通文字与图片引用

本人已保存的宿主配置使用 `runtime_profile.api_format=claude / protocol=claude-api`，
普通 `canvas_text / operation=text` 和独立 `canvas_model_test` 可使用 `claude_messages.v1`。
准入冻结已保存的凭据、文字能力和 `default_options`；排队后修改配置不替换任务快照。
标准生成和 Agent 继续沿原协议许可，不借此开放 Claude。

请求使用 `/v1/messages`，保留服务地址中的代理前缀，接受已含完整端点的地址；
鉴权为 `x-api-key`。默认 `anthropic-version=2023-06-01`，显式自定义版本头保留，
同名大小写变体归为一个请求头。`max_tokens` 依次使用内部显式 `max_output_tokens`、
本次 `input.metadata.providerOptions["claude-api"]`、服务端冻结的默认选项、4096。
非空本次 `config.systemPrompt` 成为顶层 `system`，否则使用供应商默认 `system`；
历史 `system` 不重复拼入 `messages`。`textOptions.stream=false` 或保存能力
`text.streaming=false` 均使用非流 JSON。

JSON 仅拼接各 `text` 块，SSE 接收实际 `content_block_start / text_delta` 正文；
两者都将 thinking 独立保存为本人 `resultJson.reasoning`，不进入正文增量或共享作品。
流读取覆盖 UTF-8 分块、CRLF、多行 data、BOM、JSON 回退及有效正文 EOF；
格式错误、仅有 reasoning 或正文截断保留实际私人草稿和错误状态，不自动二次提交。
普通Claude图片引用已按保存的文字图片能力接入；执行期用永久资源签发URL，构造
`image/source.type=url`块，图片位于当前user文字之后。视频和音频仍拒绝，普通
Gemini多模态仍未开放。`thinking=true`仅在保存的`text.thinking`严格为true时接通；
Claude使用`{type:enabled,budget_tokens:1024}`，`max_tokens`必须大于1024。编辑器工具
沿下文独立合同处理，不借普通文字接口放行任意工具。
普通节点仍在终态后由原消费者回填，受控 HTTP/MySQL/原 UI 验证不等于收费供应商验收。

### 画布 Gemini 普通纯文字

普通 `canvas_text / operation=text` 和独立 `canvas_model_test` 使用
`gemini_generate_content.v1`。本人已保存配置必须为 `runtime_profile.api_format=openai`，
协议为 `gemini-generate-content` 或 `google-gemini-generate-content`；这对应固定源
hosted 渠道派生为 OpenAI 格式的可达分支。`api_format=gemini` 保留源 custom 普通文字
gate：节点受理返回 `422 canvas_generation_protocol_unsupported`，宿主模型测试返回
`400 generation_unsupported_protocol`，不因品牌或模型名自动放行。

请求使用非流 `/v1beta/models/<escaped model>:generateContent`，保留代理前缀并替换
基础地址末尾的已知 API 版本。模型键按 Go `url.PathEscape` 的 segment 规则编码；
斜线、问号和井号不能变为新路径、query 或 fragment。凭据仅在执行时转换为
`x-goog-api-key`，不发送 Bearer 或 query key；自定义请求头沿宿主加密凭据规则处理。
历史 system 被过滤，assistant 映射为 model，当前 `config.systemPrompt` 成为独立
`systemInstruction`；本次无指令时保留源 JSON null 形状。

准入冻结服务端 `default_options` 为 `canvas_text_defaults`。本次选项只从
`input.metadata.providerOptions["gemini-generate-content"]` 读取，使用闭合的普通
`generationConfig`、`safetySettings` 和 `cachedContent` 合同；未知字段、工具、thinking
及多模态拒绝。即使源节点请求 `textOptions.stream=true`，本分支仍使用非流 HTTP，
不发送 stream 字段，也不向 OpenAI SSE observer 传递响应；运行时私人草稿为空，节点保持
loading，终态由原消费者直接回填。

仅取 candidate 0，将其正文 text 逐段 trim 后无分隔拼接。意外返回的 `thought=true`
独立进入本人 `resultJson.reasoning`，不进入正文或共享图；这是为私人过程隔离做的明确
适配，与源 manifest 拼接全部 text 的行为不同。受控 HTTP/MySQL/Chrome 验证不代表
Gemini 真实供应商或企业授权验收；仅免费 Agnes 文本/图片/视频真实验收已获授权，其他供应商协议、收费模型、真实音频与企业授权仍暂缓，结果以专项验收记录为准。

### 画布文字图片引用

普通 `canvas_text / operation=text` 节点接通 `openai_chat.v1`、`openai_responses.v1` 的已保存图片引用，
并增加这两种默认 manifest 的视频引用。`input.referenceImages[].storageKey` 与
`input.referenceVideos[].storageKey` 必须为当前项目可授权的 `resource:<十进制字符串>`；
不接受未保存 URL、Blob 或浏览器内嵌图片。标准模式 `TextInput.messages[].content` 继续为字符串，
不能通过标准接口提交任意多模态 JSON。

准入从本人宿主配置的 `runtime_profile.capability_config.text` 冻结 `canvas_text_capability`，
不采用本次请求声明的视觉能力，也不按模型名猜测。没有配置时沿用源文字默认最大图片/视频数 0；
图片数、单图字节和提示词长度检查保存的 `references.maxImages/maxImageBytes/promptMaxChars`。
视频检查 `references.maxVideos/maxVideoBytes`；当前文字 DTO 分别最多16张图片和16个视频，
这个阶段限制尚未代表源全部可配置范围。
准入校验实际资源范围、永久 MinIO 定位、MIME 与大小，用闭合 `canvas_text_references` 内部信封保存；
浏览器提供的类型、大小、尺寸与展示 URL 不作为可信媒体元信息。

Worker 沿源对象存储分支在执行时签发短期读取 URL，按原顺序构造当前 user 的
`[{type:text,text}, {type:image_url,image_url:{url}}, ..., {type:video_url,video_url:{url}}, ...]`，
图片在视频之前，各组保持输入顺序。Chat 的 system 与已有文本历史保持字符串；Responses
`input` 过滤 system、保留非空 user/assistant 历史，配置系统提示词放入独立 `instructions`。
这是固定源默认 manifest 的 wire；`input_text/input_image/input_video` 属于源另一 fallback 分支，
不能据受控联调声称原生 Responses 供应商已经接受该默认形状。
签名 URL 只用于本次供应商请求，不写入任务身份、请求归档或画布。MinIO 的供应商读取地址须可由
所选供应商访问；本机受控 HTTP 能读取图片不代表远程供应商已经验收。
PNG、JPEG、WebP、GIF 为已接通的图片 MIME；视频为 MP4、WebM、MOV，并冻结服务端检测的正时长。
Claude普通图片已接通相同永久图片资源信封，按Claude URL图片块执行；其视频拒绝。
文字音频、其他图片MIME及Gemini普通多模态仍拒绝。Chat／Responses／Claude的普通
文字及编辑器工具thinking均须保存的`text.thinking=true`能力；Chat使用
`reasoning_effort=medium`，Responses使用`reasoning={effort:medium,summary:auto}`，
Claude使用1024 token thinking预算并要求更高的输出上限。Gemini thinking按固定源拒绝。
准入冻结原能力，后改配置不影响已受理任务；未声明能力时422且不新增任务／记录／binding。

`textOptions.stream=false` 或保存的 `text.streaming=false` 使用非流请求；Chat 流式请求沿源
增加 `stream_options.include_usage=true`。正文仍使用已有 delta/SSE、本人权限、游标恢复和直接
结果绑定；普通画布文字节点按源等待终态回填，未新增逐字展示。
Responses 流使用 `response.output_text.delta` 与完成事件；非流解析非空顶层 `output_text`，
兼容既有 `output` 消息数组。双协议图片/视频HTTP/MySQL/MinIO专项48项通过，
原UI自动图片/视频、显式引用顺序/重复去重/缺失拒绝及三份批量已分别通过。
绘图引用使用已保存版本的永久render；双协议原UI通过空绘图拒绝、真实笔画、精确资源复用、
终态及关闭旧窗口后的预览/笔画恢复，未重复上传同版render。
份数按源为1–15，默认1可缺省；各份独立任务和操作键，普通节点终态直接回填。
上述受控验证不覆盖其他文字协议或全部绘图组合；仅免费 Agnes 文本/图片/视频真实验收已获授权，其他供应商协议、收费模型、真实音频与企业授权仍暂缓，结果以专项验收记录为准。
不会把 reasoning 加入作品。同键图片顺序或内容变化返回 409，读取/重放不提交新生成。

新任务准入、选择 adapter 前，按服务器保存的宿主 `runtime_profile` 刷新派生能力缓存。
缓存变化只推进执行配置版本，不修改配置正文、凭据或个人偏好；相同缓存完全无操作。
旧任务继续使用原冻结配置，同键重放直接返回原任务；旧版本画布任务的完成回写不能覆盖新配置缓存。
未绑定的标准配置、停用/已删除模型与不支持的协议继续遵循原权限和拒绝行为。

### 编辑器工具请求

文字任务可提交`input.agentRequests.canonical`，含`messages`、唯一固定`tools`、`toolChoice`与`systemPrompt`。当前固定工具为`optimize_prompt`及九个审美阶段工具；schema由固定源快照验证，客户端不得改工具名或参数定义。`metadata.source=prompt-optimizer|art-critique`、目标节点与阶段必须一致；仅图片引用，`referenceImages`顺序须与canonical相同，图片定位仅永久`resource:<uint64十进制ID>`，服务端核对权限和实际字节。普通历史、音视频、mask、客户端transport或不同config均拒绝。合法布尔thinking仅在本人保存的`text.thinking=true`能力且为Chat／Responses／Claude时允许；Chat thinking移除`tool_choice`，其他两协议沿上述静态参数。Gemini thinking拒绝，Claude输出预算不够时拒绝。审美prepare／approve／execute共用这项能力门禁，不绕过阶段批准。

工具使用本人已保存文字模型与四种已实现协议。保存配置的非流模式仍为非流；Chat/Responses/Claude流回复须有协议闭合证据，Gemini为非流。成功`resultJson`含`text`、`toolCalls`及私人`reasoning`，仅工具调用、空text也可成功。工具请求与结果不改变来源节点loading或共享图，不生成`CanvasResult`，物化与bind返回422。创建、列表、详情、日志和文本增量保持本人隔离；前端优化结果通过源“采用优化后的提示词”显式写入。

截断、非法工具JSON及断连保留unknown许可，不自动重新POST。Chat兼容回退只对明确400/422工具选择拒绝启用：强制选择、auto、省略最多三次，共用原deadline；普通认证/服务端错误与其他协议不触发回退。秘密与签名URL不归档，固定schema在解析和脱敏后再次校验。审美直接提交`/tasks`仍返回`409 canvas_tool_approval_required`；只有下列持久阶段批准链可以准入。

审美与已有分镜节点生成使用`/canvas-runtime/creation-runs`，接通`kind=art-critique`及`kind=storyboard`，不开放旧助手proposal或共享图提交接口：

| 方法与相对路径 | 合同 |
| --- | --- |
| POST、GET `/creation-runs`；GET `/{id}` | 创建、本人列表与详情；创建带`clientKey`、`canvasId`和固定身份`state`，同键同输入重放，不同输入409 |
| POST `/{id}/claim` | `{expectedEpoch, owner}`；45秒租约，CAS成功后推进`executionEpoch`；被其他活跃窗口持有或旧epoch返回409 |
| POST `/{id}/heartbeat`、`/{id}/release` | `{executionEpoch, owner}`；前端每15秒续租，过期或旧窗口不能续租/释放新租约 |
| POST `/{id}/submissions/prepare` | `{executionEpoch, owner, itemKey, request}`；唯一`itemKey=stage:<固定工具名>`，冻结请求摘要，阶段不同输入409 |
| POST `/{id}/submissions/approve` | `{executionEpoch, owner, submissionIds}`；仅批准本人本运行已准备阶段，最多九个且不能重复 |
| POST `/{id}/execute` | `{executionEpoch, owner, submissionId}`；未批准409，新受理202，已受理回执重放200并返回同一任务 |

创建运行前必须已保存审美节点，且恰好连接一张本项目永久图片。服务端冻结图片稳定定位、元数据、真实checksum及本人模型执行配置/凭据摘要；摘要不含可读取秘密。新阶段准备、批准和执行均复核来源、连线、模型配置与个人启用状态。每次运行最多九个固定阶段，任务操作键为`canvas-tool:<运行数据库ID>:<工具名>`；改变同阶段输入须开始新分析。

分镜运行的`clientKey`为`storyboard:<稳定操作键>`，`state={kind:"storyboard",nodeId,sourceNodeId,model,logicalModelId}`；来源与目标须为同一已保存Script。客户端先完成来源保存，再创建/claim/prepare；`itemKey="storyboard"`，任务`clientOperationId`与运行`clientKey`一致。取消不批准也不执行；租约与批准沿上表，来源、画风、资产目录或模型改变时阻止新执行，已受理/unknown回放只读取原任务。直接`POST /tasks`不能绕过分镜批准。

任务为`type="canvas_text",operation="storyboard"`，`input`携带`mode="text"`、相同`prompt`、`canvasAssets`、`requirements`、`projectStyle={presetId,title,prompt,profileJson?}`、`characters`、`shotCount`和`shotDurationSeconds`。镜数范围0–100、每镜秒数0–60，0表示自动；引用媒体、history、工具canonical等不能混入该请求。服务端从本人已保存文档重建上下文，拒绝客户端伪造的目录或画风。项目角色使用已保存角色卡的`assetId/versionId/name/definition`，创建、准备、批准和最终准入均校验可信来源、两侧项目权限、素材关系、当前版本及封面来源；`canvasAssets`保留角色节点身份和`type="character"`。同一真实角色重复卡保留，同名不同素材或版本返回422 `canvas_storyboard_character_ambiguous`，不会按名称静默覆盖身份。

模型只可返回闭合`{title,rows}` JSON，镜号从1顺序递增、每镜1–60秒，指定镜数/时长必须一致；字段类型、角色名及资产绑定均验证，不能注入节点ID、图片/视频关联或状态。非法结果使私人任务failed，保留本人正文，零作品回填。成功`resultJson`包含服务端验证的`title/rows`和原`text`；行ID固定为`shot:<十进制任务ID>:<顺序>`，角色名映射为当前画布的 `characterNodeId`。绑定复核冻结的原行与已保存角色定义；删卡或角色内容变化时保留私人结果，拒绝覆盖镜头。合法绑定在同一图事务更新原Script、标题及分镜，删除失效逐镜连线并保留旧媒体节点。绑定回执、仅cookies恢复及重复消费复用原行身份；共享分镜作品，运行/正文/生成偏好仅本人可读。该合同与标准模式候选采用分开，遵循无限画布直接回填边界。

批准核对、任务创建及阶段任务关联在同一事务内完成；事务提交后重新读取任务详情，避免并发等待锁时的旧快照。阶段请求、批准时间及任务回执不能替换，普通HTTP作用域ORM写入不能伪造授权。已受理阶段在停用工具后仍可观察原回执；unknown恢复只查询原任务，不重新调用供应商。运行、阶段、报告及reasoning保持本人隔离，项目成员权限不能代替作者权限。

部署先补[011-canvas-tool-runs.sql](../database/migrations/2026-10-05-infinite-canvas/011-canvas-tool-runs.sql)，应用不自动迁移。本机业务库`short_drama`已实际创建`canvas_tool_runs`、作者更新时间索引及`canvas_tool_submissions`，迁移检查为ready/无缺项/无漂移；没有修改既有业务记录。

个人工具状态使用GET `/canvas-runtime/plugins/status`与PUT `/canvas-runtime/plugins/{plugin_id}/activation`，写入`{ "enabled": true|false }`，保留源camelCase状态合同。仅支持`prompt-optimizer`、`ai-art-critique`，默认关闭，复用本人偏好JSON；成员不读取或修改他人状态，模型偏好保存不会清除它。停用拒绝新的优化请求，原受理同键重放仍返回原任务。

宿主只显示可达的AI审美开关。固定源CSS隐藏提示词标题栏，优化/放大唯一入口位于该栏，保持隐藏；优化器组件与relay合同不构成可达原UI验收。44项工具/状态/模型偏好、原画布开关恢复1项、持久授权/四协议九阶段及迁移27项已有证据。原审美UI另通过取消观察后的仅cookies恢复：继续原run和五任务，随后九阶段完成，终态报告再次恢复；原SVG定位与复制提示词可用，私人报告及任务不对成员开放，原图不变。完整动态运行政策/存储统计仍待R22移植；受控供应商和直接执行器不代表AMQP、收费供应商或模型审美质量；仅免费 Agnes 文本/图片/视频真实验收已获授权，其他供应商协议、收费模型、真实音频与企业授权仍暂缓，结果以专项验收记录为准。

### 画布音频协议

本人保存的 `api_format=openai` 音频模型可使用 `openai-audio`（`openai_speech.v1`）
或 `async-audio`（`canvas_async_audio.v1`）。同步协议 POST `/v1/audio/speech` 返回二进制；
异步协议 POST `/v1/audio/tasks`，按原供应商 ID GET `/v1/audio/tasks/{id}`。
正文冻结 `model/input/voice/response_format/speed` 和非空 `instructions`；
兼容模型的指令不依赖 OpenAI 模型名猜测，标准无画布 envelope 的默认 WAV 保留。

异步结果接受固定 manifest 的六个 URL 路径、内联 data URL 和媒体对象/数组，
音频节点取首个产物；完成但没有媒体地址时通过 `/v1/audio/tasks/{id}/content` 下载，
同源保留本人冻结 Bearer/header，并发送 `Accept: audio/*`，跨源媒体不继承认证。
MP3、WAV、Opus、AAC、FLAC经过实际 FFmpeg 解码后保存为 MinIO 永久资源。
PCM按冻结格式保留原始字节和`audio/pcm`，不猜测采样参数或时长、不转封装；
Chrome原播放器无法解码时显示就近错误并禁用进度。成功回执保持本人隔离，
只有原消费者完成 bind 后才进入共享画布。

异步首次轮询立即调度，后续间隔 2.5 秒，并受冻结任务预算限制；
固定源非视频声明式 policy 没有开启 `RetryTransient`，查询错误停止自动轮询。
未知受理不自动重新提交或开放 `retryOf`。投递中断若保有原供应商 ID 和安全恢复许可，
继续原任务的 poll；已确认生成的音频归档失败只恢复 save，不允许重新付费生成。
17 项受控 HTTP/MySQL/MinIO 集成与 186 项相邻单测已通过，
原音频 UI 三项和调度单项另为 4 passed：关闭旧窗口后仅 cookies 恢复、
原生成/播放/键盘进度、终态再次恢复和 unknown 无重试均已实际验证，
字节/数据库/权限后置断言执行，零页面错误/失败请求；受控执行器不是 AMQP 或收费供应商验收。
两协议六格式及unknown另有41项实际联调，包含13个原UI场景；容器格式实际播放、
PCM原错误态、仅cookies恢复及数据库/字节/权限断言执行，未调用收费供应商。

MiniMax Speech的空音色、OpenAI音色别名或“中文”按可信模型解析为
`male-qn-qingse`，自定义音色保留。MiniMax Music解析为空后继续manifest回落。
`input.metadata.providerOptions["openai-audio"|"async-audio"]`仅支持
`voice/response_format/speed/instructions`；字段类型及范围在准入时校验。
保存的`default_options`随模型冻结，本次同名选项覆盖该默认；画布音色、格式和
非零语速优先于命名空间，空指令与音乐的空音色才回落，零语速标记回落到命名空间或1。
未知字段与非法类型返回`400 generation_unsupported_parameters`，不产生任务或
供应商请求；`capabilityOptions`不能代替该入口。
其他协议、模型声音质量、完整批次和一比一对照仍未完成。

### 画布视频协议

| 源协议或渠道 | Python 合同 |
| --- | --- |
| `newapi`、`openai-video`、`openai-videos` | `canvas_openai_videos.v1`；`POST /v1/videos` multipart，只将排序后的首张非蒙版图片作为 `input_reference` |
| `newapi-channel-2` | `canvas_newapi_video_generations.v1`；`POST /v1/video/generations` JSON，按合同传递图片、视频和音频参考 |

视频请求仍使用上述任务接口。客户端提交稳定 `resource:<id>`；服务端读取当前项目内媒体的真实类型、字节、尺寸和时长，不采用客户端伪造元信息，不把临时 URL 作为媒体身份。画布视频请求最多容纳 30 张图片；实际准入取协议与冻结能力上限，标准 `VideoInput` 最多 9 图。编辑、延长等操作须由明确的元数据意图与模型能力共同允许，不能从提示词推断。

自定义 Channel 2 的本地参考在创建任务前返回 `422/reference_media_requires_url`，此时没有任务、执行记录或供应商请求；原界面替换为有效 HTTPS 链接后可用原操作键重新提交。该准入只校验 HTTPS 语法，供应商获取链接；后端实际读取远程媒体时仍使用既有受限 transport。

`agnes-video-2.5-flash` 使用 `canvas_openai_videos.v1`，稳定参考仅在服务端同时配置
`MINIO_PUBLIC_ENDPOINT` 与显式 `MINIO_PROVIDER_PRESIGN_EXPIRY` 时开放；缺失配置仍在
创建任务前返回上述 422。资源必须属于当前项目，真实类型、尺寸、字节及首尾节点映射
继续校验。首次 Worker submit 重新核对授权和媒体后，将已冻结的原索引解析为临时
HTTPS 签名，按原节点选择输出 `first_frame/last_frame` 或参考组；签名只进入提交副本，
不成为媒体身份、幂等来源或持久化参数。纯结构校验不生成 `.invalid` 占位 URL，最终
wire 拒绝未解析引用及 `.invalid`。unknown 不二次 POST，后续 poll/save 不重新签名。
POST 前短事务保存输入 URL 和敏感签名参数的单向指纹，事务结束后才发送请求；
缺失或损坏指纹的稳定参考任务以 `reference_guard_unavailable` 停止，不重新签名补全。
提交、轮询及归档前均拒绝输入 URL 或敏感签名值回显，包括同 query 换路径及换参数名，
错误为 `reference_output_rejected`；输入签名不进入输出归档的 `source_cipher`。
POST 后仍保留安全供应商 ID 与未知受理状态，轮询拒绝回显不自动重试；
正常独立视频输出保持原归档行为。检查有 URL、字符及节点上限，不扫描 base64。
这些合同及模拟测试不证明供应商已读取首帧；公网 GET 和视频首帧内容必须独立验收。

选定服务器目录模型后，视频配置与合法能力选项按规范冻结；`duration/aspectRatio/resolution` 对应 `videoSeconds/size/vquality`。空参数取冻结模型能力默认值；时长枚举/range step、画幅及分辨率按该快照校验，官方单规格分辨率固定到声明值。原请求正文继续决定幂等身份，规范后的 config、能力选项和私有布尔参数决定显示及实际执行；换正文不能重用原键。原全局 `videoArkPrivateAssetUpload=true` 在这三个非 Ark 协议中不触发 Ark 上传。

参考素材内联（RunningHub 等）限图/视频/音频 30/200/15 MiB，inline 总载荷含编码开销限 64 MiB。

上述视频协议默认首次及后续轮询均为 30 秒，尊重更长 `Retry-After`。连续 404/明确未就绪和损坏响应分别累计三次即停止；成功或另一类响应按源规则重置计数，计数保存在执行记录中。预算耗尽后不再发送轮询。实际下载只对暂时性错误重试，最多三次；非暂时性错误立即停止。下载终止投影为原界面可识别的 `download_failed`；已有确认生成记录和供应商 ID 的画布视频不能通过 `retryOf` 新建付费任务，仍按真实归档清单提供恢复或原 ID 查询。本地取消不补发源实际调用链未使用的供应商 DELETE。受控 HTTP、MySQL、MinIO 与原 UI 验证记录见原 BeefTV 实施记录（已归档）；完整错误分类、视频供应商兼容与全部 M2 协议仍未验收。

## 本人模型选择与偏好

模型创建、编辑、默认、启停和删除统一使用宿主 `/ai_config` 与 `/ai-model-configs`。新增字段、脱敏、凭据更新语义及迁移见[统一模型配置](model-runtime.md)。旧 `/canvas-app/settings` 深链转宿主；在画布内进入配置前等待偏好和作品保存，失败或 409 保留草稿并阻止离开。

`GET /canvas-runtime/workspace/model-config` 返回 `{models,channels,preferences,row_version}`。`models` 包含本人未删除的宿主 AIModelConfig，包括已迁移的旧绑定配置；附完整运行 profile、默认状态、扩展凭据存在标记、可信 `credential_source` 与 `selection_aliases`。`channels` 为兼容空数组，不再提供另一套可编辑目录。前端将配置投影为只读 `host-<config_id>`，`logicalModelId` 始终为稳定配置 ID；该投影的 system 标记不代表全局共享或管理员模型。

`PUT /canvas-runtime/workspace/model-config` 只接受 `{expected_row_version,preferences}`，返回与 GET 一致的回执。带 `channels` 的旧请求明确拒绝，包括空数组；不能通过偏好接口创建、清空或覆盖模型。源 camelCase 偏好保留，409 停止自动覆盖并保留草稿。

旧 `channel::model` 由服务器别名恢复到原配置 ID，节点、重试及个人默认不按同名模型猜测。无法恢复、停用或删除的明确选择提示不可用，不自动更换供应商。宿主模型的名称、启停、删除由本人管理，地址、身份与 profile 由宿主配置维护。

API Key 保存在宿主原加密字段；Secret Key 和全部 header 值在服务端第二信封加密，只返回存在标记。执行仅消费服务端冻结凭据，在内存中传入受限 transport；同源媒体下载可带鉴权，跨源下载不带凭据。签名协议及未实现操作继续明确拒绝。个人画布默认与标准业务 `context_key` 偏好分开保存。

## 协议目录与模型目录读取

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| GET | `/canvas-runtime/plugins/catalog` | `scope=user.custom-channel` 或源允许 scope，可选 `capability`；返回 `{providers}` |
| POST | `/canvas-runtime/ai/models` | `{baseUrl,apiFormat,apiKey?,headers?,channelId?,credentialRef?}`；返回 `{models}`，不保存配置、不推理 |

官方目录保留固定源 commit 的 85 个包、86 个 provider 的原 manifest、路径及 SHA-256。`catalogAvailable` 表示元数据存在，`executionSupported/enabled` 表示已接入当前 Python 适配器，未接入项带 `unavailableReason`。该标记不保证该协议全部参数与操作已经验收。模型探测通过有限、拒重定向的实际 GET 获取 `/models`；最多 2 MiB、12 秒，只使用模型发现 allowlist。脱敏渠道可复用本人的同地址凭据，换地址不能带走旧密钥。Claude 无目录能力时明确返回 422。目录返回的可见字段按源投影并剔除凭据，不把“能读模型列表”当作生成验收。

## 独立模型测试

`M=/canvas-runtime/model-tests`。宿主配置页面复用独立模型测试，不要求项目、画布或节点，不生成虚构的作品与 binding。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| POST | `M` | `{channel,model,mode,prompt,config,textOptions:{stream:false,thinking:false},clientOperationId}`；必需匹配的 `Idempotency-Key`；首次 202、重放 200 |
| GET | `M/{id}` | `{id,status,result?,error?,errorCode?,canCancel}`；成功结果必须来自实际执行与归档 |
| POST | `M/{id}/cancel` | 本人安全取消；终态继续可读取 |

`channel` 保留原 ModelChannel draft 合同。宿主对已保存模型发起测试，引用本人的确切配置、同地址与保存凭据；旧 draft 合同仍由服务端安全校验。草稿凭据只经加密 test-only AIModelConfig/执行记录冻结供执行器使用；测试配置从普通模型列表和默认选择排除。仅服务端标记的本人 personal 测试可执行这种配置，并核对冻结版本与凭据身份；普通停用配置继续拒绝提交。跨账号或换地址拒绝复用保存凭据。

同操作键异正文、异秘密返回 409。本人 `/tasks` 列表、详情、日志、取消和许可的恢复包含这些测试，省略 `projectId`，`clientContext.source=model-connection-test`；结果媒体使用真实 `resource:<id>` 和受鉴权文件地址。其他账号无法读取测试或下载媒体，测试不会发布到共享画布。原设置按钮停止观察不等于取消已受理任务。完整 RunningHub、签名/多模态协议、所有参考与高级操作、完整日志及真实收费供应商仍待后续验收。

## 资源上传、复制与读取

下表 `R` 表示 `/canvas-runtime/resources`。响应兼容源 camelCase，资源/上传 ID 保持十进制字符串。

| 方法 | 路径 | 合同 |
| --- | --- | --- |
| POST | `R` | multipart `file`、`kind=image|video|audio|file`，可选 width/height/durationMs；返回 `{resource}` |
| POST | `R/import` | JSON `{url,kind,width?,height?,durationMs?}`，必须带 `X-Idempotency-Key`，可选 `X-Canvas-Key`；有界下载公开 HTTP/HTTPS 原文件，返回 `{resource}` |
| POST | `R/uploads` | JSON `{fileName,kind,size,width?,height?,durationMs?}`；返回 uploadId/chunkSize/chunkCount/expiresAt |
| PUT | `R/uploads/{id}/chunks/{index}` | 原始字节，单片最多 8 MiB；重复分片须长度与 SHA256 相同 |
| POST | `R/uploads/{id}/complete` | 校验全部分片、重新探测媒体、归档 MinIO 后返回 `{resource}`；重复完成返回相同结果 |
| POST | `R/copies` | `{source_resource_id,canvas_key}`，必须带 `X-Idempotency-Key`；创建目标画布所属项目的独立资源，返回 `{resource}` |
| POST | `R/normalize` | `{canvas_key,resource_ids}`；1–200 个十进制资源 ID，返回 `{resource_map,resource_aliases}`；同项目直用，跨范围按稳定身份创建或恢复独立副本 |
| GET | `R/{id}` | 按资源范围鉴权返回 `{resource}` |
| POST | `R/{id}/playback` | 准备本人 H.264/AAC 兼容预览，可带 `X-Canvas-Key` 核对画布权限；返回 `{resource}`，不替换原件 |
| GET / HEAD | `R/{id}/file` | 鉴权读取真实字节，支持源单 Range 的 200/206/416 语义；file 为附件 |

视频资源响应可含 `playbackStatus: processing|ready|failed`、`playbackTaskId` 和 `playbackError`。
准备任务按作者、原资源定位/校验值/字节数和编码配置 `h264-aac-v1` 去重，原资源 ID、
storageKey 和无 variant 下载内容保持稳定。`GET/HEAD R/{id}/file?variant=playback` 只返回
就绪副本，未就绪返回 `409 canvas_playback_not_ready`；同样鉴权并支持 Range，ETag 以
`:pb` 区分。任务由 scheduler 和 render Worker 执行，依赖 FFmpeg/ffprobe 和 MinIO。
写对象前持久登记 `playback_staging`，取消、租约中断或进程重启后由调度器继续清理未登记
文件，已登记的成功输出不会误删。前端用认证客户端读取 Blob，解码失败才准备兼容副本，
每 2.5 秒观察、最多 120 次；卸载/切换停止观察，显式重试沿原任务恢复。

上传首次请求可带 `X-Idempotency-Key`；前端对原 Blob/源本地存储键保持同一个键。本人同键异参、同分片异内容返回 `409 canvas_upload_conflict`，未知结果用原键重试。超过 50 MiB 的文件走分片；会话保留 90 分钟，单人最多 32 个活跃待完成会话。上传最终对象采用预留资源 ID 构造固定路径，存储响应未知不另建资源。

`X-Canvas-Key` 为上传开始时捕获的 source_key，服务端校验成员和画布归属；没有该头时为本人私人资源。图片/音视频尺寸和时长以 Pillow/ffprobe 的实际探测为准，不相信客户端尺寸。源 `resource:<id>` 保存为稳定身份，响应不暴露 MinIO 内部定位值、凭据或临时签名 URL；文件下载端点每次鉴权。

URL 导入先核验本人及目标画布权限，再沿既有 SafeTransport 校验 DNS、地址和每次跳转，
下载结束由原上传服务再次核权。禁止 URL 用户密码、fragment、任意调用者请求头或
Cookie；下载不转发当前账号凭据。公开下载默认拒绝私网，显式管理员允许主机沿既有
transport 配置处理，不修改全局网络策略。上限为 50 MiB，超限返回
`413 canvas_upload_too_large`，提示下载后分片上传；空文件和非法媒体返回422，不伪造
尺寸或时长。地址/跳转不允许返回422；其他下载失败返回
`503 canvas_resource_download_failed`，公开详情仅包含安全原因码，不回显来源 URL。

同作者同键同来源与字段的 ready 请求直接返回原永久 ID，来源过期或变动不再触发GET。
来源和字段摘要加入原 multipart 声明，不存储 URL 原文或签名查询。未完成请求若原
MinIO 对象已经存在，重检原字节并沿预留 ID 完成，不重新下载；尚未存储时再下载，
变动字节被原请求摘要拒绝409，保留原预留身份。同键异来源/字段返回409，同键并发
使用独占导入锁，忙时保留原请求重试；不持有项目、上传行或用户全局锁下载文件。
已彻底删除资源的原导入键返回410，不换键复活。导入成功仍沿原私人/共享发布规则，
只有加入共享作品后成员才能读取；跨项目沿原独立副本归一化。

默认画布本地分支的外部图片先在浏览器fetch，再按原文件上传；只有非本地分支的
`uploadImage` 会先请求此导入接口。API导入与默认原界面上传恢复的验证分开记录，
不能据此关闭全部外部URL菜单、源动态大小/配额或H.265门禁。后续已补原画布
音视频节点的显式保存重传：保留本人原File/声明/幂等键、素材确认后回填，16独立
场分批通过；立即图片、时间线等其他离线媒体及全部并发仍未由节点恢复关闭，
详见[音视频验收](../plans/2026-10/2026-10-10-画布音视频离线稿重传验收.md)。

本人同项目资源进入共享图时直接发布，符合画布原版操作步骤；只进入私人参数不会发布。私人全局文件或其他项目文件须通过独立副本接入共享作品，不能直接放宽资源范围。已有画布的源保存流程已接入自动映射，首次整图创建接入原子准备与回执映射；兼容副本只用于预览，不改变这些资源归属和发布规则。

`normalize` 仅接受已存在且本人可访问的目标画布。同项目资源返回原 ID；其他资源调用既有复制服务，按当前作者、目标 source_key、输入资源 ID 使用固定键 `canvas-normalize:v1:{canvas_key}:{resource_id}`。请求顺序、批次、重试和刷新不改变该身份；已完成副本在来源撤权后仍可恢复，目标撤权仍拒绝，副本删除墓碑返回 410。中途失败可能已完成部分独立文件，但图尚未提交；重试复用这些文件。操作完成后再次检查目标及全部结果资源的可访问范围。

前端先重放提交日记中原有的在途请求；只有准备下一次新请求时才冻结当前图、每批最多 200 个资源调用归一化。映射和来源全部校验成功后才建立新的不可变提交。实时图只替换仍匹配的资源定位值，复制期间的拖拽、删除和新输入保留；编辑器观察基线及撤销/重做补丁使用同一次映射，不增加用户历史步骤、不清空重做栈。归一化不会改写在途请求的键或正文。首创 `revision=0` 使用上文完整创建的媒体准备事务及恢复流程，不能先保存空图后冒充原完整请求成功。

复制请求的 `source_resource_id` 是十进制字符串，`canvas_key` 是捕获的目标画布 source_key；请求键长度为 1–512 字符且不能全为空白。服务端验证目标为本人可访问的无限画布，来源为本人私人文件或有权读取的项目作品；项目其他成员的未发布文件不可复制。即使来源已经在目标项目，显式复制仍创建独立文件；前端接入时须避免不必要的重复复制。当前来源必须有真实文件类型和非零字节数，缺失时返回 `422 canvas_resource_invalid`。

同键同参恢复相同资源身份，同键异参返回 `409 canvas_upload_conflict`。复制时不持有项目行事务，来源由持久外键保护，完成前重新核验来源、目标权限与来源元数据。`503 canvas_resource_copy_failed` 表示对象存储结果未确认，调用方保留原键与原请求重试；`409 canvas_resource_copy_changed` 表示来源或复制结果已变化，停止旧请求并由用户明确重新复制。并发复制忙时返回 409，待完成复制与上传共享每人 32 个会话限额。

新副本最初仍为私人文件，进入共享图才按原有规则发布；尺寸、时长、MIME、字节数和 SHA256 来自原文件，不带入可能指向原项目的预览清单。完成后释放来源外键，副本具有独立生命周期：原文件被删除或原项目撤权，不影响仍有目标权限的本人重放已完成请求。删除副本后原复制键返回 `410 canvas_resource_deleted`，不能自动换键复活它。

复制使用专用 `copy` 会话和来源快照。到期暂存清理与复制共用有界数据库互斥锁，不能删除正在复制的文件；失败副本到期后可回收未引用字节并释放来源外键，仍保留原请求身份。若来源仍存在且未变化，原键可以恢复到原预留资源 ID。来源快照仅本人可见，不因副本发布而向目标成员公开。

新复制在不可变来源快照中保存当时本人可见的 `resource_ancestors`，副本再次复制后仍可匹配原素材；中间项目撤权不会抹去最终副本已确认的来源。早期没有此字段的记录在本人可访问范围内沿来源继续解析。完成的副本被节点或时间线 directMedia 使用且仍声明原 `assetId` 时，画布保存校验本人素材与副本来源一致，并登记该素材到实际副本的引用；不新增素材，也不修改原素材的项目归属、payload 或 storageKey。不相关的素材/副本配对返回 `409 canvas_asset_resource_conflict`，整个保存回滚；普通跨项目资源引用继续拒绝。

完成后先提交资源元数据再清理分片。清理失败保留记录并记统一日志；`cleanup_canvas_uploads.py` 默认检查，`--apply` 清理已完成分片和到期暂存。已升级资源删除表的环境由 scheduler 每小时执行暂存清理；启动后首次调度也会执行。清理 helper 已在隔离 MinIO 验证，未在业务库执行批量清理。URL 导入已接入上述有界原文件合同，源动态导入政策、OSS/Ark 协议与完整存储用量接口尚未完成。

该脚本同时检查首次整图创建的到期准备对象。每轮固定过期截止时间，按稳定 ID 每页
100 条读取，每项仍在原副本锁下重验 pending 状态、有效期、规范媒体/二进制记录和
预留对象命名空间。单项失败保留原身份并继续后页；回收更新 updated_at 或规范记录
持续保留页首对象时，后面的独立对象仍会被处理。删除字节前先将准备态改为 pending
并撤销复制确认；删除响应丢失后仍可安全重试，原创建请求恢复到同一个预留媒体 ID。
已完成创建、未到期准备对象及仍被规范记录保留的字节受原有检查保护。

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

宿主媒体身份仍要求 `resource:` 后接有效非零 uint64 十进制 ID，并由 Service 验证实际资源及权限；此时 `dataUrl/url` 可以是空字符串。外部 URL 须先通过上述导入接口取得永久身份；素材写入不会自动下载 URL 或读取浏览器本地键，完整旧源导入仍有接入差距。后端补齐默认封面、标签和分类，时间戳以服务器为准；不补造缺失尺寸、MIME 或字节数。非法请求返回 422，不能写入新素材、覆盖原内容或改变原资源引用。

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

尚未取得永久身份的外部 URL 素材、旧源 ID 映射以及尚未迁入的生成/绘图/工作流引用仍待补齐。已有画布的保存归一化及副本来源恢复已接入，原版选择弹窗、等待期间拖拽/删除/撤销/重做，以及提交失败后刷新重试已有浏览器验证；首次整图创建的验收范围见实施记录。上述托盘/剪贴板证据限于已列出的图片场景，尚未覆盖所有媒体类型、绘图子文档和特殊 URL 路径。新业务接入时必须同时建立资源引用与删除保护，不能仅凭当前删除链路测试通过认定完整素材功能已验收。

## 时间线只读编译

`POST /api/v1/canvas-runtime/timeline/render-plan` 接受源 camelCase 合同
`{timeline,sources?,options?}`，认证后直接返回语义计划，不排队、下载媒体、写入数据库或调用
模型。保留 `X-Canvas-Actor` 捕获账号检查；匿名返回 401，账号变化返回 409。来源元数据
仅参与此次规划，实际渲染仍须重新核验媒体权限与探测信息，不能由该接口授予资源访问权。

- `timeline.version=2`，轨道 `visible` 缺省为真、`muted` 缺省为假；隐藏轨片段跳过内容
  校验，但所有轨道和片段 ID 仍必须非空且不重复，片段不能引用不存在的轨道。未知 UI 字段忽略。
- `sources` 缺省或 null 时，从 `directMedia.storageKey=resource:<opaque-id>` 提取来源；
  显式数组（包括空数组）要求每个可见媒体匹配 `nodeId`、资源纯 ID、`resource:ID` 或
  `directMedia.id`，按此顺序选择。不解析原生文件路径或外部 URL。
- `options` 缺省为 1920×1080、30fps、44100Hz、烧录字幕；尺寸须为偶数，最大
  7680×4320，fps 为 1–120、采样率为 8000–96000。0 使用源默认值。
- 时间为整数毫秒，总时长不超过 12 小时；不按 30fps 量化。video/image 按开始时间和
  ID 排序，跨轨视觉重叠返回 400，空隙补 gap。独立音频保留混音、静音、0 音量、来源
  裁剪和淡入淡出；已知非图片来源允许 100ms 时长容差，明确缺少所需音视频轨时拒绝。
- 字幕去首尾空白、稳定排序并生成末尾双换行的 SRT；`burnSubtitles=false` 清除字幕及
  SRT，但保留时间线总时长。可见 text clip 与纯字幕/空时间线沿固定源规则拒绝。

计划 `version=1`，包含 `output/durationMs/segments/audio/subtitles`。未使用的 audio 和
subtitles 为 null；空 `subtitleSrt` 省略，段落的空身份、0 裁剪/淡入淡出和 false muted
字段按 Go `omitempty` 省略，`hasAudio` 始终存在。业务拒绝返回 400 和稳定
`canvas_timeline_<reason>`；非法字段类型返回 422。前端 `compileTimelineRenderPlan`
复用宿主 HTTP，失败继续上抛，导出不得自行改用另一套语义编译。

只读计划夹具通过不等于成片渲染验收；原生FFmpeg及固定源成对产物的实际证据见实施记录。

## 时间线私人渲染任务

`POST /api/v1/canvas-runtime/timeline/renders` 接受
`{projectId,clientOperationId,timeline,options?}`，返回202和源任务中心合同。
`projectId` 是当前画布的 `source_key`；`clientOperationId` 必须为8–128字符ASCII稳定键。
若提供 `Idempotency-Key`，须与请求字段一致。同作者/键/内容返回原任务，内容或画布变化
返回 `409 canvas_render_operation_conflict`；不能用新键重试一个回执未知的原请求。

编译时不接收客户端 `sources`、URL、探测事实或凭据；可见片段必须引用本人可访问的
当前项目资源或本人私有媒体，`resource:` 后为非零uint64十进制字符串。跨项目来源即使
作者可访问也须先复制。准入冻结编译计划、实际媒体定位、摘要、字节数及当时画布版本，
保留来源真实外键。时长受 `RENDER_MAX_DURATION_MS` 限制，默认一小时。

任务类型/操作为 `timeline_render`，provider/model 为 `local`/`ffmpeg`；不使用AI任务表。
详情、列表、日志、取消及显式恢复沿 `/tasks` 合同，只有作者及当前项目权限可读。
queued取消立即终止，running取消由执行器检查并停止；failed/cancelled恢复保留原ID和
冻结请求，仅增加消息版本。重复消息及旧租约不得覆盖结果。普通ORM写入不能伪造或
改写渲染快照、来源引用和完成回执，HTTP也不能开启系统执行会话。

后台复用现有render队列，重新检查当前账号、项目、画布权限并按冻结字节校验来源。
成片使用固定上传键 `canvas-render:<task-id>:0` 和持久分片上传，先恢复已有归档或完整
分片，再决定编码；完成前核验实际SHA-256、字节数、音视频轨、尺寸和时长。
成功的 `resultJson` 含字符串 `resourceId`、`fileName`、`size`、`durationMs` 和可选
`subtitleSrt`。成片仅本人可见，明确放入共享图后才发布；关闭前端观察不等于取消任务。

持久准入、本地执行器与独立Celery/AMQP、实际FFmpeg/MinIO专项已通过；证据和未验
范围见实施记录。原画布“导出成片/生成新片段”沿固定源使用浏览器FFmpeg并调用只读
render-plan；后台renders调用位于源整站剪辑页组件，该页面按当前产品范围已删除。
原画布浏览器下载、新片段归档和仅cookies恢复在两宽度、开发及宿主组合生产预览
专项通过，实际SHA/ffprobe和三方权限已验证。完整取消/异常与成对视觉仍在验证，
不由DDL、计划、后端专项或本次正常运行范围推断全部通过。

## 私人本地字幕转写

`POST /api/v1/canvas-runtime/timeline/transcriptions` 使用源 camelCase 合同
`{resourceId,language?,projectId?,clientOperationId,model_config_id?}`（兼容 `modelConfigId`）。`resourceId` 为非零uint64十进制字符串，
`clientOperationId` 为8–128位字母/数字/冒号/下划线/横线。可附同值 `Idempotency-Key`；
同一作者同键同请求返回原任务200，新任务202，改输入或请求头不一致返回409。客户端
不能提交服务地址、凭据、租约或终态。语言去首尾Go空白，最多128字符。

`projectId` 是原稳定画布键；当前项目来源或本人全局私人媒体可准入。未传时，项目媒体
使用其已授权主画布，全局私人媒体创建无项目的本人任务。跨项目来源须先复制；图片、
外部URL及无权来源拒绝。准入冻结实际定位、SHA/字节长度、MIME及画布版本，来源外键
保护排队与历史输入，不把签名URL保存为身份。当前账号、项目、画布和来源权限在执行
回调及完成事务内重新检查。

任务的 type/operation 为 `timeline_transcription`；默认 provider/model 为 `local`/`whisper.cpp`，
选择云端配置时为该任务冻结的渠道模型。
详情、列表、日志、取消和明确恢复沿现有 `/tasks` 合同，只允许作者及当前权限读取。
queued取消立即终止，running取消停止本地预处理和HTTP观察；failed/cancelled恢复保留
原ID/来源，仅增加消息版本。完成回执 `resultJson` 解包为 `{segments,srt,language}`，
segment字段为 `startMs/endMs/text`，毫秒按固定Go源半值远离0取整；保持返回顺序、
去Go空白、跳过空正文，SRT每条末尾两个换行，负SRT时间钳0。

Worker使用 `CANVAS_WHISPER_BASE_URL` 的 `/inference` 和真实16kHz单声道PCM WAV，
配置缺失、静音/坏媒体、HTTP/解析错误会持久失败，无自动HTTP重试。成功结果仍为私人
回执。画布音视频节点的“编辑字幕”入口观察任务，成功替换当前字幕草稿，由原“保存”动作
写入节点；关闭窗口停止观察，服务端任务继续执行。整站剪辑页保持移除。
014 DDL已在当前 `short_drama` 执行；受控HTTP测试不代表真实ASR模型验收，完整迁移仍
按台账逐项验证。

`GET /canvas-runtime/timeline/transcription-models` 返回
`{models:[{id,displayName}]}`，只列本人启用且声明
`runtime_profile.protocol="openai-audio"`、`api_format="openai"` 和
`capability_config.audio.transcriptionSupported=true` 的宿主手动配置。
传入 `model_config_id` 时冻结模型 ID、配置版本和 modelKey；执行前再次校验配置版本，
密钥只在服务端解密。先预处理 WAV（最大 25 MiB），再向配置基础地址的
`/audio/transcriptions` 发一次 multipart 请求，要求有效 `verbose_json` 时间戳片段。
不自动切换模型或重试付费提交。发 HTTP 前持久标记 `cloud_submission_started`；
断连、进程中断等结果未知的任务禁止 resume 重发，同操作键只重读原任务，须到渠道核实。
模型列表可显式刷新；不选择云端模型时继续使用宿主 Whisper。

## 画布个人风格与完整快照

`P=/canvas-runtime/style-profiles`。接口不依赖全局工作区模式，个人原件按账号隔离；
项目作品保存独立快照，不会因个人原件修改或删除而变化。个人返回值沿源 camelCase，
`id/userId/rowVersion` 为十进制字符串，`revision` 仅为快照内的语义版本。

| 方法与路径 | 请求与结果 |
| --- | --- |
| `GET P` | `{profiles:[...]}`，仅本人未删除风格 |
| `POST P` | `{profileJson,import_key?}`，返回 `{profile}`；同一本机导入键返回已有原件，不覆盖其后续修改；该键已删除时 409 |
| `PATCH P/{id}` | `{profileJson,expected_row_version}`，返回 `{profile}`；CAS 冲突 409，不覆盖旧原件 |
| `PATCH P/{id}/favorite` | `{favorite,expected_row_version}`，返回 `{id,rowVersion,favorite}` |
| `POST P/{id}/use` | `{expected_row_version}`，更新最近使用并返回当前版本 |
| `DELETE P/{id}` | `{expected_row_version}`，软删除本人原件，项目快照保留 |
| `GET P/preferences` | `{favorites:[presetId],recent:[presetId]}`，本人系统风格偏好 |
| `POST P/preferences` | `{preset_id,action:"favorite"|"use",favorite?}`，收藏动作须指定 favorite |

`profileJson` 是经过完整结构验证的快照 JSON 字符串，含 schemaVersion、presetId、title、
description、tags、prompt、negativePrompt、selection、assets、executionPolicy、source、revision
等源字段（可选项按定义省略），最大 256 KiB。凭据及危险字段拒绝；资源 ID 必须可授权，
媒体通过关系表保护。旧本机风格只由用户明确勾选导入，重名不覆盖。前端 409 保留编辑草稿，
可重新打开最新版本或另存新风格；异步写入绑定启动时的账号及登录 epoch。

## 私人深度动作捕捉

`POST /api/v1/canvas-runtime/depth-captures` 使用源 camelCase 合同
`{resourceId,projectId?,clientOperationId}`，标识和操作键约束同本地字幕转写。
可附同值 `Idempotency-Key`；新任务202，同作者同键同输入返回原任务200，改输入、
跨任务类型复用键或请求头不一致返回409。客户端不能指定模型、设备、组件路径、
发布清单或执行回执。来源须为已授权的永久视频资源；超过15100ms拒绝，实际执行前
再次探测时长和冻结字节。当前画布与本人全局私人媒体的范围规则同转写，不接受
跨项目资源或外部URL直接作为输入。

任务复用014的 `canvas_local_jobs`，不新增表；`type/operation=depth_capture`，
`provider=local`，`model=video-depth-anything-small`，固定
`profile=vda-small-mps-standard-v1`。详情、列表、日志、取消和明确恢复沿 `/tasks`
合同，只允许作者及当前权限。关闭前端观察不取消后台；失败/取消后明确恢复保持
原ID与来源，旧消息或失效租约不能完成任务。

独立组件支持Windows x64的CPU/CUDA与Apple Silicon的MPS。仅CUDA设备故障退出码42
允许一次CPU回退；模型缺失、安装、推理或编码错误持久失败。默认CLI参数为输入280、
最长边960、输出1920×1080、最长15秒、2/98百分位和gamma1.25。执行设备、回退原因与
耗时先持久保存；视频按 `depth:<task-id>` 分片归档，完成回执丢失后复用原资源，
不重复推理。完成前重新核验归档SHA-256、大小、分辨率和时长。

成功 `resultJson` 解包为
`{resourceId,fileName,size,durationMs,width,height,device,fallbackReason,runtimeVersion,processingMs}`。
`resourceId` 为十进制字符串；`fileName=depth-action-reference.mp4`，尺寸1920×1080，
`device` 为 `cpu/cuda/mps`，`runtimeVersion=v1`。回执与未放入图的结果仅本人可见；
原画布操作附加节点后，成品才按项目权限共享。组件部署配置见
[开发指南](../instruction/开发运行与验证.md#环境准备)，验收范围和未验部分见剩余工作台账。

## 已退役来源与普通角色

来源项目绑定、小说章节读取、企业角色目录及来源画风同步已取消，相关路由不再注册。新文档不接受退役 `projectId` 或企业角色版本字段；携带这些关系的旧导入包需明确拒绝，不自动恢复绑定。

原生画布角色保留节点 `nodeId/name/definition`、本地图片与音频引用。分镜工具接收当前画布角色定义，返回 `characterNodeId`，只按当前画布已保存节点校验，不读取标准项目的素材或角色版本。生成参考图与样音仍通过普通媒体引用和本人媒体复制准入，不能将声音样本冒充供应商音色。

创建或复制仍可携带成对 `source_canvas_key/source_canvas_row_version`，用于校验来源画布权限与精确版本；剪贴板 `sourceProjectId` 表示来源画布，不能据此建立创作项目绑定。

<a id="canvas-compatible-chat"></a>

### 画布兼容 Chat 协议

固定源以下十八个 provider 的完整声明式合同相同，已接入 `openai_chat.v1`：

`atlascloud-chat`、`baichuan-chat`、`deepseek-chat`、`fireworks-chat`、`groq-chat`、`kimi-chat`、`litellm-proxy-chat`、`localai-chat`、`mistral-chat`、`newapi-chat`、`nvidia-nim-chat`、`openrouter-chat`、`siliconflow-chat`、`together-chat`、`vllm-chat`、`xai-grok-chat`、`yi-chat`、`zhipu-glm-chat`。

本人保存 `service_type=text`、`api_format=openai` 和确切 `protocol`。画布节点、独立模型测试及编辑器工具冻结 `canvas_text_protocol` 与 `canvas_text_defaults`；后改配置不改变已受理任务。路由和鉴权只取冻结服务地址与加密凭据，地址支持 origin、`/v1`、完整 `/chat/completions` 和网关前缀。

允许 `temperature/top_p/max_tokens/tools/tool_choice/response_format/stream/provider/transforms/extra_body`，从 `default_options` 读取，再由本次 `input.metadata.providerOptions.<确切协议>` 覆盖。未知字段、其他协议命名空间、非法类型、模型／地址／凭据覆盖拒绝；显式 `textOptions.stream`、保存的能力及冻结模型／消息优先。编辑器工具保留 canonical 的 tools／choice／response_format，协议参数不能替换它们；thinking 仍经过已有保存能力门禁。

源族使用 `max_tokens` 且不额外加入 `stream_options`。非流正文读 `choices.0.message.content`，为空时兼容 `choices.0.text`；`reasoning_content` 仅进入本人的 reasoning，usage 单独保存。明确 JSON `error.code` 为失败；畸形返回或截断流保持 unknown，不自动重新提交。秘密、工具定义和临时签名 URL 不进入公开 resolved 参数。

上述为执行合同与本机 HTTP／隔离 MySQL／MinIO 验证，未代表供应商接受全部参数、媒体或工具。2026-10-13 官方目录 86 个 provider 中32个有映射、54个仍待实施；全部真实生成验收按用户最新指令暂停。
