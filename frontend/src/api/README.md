# 前端 API 约定

项目内创作对象直接使用 `project_id`，不存在分集身份；项目创建只提交名称和模式，设置仅保存名称。项目和全局素材库、成员及邀请入口已退役。

后端公共契约见[接口说明](../../../docs/api/README.md)。本文件只说明前端请求组织与交互约束。

项目卡片用独立新标签页链接打开创作流程，更多菜单与链接平级，菜单操作不触发导航。删除使用 `DELETE /projects/{id}?row_version=...`，版本仍为十进制字符串；确认后才提交，409 保留确认窗口并要求重新核对，读取新版本后仍需再次明确删除。成功后重新读取当前筛选与分页，末页为空时回到有效页。项目删除后不能继续访问其内容；已受理的生成任务与媒体记录保留，不自动取消任务。

## 分层与类型

- `http.ts`是统一Axios实例，默认`/api/v1`、15秒超时；将网络、HTTP和业务错误转为`ApiError`，不记录正文/凭据，不自动重试写入，不触发全局toast。
- `types/`和`modules/`中的DTO保留服务端snake_case字段；ID与版本保持十进制字符串，不能转换成JavaScript number。
- `modules/`封装资源请求与必要的UI映射；组件不另建Axios实例。
- API 模块不能依赖 `features/`。模型配置的草稿序列化、业务模型映射与变更通知由 `features/ai-config/config-api.ts` 组合，底层模块只接收和返回 DTO；通用组件不直接请求 API。
- 页面/会话负责加载、分页、错误、提交和过时响应处理。可取消的列表请求使用AbortController，取消不作为业务失败显示。

## 保存、冲突与生成

`episode-writing.ts`的`storySettingsApi`提供项目故事设定GET/PUT及四字段DTO；`row_version`独立于正文，未保存版本为字符串0。StorySettingsEditor复用ResourceEditingSession和standard-session-registry，串行保存、确认回执、恢复及冲突核对参与顶部状态与导航保护。中文合成结束才提交，旧版本409保留本机输入；不读取旧大纲、不自动影响生成提示词。

小说、大纲与剧本使用 `writing-session.ts` 串行保存共享 `content_version`，`episode-writing.ts` 提供 outline 保存及 `active_writing_tasks` DTO。非空当前剧本保存即生效，以响应的 `confirmed_script_id` 为准；并发版本用于防覆盖；AI 替换与导入的服务端变更快照继续保存，剧本页已移除历史恢复入口及对应前端请求封装。当前界面采用纯文本，保存清除旧 Delta，历史格式数据仍兼容读取。旧响应不能覆盖新输入；409 暂停并保留输入，网络不确定时先核实服务端。会话按账号及资源保留，页面卸载仅解除订阅；普通保存不阻止应用内切换。

`script-desk/useWritingGeneration.ts` 先 flush，再冻结完整请求及幂等键；受理不明只查询或重放原请求。已受理任务可离开页面，刷新 writing 重建目标锁，任务结束回读三文档；非目标脏输入仅在服务端基线未变时安全续接全局版本，真实冲突保留输入。目标生成／改写期间只读，其他文档仍可编辑。`writing-requests.ts` 将 textarea UTF-16 偏移转换为 Unicode 码点，拒绝半个 surrogate；服务端验证并仅替换选区。生成成功直接回写，编辑器把外部替换记录为独立撤销。来源与错误码见[工作台契约](../../../docs/api/writing-workspace.md)。

正文、剪辑与声音的本机恢复稿按账号和稳定对象隔离，记录服务端基准版本及未知保存回执。同基准自动恢复续存；基准变化或记录损坏保留原记录，用户按需在非模态详情选择版本，替换前持久保留副本，不追加确认。未知回执不能因 GET 恰好返回旧基准便当作保存成功；重新保存最后输入以并发版本拒绝迟到旧写。声音审核状态在版本或剪辑变化后失效。剪辑只保存稳定媒体 ID 与编辑决定，不保存签名 URL；恢复重新读取展示地址，失效来源仍需处理。只有仅在内存的输入才触发文档退出 `beforeunload`，不会阻止应用内导航。

Agent 待核对消息与普通输入草稿分开保存，包含原始完整请求体、附件、Skill 版本、音轨选择及幂等键；刷新后的核对不受当前附件 pending 状态影响。成片导出、预览及任务重试同样保存原请求和原键，只在明确收到受理结果后清除；未知结果禁止换参数换键自动重提。明确拒绝且未建立任务的业务错误允许处理原因后新建请求，幂等冲突保留原记录供核对。

成片同步连接状态独立于草稿保存状态；失败保留编辑和上次进度，可显式重连。有活动任务时每 4 秒同步，空闲每 15 秒，后台页 30 秒，失败有限退避，避免轮询重叠。声音功能区分加载中、明确关闭与读取失败；试听地址刷新只合并媒体展示信息，不替换声音文档、选择、审核状态或版本。

素材与分镜使用各自版本；排序还需要storyboard_version。生成前先保存来源内容，采用时提交服务器返回的版本、当前媒体ID和上下文hash；不要在前端生成这些比较值。

创作模式新增素材先保留本机临时卡片，名称与类型完整后用 `assetLibraries.create` 和原创建键保存；UUID 不发送为业务 ID，未命名占位文字不作为真实名称。请求受理不明时核对原键并保留原卡片，不能另建一张。素材 PATCH 支持 `kind`，同时保留 `row_version`；转换不兼容时保留输入并展示错误，不能代替服务端删除声音、对白、历史或关联。

镜头 `title` 与 `shot_size` 随创建、修改、详情和采用回执读取；title 默认空，界面显示“未命名镜头”，不用脚本第一行冒充名称。shot_size 可空，枚举为 `full_shot/medium_shot/close_up/extreme_close_up/wide_shot/two_shot`。名称、脚本沿同一镜头保存队列，图与视频页共用。隐藏的素材关联、脚本时长和完整 image_settings 保留服务端值，不被局部表单清空。

分镜对象详情通过 `storyboardApi.shot(id)` 独立于 20 条分页恢复，额外详情不计入列表 offset；仅服务端详情的归档状态或明确 404 能证明当前对象不可用。详情先于列表返回时仍接收自动保存的行版本与上下文回执，保存后重新读取集合版本。迟到的相邻分页不能覆盖后来选择的对象；上移、下移前完成保存，执行期间锁住编辑。镜头与素材列表独立加载，素材读取失败不得隐藏镜头或覆盖未保存输入。图片参考数为已确认素材图与手动图按 `media_id` 去重后的合计，最多 16 张；生成前保存读回并重新读取两类引用，不自动丢弃超限引用。

`StoryboardResultPage` 预览包含逐镜 `title/shot_size/script/duration_ms/asset_ids/assets/source_excerpt/story_beat` 和整批 `total_duration_ms`。素材名称取生成时冻结快照，`available` 标识是否仍属于本项目，`snapshot_missing` 表示旧快照缺失；不得用当前名称冒充原始生成上下文。采用仍为完整批次，预览分页不限制采用范围。

`generations/attempt.ts`按操作与请求内容保存幂等标识。请求超时后同一请求复用原键，内容改变或明确新建任务使用新键；不要为了“重试”自动触发第二次付费提交。可执行操作以can_cancel/can_resume/can_retry为准。

`generations.lookup` 使用 `GET /ai/generations/by-idempotency-key`，按类型与原键只读核对本人任务。未知结果保留冻结请求及原键；查询返回 null 不能触发自动新提交。账号、对象、请求或草稿范围改变后，旧核对回执不能更新当前对象的任务。

图片/视频的asset_id、素材本体id、media_id不是同一概念。参考图提交文件media_id；候选采用接口路径使用生成媒体资产asset_id；目标id则由target.type决定。

主页资产库通过 `mediaLibrary.list` 读取本人 AI 图片、视频、音频。`AssetFilters.media_type` 可省略表示全部，`workspace_mode` 可选 `standard/infinite_canvas`，`name` 只匹配资产名称；工作台、媒体与名称可组合，服务端统一计算总数、倒序分页。前端每页 24 项，切换条件清除 offset 和 asset，URL 保留其他条件。手动上传不进入生成资产列表。

`AssetPreview` 打开时只调用 `mediaLibrary.detail` 刷新签名地址，失败可重新读取；不发送重命名或采用写请求，也不跳转任务管理。图片使用画布同款预览，视频与音频关闭时暂停并清除 src；关闭恢复触发焦点。`/tasks/*` 仅兼容跳转项目列表，不请求任务列表，创作场景内原生成接口与恢复合同继续有效。

素材详情的旧来源提交 `source:{scene:"asset_image",asset_id,row_version}`，`input.prompt` 为本次补充要求；创作模式的私人来源扩展见下一节。保存回调必须返回服务端的新版本；短操作锁覆盖保存和提交，不能从尚未更新的 React state 读取版本。历史按素材 ID 查询；切换素材时取消旧请求，轮询错误不覆盖已有任务状态。

候选 `generation` 提供来源任务、版本和 `is_stale/stale_reason`。上传或通用图片候选该字段为 null。采用遇到 `stale_source` 时明确确认后提交 `acknowledge_stale_source`，旧来源确认不能跳过目标版本冲突。

## 图片组与逐图工具

`modules/image-groups.ts` 访问共享组、固定图位、主图及私人操作；完整合同见[图片组 API](../../../docs/api/image-groups.md)。`useImageGroup` 保护对象切换和轮询顺序，主图变化后重新读取业务 DTO，不覆盖未保存草稿。尚无作品时按本人草稿的张数预留图位；已有作品按组的 `expected_count` 展示，不因下一次生成张数变化清空。受理后读取新组，各图按 `output_index` 固定回填。

设主图提交 `expected_item_version/expected_primary_version/expected_target_version`，409 保留输入并等待核对。单图操作先以原版本和幂等键建立私人记录，再执行本地像素处理或准备 AI 输入；AI 操作使用原公共 `generations.generateText/generateImage` 和 `source.scene=image_operation`。受理不明保留完整原请求及两个幂等键，仅查原生成回执，返回 null 也不自动重新提交。

图片读取经宿主鉴权同源 content API，按真实像素创建编辑面板；读取失败保留原图并提供重试。原图历史恢复仍经过版本和范围检查。共享算法位于 `src/lib/image-tools/`，画布以兼容 re-export 复用，独立布局和交互保持原入口。

## 创作模式私人生成草稿

`modules/creation-drafts.ts` 访问 `/projects/{project_id}/drafts`，按账号、项目、稳定对象和 `purpose` 管理。用途为 `novel_adaptation/asset_image/storyboard_image/storyboard_video`，小说对象为项目 ID。首次 POST 创建；已有草稿用 PATCH 和 `row_version`，遇到创建或版本冲突先读取当前草稿，保留当前输入等待明确核对。草稿只保存下一次生成输入，按账号与项目隔离。

剧本工作台不再读取或保存 `novel_adaptation` 私人草稿；旧用途仅保留历史兼容。大纲是服务端当前文档，生成要求保留在本次请求；不建立新的用户草稿或版本管理。

DTO 保留 `model_config_id/input_text/image_settings/video_settings/reference_media_ids` 及服务端 `id/draft_version/source_version/row_version`。未选模型仍可保存输入，生成前必须选择本人可用模型。小说改编和图片补充输入最多 4000 字符，视频提示词最多 16000 字符；私人图片张数为 1/2/4，参考图最多 16 张。视频时长为整秒的毫秒值，实际范围遵守模型能力。仅保存永久媒体 ID，`reference_images` 里的现签 URL 用于展示；作品来源参考图与私人参考图分别管理，生成时合并去重并重新校验合计与模型上限。

先完成作品来源保存，再等待本人草稿保存回执，最后提交来源的最新版本和 `draft_id/draft_version`。仅本机保留的输入不能显示服务端已保存，也不能引用为生成来源。`source_version` 是保存时的溯源版本，不替代当前作品许可；作品来源后来改变时重新读取最新行版本和 hash，私人输入仍保留，不自动拿缓存值拼可信最终提示词。

素材私人来源还带 project_id；分镜图使用 `context_mode:saved` 和当前完整 layout。图像补充提示词与可见参数由服务端读取本人草稿并冻结，隐藏 layout 保留来源值。分镜视频仍携带当前主图及 video_context_hash，禁止同时传 input；私人提示词只替代本次视频提示词段，保留剧本，私人时长不改脚本时长。无私人引用的请求仍遵守共享保存设置。409、未保存、不可用模型或当前主图与提交时引用不匹配时停止提交并保稿；素材图和分镜图生成后直接保存到作品图片组，默认第一张主图；分镜视频由后端成功归档时自动确定，前端重新读取 ShotRead.video，不发起媒体采用请求。素材提取、分镜结构及 Agent 文本候选继续明确采用；剧本工作台的三类写作任务成功即回写当前文档。

私人草稿、提交核对记录和恢复稿都按账号及稳定范围隔离。A → B → A 与图片／视频切换分别恢复各自输入，旧保存和提交响应不能覆盖新对象；切账号清除当前会话中的私人展示。

## 配置与模型目录

配置PATCH/默认切换提交row_version，DELETE用查询参数row_version。成功写入后重新读取，尤其默认切换会影响另一条配置版本。配置冲突需要用户核对，不能自动覆盖。

Key仅在当前表单内使用：编辑留空省略字段、明确清除发送null、新值替换。不得进入Context、localStorage、IndexedDB或前端环境变量。

模型目录请求超时20秒。编辑可传config_id复用同地址的已存密钥；地址变化后需新密钥。旧响应不得覆盖新地址/密钥下的选择，关闭表单取消请求。获取成功不自动保存；truncated提示目录不完整，始终允许手填模型名。原生dialog内下拉层应挂在dialog内。

## 错误与媒体

错误按业务场景就近展示，保留未提交内容；避免把原始上游文本当成安全UI错误。版本冲突可展示当前版本/引用对象，但不能把错误details作为可信业务数据写回。

临时签名URL只用于显示/下载，不作为持久化身份。过期后重新读取媒体详情。上传走FormData字段file，不手工固定multipart boundary；流程内兼容候选上传与确认仍是两次接口调用；创作模式的上传会连续完成并直接更新作品，不展示确认采用步骤。

文本候选、私人媒体历史、生成任务及过程历史仅本人可见。素材图、分镜图的完整图片组及原图历史通过作品图片组 DTO 获取，隐藏他人的 task_id、prompt 与处理记录；自动确定的分镜视频和其他已采用作品通过作品 DTO 获取；成片使用 `assembly.current_work` 与 `/assembly/current/download`，音色使用 `current_voice`。项目、任务、生成历史和作品均仅所有者可读。

Agent 上下文使用 `modules/agents.ts`：附件上传/引用带幂等键，先恢复 `pending=true` 附件再允许发送；无法确认上传或引用结果时沿用原请求核对。发送冻结 `attachment_ids`、Skill 的 `id/content_version` 和 `video_audio`，未知发送结果重试复用完整请求体与原键。消息 `references` 返回附件与 Skill 的展示投影，临时 URL 每次读取重签。自定义 Skill 的 ID、内容版本及行版本为十进制字符串，详见 [Agent 上下文 API](../../../docs/api/agent.md)。


Skill 管理继续使用 `modules/agents.ts` 与 `types/agents.ts`。列表仅按名称／用途搜索，DTO 不包含触发方式；`tags` 隐藏保留，更新时省略表示保留、显式空数组表示清空。导入预览返回 `tags_provided` 和各模式的 `mode_issues`。文件资源按准确版本读取，文本 offset 以 Unicode 字符计数，图片使用 Base64；创建受理不明先通过 `skillRequest(key)` 只读核对原回执。

## 环境

`.env.local`仅放公开设置：VITE_API_BASE_URL默认`/api/v1`并会进入浏览器，API_PROXY_TARGET仅供开发代理。生产同域先代理`/api/`再配置SPA fallback；跨域需要服务端额外配置CORS。资源base与深链部署限制见[运行指南](../../../docs/instruction/开发运行与验证.md#部署与维护)。

## 画布普通角色节点

画布角色直接来自本图节点，保存 `nodeId/name/definition`，生成通过 `characters` 与 `characterNodeId` 引用。详情、改名、普通图片与声音引用沿原生画布模型保存，不再请求来源项目、企业角色目录、角色版本或小说章节接口。`workspaceProjectId` 及剪贴板来源画布身份继续保留；这些身份不能用于恢复已退役的创作项目绑定。

## 画布文字 thinking 与工具范围

普通文字及固定编辑器工具的`input.textOptions.thinking`必须为布尔值，且本人已保存配置的`runtime_profile.capability_config.text.thinking`严格为true。Chat、Responses、Claude沿固定源转换各自参数；Gemini thinking拒绝。能力在准入冻结，配置后改和同键恢复不能改变原任务。Claude普通图片已接永久资源引用，视频拒绝；Gemini普通多模态仍拒绝。

审美阶段prepare／approve／execute沿同一能力门禁；thinking不授予旧执行型助手权限。工具结果和reasoning仍本人可见，不直接改共享图。合同与实际受控测试范围见[画布文字引用及工具请求](../../../docs/api/canvases.md#画布文字图片引用)。真实生成验收按当前用户指令全部暂缓。

## 项目 AI 创作助手

`assistantApi.globalConversations/globalHistory` 必传当前 `projectId` 作为筛选；`createGlobalConversation` 发送 `{project_id}` 并携带幂等键，响应 `project_id` 可空以兼容旧记录。恢复 URL／本机选择前核验项目归属，当前会话选择按账号与项目隔离。`/global` 只是兼容前缀，不表示前端跨项目共用对话。

旧引擎停止后发送新要求时先调用 `sendTask` 保存普通消息，再以 `{kind:'resume',expected_control_seq,message_id}` 调用 `controlTask`，复用刚受理的消息，不额外生成用户气泡；控制意图随待核对发送记录冻结，消息与控制分别保留原请求及幂等键。`message_id` 是十进制字符串，只用于 resume，不能同时传 content，旧停止水位不能覆盖新的停止操作。新任务的写入目标来自当前工作区；补充省略目标，由服务端恢复原任务及待应用输入的冻结目标。项目设定范围由服务端自动限定，前端不发送手动记忆设置。受理不明保留原体原键，不以当前页面重建请求。记忆和删除预览 API 留作内部兼容，前端不展示管理入口；普通删除传 `{row_version,clear_derived_memory:false}`，旧待核对请求仍原样重放。

> **2026-10-28 入口调整：** 剧本页历史恢复入口及下述 `scriptRevisionApi` 前端封装已移除。下段保留原实现约定；服务端变更快照与兼容API继续保留。

`episode-writing.ts` 的 `scriptRevisionApi` 提供摘要分页、前后正文与版本恢复；import 保存显式传 `change_source:import`，未知保存回执仍保留该来源标记，核对成功后清除。恢复前 flush，并核对预览时的当前版本，API 已成功但刷新失败时只重读正文。

`assistant-script.ts` 提供 `preview/apply`，DTO 为 `AssistantScriptProposal`；普通消息可携带 `script_reference`，只在发送前完成正文保存后冻结版本。Harness 的 stopped／paused普通消息直接续跑，运行中补充也调用 `sendTask`，不使用 redirect。明确拒绝用稳定受理错误码判定，不能泛化为清除全部4xx pending。`assistant-business-events.ts` 按操作和撤销指纹通知业务刷新，编辑会话遇新版本保留脏稿。草稿只持久化引用的最小身份、名称、版本和校验值，恢复后通过 `inspectSubjects` 再核对；不保存正文或签名URL。
