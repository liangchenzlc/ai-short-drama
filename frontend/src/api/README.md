# 前端 API 约定

后端公共契约见[接口说明](../../../docs/api/README.md)。本文件只说明前端请求组织与交互约束。

## 分层与类型

- `http.ts`是统一Axios实例，默认`/api/v1`、15秒超时；将网络、HTTP和业务错误转为`ApiError`，不记录正文/凭据，不自动重试写入，不触发全局toast。
- `types/`和`modules/`中的DTO保留服务端snake_case字段；ID与版本保持十进制字符串，不能转换成JavaScript number。
- `modules/`封装资源请求与必要的UI映射；组件不另建Axios实例。
- 页面/会话负责加载、分页、错误、提交和过时响应处理。可取消的列表请求使用AbortController，取消不作为业务失败显示。

## 保存、冲突与生成

小说与剧本使用`writing-session.ts`串行保存共享content_version。旧响应不能覆盖新输入；409暂停保存并保留草稿，网络不确定时先读取服务端核实。导航保护等待保存、确认和其他制作编辑；失败后才允许用户明确决定是否放弃。

正文、剪辑与声音的本机恢复稿按账号和稳定对象隔离，记录服务端基准版本；刷新后先显示服务端内容和恢复提示，不能自动提交。恢复前重新读取最新对象；版本已变化须明确核对，声音审核状态在版本或剪辑变化后失效。损坏记录可下载核对并明确放弃，处理前禁止编辑覆盖该记录。剪辑只保存稳定媒体 ID 与编辑决定，不保存签名 URL；恢复重新读取展示地址，失效来源仍需处理。

Agent 待核对消息与普通输入草稿分开保存，包含原始完整请求体、附件、Skill 版本、音轨选择及幂等键；刷新后的核对不受当前附件 pending 状态影响。成片导出、预览及任务重试同样保存原请求和原键，只在明确收到受理结果后清除；未知结果禁止换参数换键自动重提。明确拒绝且未建立任务的业务错误允许处理原因后新建请求，幂等冲突保留原记录供核对。

成片同步连接状态独立于草稿保存状态；失败保留编辑和上次进度，可显式重连。有活动任务时每 4 秒同步，空闲每 15 秒，后台页 30 秒，失败有限退避，避免轮询重叠。声音功能区分加载中、明确关闭与读取失败；试听地址刷新只合并媒体展示信息，不替换声音文档、选择、审核状态或版本。

素材与分镜使用各自版本；排序还需要storyboard_version。生成前先保存来源内容，采用时提交服务器返回的版本、当前媒体ID和上下文hash；不要在前端生成这些比较值。

分镜对象详情通过 `storyboardApi.shot(id)` 独立于 20 条分页恢复，额外详情不计入列表 offset；仅服务端详情的归档状态或明确 404 能证明当前对象不可用。详情先于列表返回时仍接收自动保存的行版本与上下文回执，保存后重新读取集合版本。迟到的相邻分页不能覆盖后来选择的对象；上移、下移、归档前完成保存，执行期间锁住编辑。镜头与素材列表独立加载，素材读取失败不得隐藏镜头或覆盖未保存输入。图片参考数为已确认素材图与手动图按 `media_id` 去重后的合计，最多 16 张；生成前保存读回并重新读取两类引用，不自动丢弃超限引用。

`StoryboardResultPage` 预览包含逐镜 `title/script/duration_ms/asset_ids/assets/source_excerpt/story_beat` 和整批 `total_duration_ms`。素材名称取生成时冻结快照，`available` 标识是否仍属于本集，`snapshot_missing` 表示旧快照缺失；不得用当前名称冒充原始生成上下文。采用仍为完整批次，预览分页不限制采用范围。

`generations/attempt.ts`按操作与请求内容保存幂等标识。请求超时后同一请求复用原键，内容改变或明确新建任务使用新键；不要为了“重试”自动触发第二次付费提交。可执行操作以can_cancel/can_resume/can_retry为准。

图片/视频的asset_id、素材本体id、media_id不是同一概念。参考图提交文件media_id；候选采用接口路径使用生成媒体资产asset_id；目标id则由target.type决定。

素材详情提交 `source:{scene:"asset_image",asset_id,row_version}`，`input.prompt` 为本次补充要求，参考图片列表为空。保存回调必须返回服务端的新版本；短操作锁覆盖保存和提交，不能从尚未更新的 React state 读取版本。历史按素材 ID 查询；切换素材时取消旧请求，轮询错误不覆盖已有任务状态。

候选 `generation` 提供来源任务、版本和 `is_stale/stale_reason`。上传或通用图片候选该字段为 null。采用遇到 `stale_source` 时明确确认后提交 `acknowledge_stale_source`，共享确认标志与旧来源确认标志需同时保留，但均不能跳过目标版本冲突。

## 配置与模型目录

配置PATCH/默认切换提交row_version，DELETE用查询参数row_version。成功写入后重新读取，尤其默认切换会影响另一条配置版本。配置冲突需要用户核对，不能自动覆盖。

Key仅在当前表单内使用：编辑留空省略字段、明确清除发送null、新值替换。不得进入Context、localStorage、IndexedDB或前端环境变量。

模型目录请求超时20秒。编辑可传config_id复用同地址的已存密钥；地址变化后需新密钥。旧响应不得覆盖新地址/密钥下的选择，关闭表单取消请求。获取成功不自动保存；truncated提示目录不完整，始终允许手填模型名。原生dialog内下拉层应挂在dialog内。

## 错误与媒体

错误按业务场景就近展示，保留未提交内容；避免把原始上游文本当成安全UI错误。版本冲突可展示当前版本/引用对象，但不能把错误details作为可信业务数据写回。

临时签名URL只用于显示/下载，不作为持久化身份。过期后重新读取媒体详情。上传走FormData字段file，不手工固定multipart boundary；候选上传与确认采用是两次独立操作。

全部候选、生成任务及过程历史仅本人可见，项目权限不授予他人候选访问权。已采用作品通过共享业务 DTO 获取；成片使用 `assembly.current_work` 与 `/assembly/current/download`，音色使用 `current_voice`。协作者无需读取作者私有导出任务或音色记录，亦不能依赖它们恢复播放地址。

Agent 上下文使用 `modules/agents.ts`：附件上传/引用带幂等键，先恢复 `pending=true` 附件再允许发送；无法确认上传或引用结果时沿用原请求核对。发送冻结 `attachment_ids`、Skill 的 `id/content_version` 和 `video_audio`，未知发送结果重试复用完整请求体与原键。消息 `references` 返回附件与 Skill 的展示投影，临时 URL 每次读取重签。自定义 Skill 的 ID、内容版本及行版本为十进制字符串，详见 [Agent 上下文 API](../../../docs/api/agent.md)。

分集列表及详情的 `cover_url` 是首个未归档镜头当前采用图片的临时展示地址，无图片时为 null。前端映射为 `coverUrl`，不写回分集设置；图片加载失败后重新读取分集详情，仍不可用时显示默认封面。

## 环境

`.env.local`仅放公开设置：VITE_API_BASE_URL默认`/api/v1`并会进入浏览器，API_PROXY_TARGET仅供开发代理。生产同域先代理`/api/`再配置SPA fallback；跨域需要服务端额外配置CORS。资源base与深链部署限制见[运行指南](../../../docs/development.md#部署与维护)。

## 画布角色只读接口

角色目录及图片解析使用 [画布 API 合同](../../../docs/api/canvases.md#画布角色目录与表示图读取)。读取以可信画布 `source_key` 为入口，来源由服务端列决定；客户端图 `projectId`、query 或项目权限不能替代当前双项目、实际角色及媒体权限检查。

先读取 `/canvas-runtime/canvas-projects/{source_key}/character-catalog` 或 `/{asset_id}`，保留返回的画布键/行版本、来源项目、角色 ID、`current_version_id` 和全部真实表示槽位。随后以同一版本请求 `/{asset_id}/image-resources?expected_version_id=...`；只支持 current，409 `canvas_character_version_changed` 需要重新读取并明确核对，不能静默升级或自动重试历史版本。全部 ID/版本保留十进制字符串，槽位按 `reference_id/role/ordinal/media_id` 保留，同媒体不同槽位不去重。

`types/canvas-character-images.ts` 保留服务端外层 snake_case 与现有资源投影的 `userId/mimeType/createdAt` 等别名。独立画布 `host-character-images.ts` 是纯 GET bridge；调用者须注入宿主统一 HTTP 客户端适配器并返回已解包正文，同时提供可信账号/画布上下文检查回调。bridge 在请求前后重核上下文，并比较画布版本、来源项目、角色、作品版本及全部槽位；当前没有 UI 调用或宿主客户端自动接线。

使用返回的稳定 `resource_key` 和受鉴权 `file_path`，不保存临时签名 URL、Blob URL 或原 locator；MIME 使用真实响应，不硬编码 PNG。GET 不写数据库或对象，不创建版本、映射、任务或副本。resolver 不检查实际字节；下载端点重新鉴权并 stat/读取，缺对象返回404，读取失败须保留当前输入和就近反馈。

`requires_canvas_copy` 只是跨项目复制提示，不能代表副本已存在，也不能授予图保存或生成准入。归一化/复制仍沿现有独立流程；此读取合同不代表角色 UI、业务回填、生成或供应商验收已完成。

### 画布 current 角色 UI

无限画布的既有角色菜单、项目资产 picker 和详情通过宿主 HTTP 读取专用 `character-catalog` list/detail，再按相同当前版本读取 `image-resources`。角色目录映射为最小源角色 card，ID、版本和 row-version 保留十进制字符串；不伪造完整 `ProjectAsset` 或 `ProjectDetail`。无可信关联来源时就近显示错误，空目录不回退本人素材库。

查询与确认重读核账号 epoch、画布 lifetime、source、row-version、角色 current 版本和全部表示槽位。图片 resolver 提供真实稳定 resource 身份；首次图保存沿现有 normalizer 复制封面，串行 journal/CAS 回执与 reload 确认持久化。同版本刷新保留已核的复制封面和本人私人 prompt/voice，旧响应不覆盖新输入。当前 UI 范围不含 pinned、声音绑定/生成、角色生成或生成引用；API 夹具与单元测试不等于真实 DB、MinIO、供应商或一比一验收。
