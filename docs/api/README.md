# HTTP API 约定

当前接口统一使用 `/api/v1`。本文描述已实现契约；完整字段、类型和默认值以运行中的 `/openapi.json`、`/docs` 及 [Pydantic schemas](../../backend/src/short_drama/schemas) 为准。账号验证默认启用；两种模式的项目均仅所有者可访问；模型配置和私人资源仅本人可读。

下文 `E` 表示 `/projects/{project_id}`；`L` 表示流程素材 `/projects/{project_id}/assets`。角色、场景、道具直接属于项目，不再存在分集、项目素材库或全局素材库。`asset_id` 在素材接口表示业务素材，在 `/media-library/items` 表示生成媒体资产；`media_id` 表示文件。

## 公共规则

- 数据库 ID、行版本、正文版本、分镜集合版本及 Agent 审核/成果来源版本输出为十进制字符串，前端不得转为 `Number`。版本输入兼容正整数与十进制字符串；声音未建稿的版本允许 `"0"`。布尔值、浮点数、负数和超出 unsigned BIGINT 的值均拒绝。事件 seq/cursor、scope_version、分页、媒体时长仍为普通数值。时间输出 UTC ISO 8601。
- JSON 输入拒绝未声明字段。省略、`null` 和空字符串按各模型定义处理，不能互换。
- 分页一般为 `{items,total,offset,limit}`，`offset>=0`，HTTP `limit` 为 1–100，默认 20；分镜列表默认 100，并额外返回集合版本。
- 错误格式为 `{error:{code,message,fields?,details?}}`。字段错误为 `{field,message}`；业务 details 仅包含允许的版本或引用信息，不回显任意请求、凭据或供应商异常。
- 404 表示不存在或嵌套归属错误；409 为版本/业务冲突；422 为参数/内容校验；413 为上传超限；503 为依赖不可用。
- `Idempotency-Key` 通常长度 1–128，生成、新任务重试、素材/分镜新建、素材提取采用必填；旧 Agent 与新助手消息必填且长度 1–64，旧 Agent 创建会话可选，新助手明确新建会话必填（`resolve` 原子返回或创建最近对话除外）。同键同请求返回原结果，同键异参 409。创作模式项目创建不提供该保障；无限画布模式项目创建要求该键并原子创建主画布。
- 读取内容不创建空白稿，不自动生成或采用。未保存内容应先成功保存，再提交依赖它的操作。

## 账号与个人资源

写请求需要与 `PUBLIC_ORIGIN` 相同的 Origin。登录后附带 HttpOnly `sd_session` Cookie 和 `X-CSRF-Token`（对应 `sd_csrf` Cookie）。未登录返回 401，角色不允许返回 403，无资源权限返回 404。错误邮箱证明返回 422，限流返回 429。

退出接口允许过期会话清除浏览器 Cookie，仍校验 Origin；有效会话退出仍需 CSRF 验证。成员与邀请接口已删除。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/auth/capabilities` | 是否启用账号验证 |
| POST | `/auth/register` | username/display_name/email/password；201 返回 challenge_id，不返回验证码 |
| POST | `/auth/verification/request`、`/auth/verification/confirm` | 注册邮箱证明，发送请求不泄露邮箱是否存在 |
| POST | `/auth/login`、`/auth/logout` | 账号名/密码登录，退出撤销会话 |
| GET | `/auth/me` | 当前账号信息 |
| POST | `/auth/password/request`、`/auth/password/reset` | 邮箱证明重置密码，并撤销既有会话 |
| GET / PUT | `/users/me/model-preferences` | 按 context_key 保存本人 config_id；null 清除偏好 |
| POST / GET | `/projects/{project_id}/imports[/{import_id}]` | 异步独立复制本人媒体；POST 需要 Idempotency-Key，202 返回可查询收据 |

项目 PATCH 仅接受 `{name,row_version}`。手动项目归档和分镜归档接口已删除。任务与媒体历史保留 `resource_scope=all|personal|project`，筛选按真实归属执行。项目内媒体复制仅支持当前用户有权使用的媒体，不支持素材跨库共享。API 不接受 owner、scope_user_id、initiated_by 或操作人字段。

## 全局 AI 创作助手与旧创作候选

创作模式和画布统一使用[项目 AI 创作助手](assistant.md)：兼容路径 `/assistant/global/conversations` 按本人账号及项目提供 V3 会话、逻辑任务、补充与独立任务队列。前端创建必传 `project_id`，列表及历史按项目过滤；任务自动使用所属项目的写入目标与记忆，画布任务另绑定当前画布，已受理目标冻结。任务不自动读取作品正文。前端已移除任务、上下文和记忆管理入口，后台摘要与记忆继续运行。回复中发送按钮切换为停止，停止后发送新要求通过带 `message_id` 的 resume 复用已受理消息，不插入额外用户气泡；停止水位和原请求核对继续生效。结构、媒体与撤销的具体已开放范围及限制以助手 API 为准；旧 V0/V1/V2 在新界面只读，不将旧授权转换为新任务许可。

Agent 默认开启；运行须完成基础迁移、[全局任务／记忆迁移](../database/migrations/2026-10-21-agent-system/README.md)及[项目会话约束增量](../database/migrations/2026-10-22-project-assistant/README.md)，保持账号认证并启动 scheduler 和独立 Agent Worker。生成可通过 `AGENT_ENABLED=false` 显式关闭，删除清理仍由 scheduler 执行。共享附件接口接受 V2/V3 会话并继续校验本人账号与项目权限，V0/V1 保持旧会话限制。附件、现有个人 Skill、模型输入能力及限制见 [Agent 上下文 API](agent.md)，配置和故障处理见[部署说明](../instruction/Agent模式部署与排障.md)。既有所选配置的真实文本、图片与视频证据见[验收记录](../reviews/Agent模式实施与真实模型验收.md)；本次系统尚未完成真实供应商验收。

新对话以[助手路由](../../backend/src/short_drama/api/v1/assistant.py)、[全局任务契约](../../backend/src/short_drama/schemas/assistant_task.py)及[记忆契约](../../backend/src/short_drama/schemas/agent_memory.py)为准。账号拥有项目内会话；创建、列表及执行另校验本人项目权限，历史未绑定 V3 保留兼容且不猜测归属。下表为旧 `/agent` 兼容接口，其项目／候选规则不自动适用于 V3。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/agent/status` | `enabled/schema_ready` 状态；不调用模型 |
| GET / POST | `/agent/conversations` | 按项目/阶段/对象/任务分页读取本人会话；创建支持可选 Idempotency-Key |
| POST | `/agent/conversations/resolve` | 按稳定 scope 返回最近未归档会话，没有才创建 |
| GET / PATCH | `/agent/conversations/{conversation_id}` | 本人会话详情；携带 row_version 改名或归档，活动运行须先停止 |
| GET | `/agent/models` | 本人已启用文本模型，不以 verified 限制选择或发送 |
| POST | `/agent/models/{model_id}/verify` | 旧客户端兼容接口，当前创作界面不要求验证；显式调用可能计费 |
| GET / POST | `/agent/conversations/{conversation_id}/messages` | 分页消息；发送必带 Idempotency-Key，返回消息、运行与事件 cursor |
| GET | `/agent/conversations/{conversation_id}/runs`、`/agent/runs/{run_id}` | 本人运行历史与当前状态 |
| GET | `/agent/conversations/{conversation_id}/state` | 当前执行/待审核运行、持久化队列及事件 cursor |
| POST | `/agent/runs/{run_id}/stop` | 停止新增决策与提交；已受理原生媒体仍可归档 |
| POST | `/agent/runs/{run_id}/reviews/{tool_call_id}` | 按 review_version/review_hash 明确批准或拒绝当前计划 |
| GET | `/agent/conversations/{conversation_id}/events` | SSE，支持 cursor/Last-Event-ID 增量回放；15 秒心跳与 access-ended |
| POST | `/agent/runs/{run_id}/continue` | 按 artifact_id/artifact_row_version 继续指定运行，独立于采用 |

新消息默认 `mode=auto`，由 Agent 判断答复、读取、单项制作或多步骤计划，前端无讨论/生成选择。单项图片/视频在对象、模型、参数和来源唯一确定时直接执行；多步骤/批量须审核。旧 `discuss` 仍不授权制作，旧 `generate/task` 保留明确授权兼容。决策使用 text 模型，媒体使用本人确定的对应模型。参数与参考图冻结；镜头缺少有效已采用图片时返回 `video_reference_required`。同一会话串行执行，补充消息持久化排队，待审核补充只能只读或修订计划；旧批准失效。scope、工具与队列细节见 [Agent API](agent.md)。未知发送沿用原请求和幂等键核对，供应商受理不明的模型段不自动重发。运行 DTO 不暴露 SDK 私有历史或原始模型回复。

本人候选以 [Agent artifact 路由](../../backend/src/short_drama/api/v1/agent_artifacts.py) 与 [artifact schema](../../backend/src/short_drama/schemas/agent_artifacts.py) 为准，同时校验作者及项目访问权，独立于私有会话 API。九表 schema 就绪时，即使执行开关关闭，作者仍可读取和采用本人已有候选；schema 不可用时返回 `agent_schema_unavailable`。候选与项目均仅对所有者开放，明确采用后才改变业务内容。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `E/agent-artifacts` | 按 kind/status 筛选、分页本人候选 |
| GET | `E/agent-artifacts/{artifact_id}` | 本人候选正文/patch/差异与来源快照 |
| POST | `E/agent-artifacts/{artifact_id}/adopt` | 按候选与当前作品版本明确采用，返回稳定采用回执 |

采用携带十进制字符串 `row_version/content_version` 以及适用的 `storyboard_version/target_row_version`，继续运行同样使用字符串 `artifact_row_version`。提取与分镜候选通过 `native_review` 提交原流程的完整逐项选项或追加/替换审核；媒体省略该对象，由后端从冻结成果推导并校验原生采用契约。来源过期或版本冲突不能被确认选项绕过。作品 DTO 不包含他人的候选、任务或生成记录关联；采用不会自动唤醒任何人的付费运行。

候选详情 `content_origin` 为 `snapshot`（冻结的原稿）、`current_script_legacy`（历史记录无原稿快照，只能读取当前剧本）或 null。新剧本候选保存原始正文快照，后续采用和编辑不会改变该快照；历史 fallback 不能作为原生成稿证据。停止时已生成但尚未展示的候选在取消事务内补消息引用，已受理媒体的延迟结果归档后也在原会话追加候选消息；相同候选不重复展示。运行保持停止终态，不能自动继续或采用。

小说改编、素材提取、分镜、图像、视频、音频、批量生成、任务中心、媒体资产库及成片历史中的私人任务、候选和未发布资产，仍按发起人或作者校验列表总数、详情、下载、恢复/重试与采用。创作模式素材图和分镜图的完整图片组及原图历史仅所有者可读，任务、提示词、模型配置与操作记录仅本人可读，见[图片组合同](image-groups.md)。已采用音色通过 `current_voice` 读取（含 `media_id/url/duration_ms/row_version`），已采用成片通过 `assembly.current_work` 及 `/assembly/current/download` 读取；已发布标记不能绕过所有者权限。

## 项目与模式

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `/projects` | 列表与创建；创建只接受 `name/workspace_mode` |
| GET / PATCH | `E` | 读取项目；带 `row_version` 修改名称 |
| POST | `E/open` | 记录本人最近打开时间 |
| DELETE | `E?row_version=版本` | 所有者删除项目；版本必填，成功 204，旧版本 409 `project_version_conflict` |

`name` 去除首尾空格后为 1–120 字符，允许重名。`workspace_mode` 仅为 `standard/infinite_canvas`，创建后不可修改。界面称 `standard` 为“创作模式”，创建成功直接进入项目的四阶段工作台。项目不接受或返回 `synopsis/aspect/style/episode_count`。普通创作阶段只接受 `standard` 项目，画布入口只接受 `infinite_canvas` 项目。

项目删除使用现有 `archived_at` 作逻辑删除，项目及其内容从本人列表与普通访问入口移除；越权、重复删除及之后打开均返回 404。无限画布项目的所有画布同时推进归档版本，旧画布回收站收据不能恢复该项目。媒体、生成记录和已受理任务保留，任务不会自动取消，但删除后不能新增付费提交。删除冲突须重新读取项目，再由用户确认，不自动沿用新版本重试。

## 小说、剧本与候选

| 方法 | 路径 | 请求 / 返回 |
| --- | --- | --- |
| GET | `E/writing` | `content_version/novel/editing_script/confirmed_script_id`；尚未创建的正文为 null |
| PUT | `E/novel` | `{content,content_version}` → 最新版本与小说 |
| PUT | `E/script` | `{content,editor_document?,content_version,script_id}` → 最新版本与剧本；首次创建 script_id=null |
| PUT | `E/editing-script` | `{script_id,content_version}` → 完整 writing |
| POST | `E/scripts/{script_id}/confirm` | `{content_version}` → 完整 writing |
| GET | `E/scripts` | 候选分页、200 字符预览、编辑/确认标记、生成来源 |
| GET | `E/scripts/{script_id}` | 候选元信息及完整 content |

正文保留空白与换行，最大 1 MiB UTF-8，允许空字符串但不接受 null。空白剧本不能确认。小说、剧本和选择共用 `content_version`，过期返回 409 `writing_version_conflict`；每次受理正文保存均推进版本，包括内容未变化的保存，以阻止旧的在途请求覆盖最后输入。

剧本读取另带 `editor_document: object | null`，小说仍为纯文本。格式文档采用受限 Quill Delta：`{ops:[{insert:string,attributes?:{header:1|2|3,bold:boolean,italic:boolean,blockquote:boolean}}]}`，最多 100000 个文本操作；禁止嵌入和其他属性。文档须以换行结束，转换为业务正文时只移除最后一个 Quill 结尾换行，保留用户空行，并与 `content` 完全一致。正文和格式在同一事务、同一 `content_version` 下保存；仅格式变化也推进版本，保留旧确认及正文相同的素材提取、分镜候选有效性。旧客户端省略或明确传 null 时清除旧格式，提取、生成与导出继续读取纯文本。

后台新增剧本候选不改变编辑指针和正文版本。切换候选不会自动确认；编辑已确认剧本会取消该稿确认；确认另一稿在同一事务取消旧稿确认。小说编辑不改变剧本内容或确认状态。

普通 `E/scripts` 列表排除尚未采用、已拒绝或已归档的 Agent 剧本候选；已明确采用的稿仍可正常读取和选择。普通选择、保存和确认不能替代 Agent 采用回执，命中未采用稿返回 422 `agent_artifact_adoption_required`，旧编辑指针亦按此规则处理。必须在本人候选入口核对并明确采用，不静默补旧回执。

## 本人创作草稿

这些资源只保存本人下一次生成的输入，不修改作品或当前采用媒体。非所有者不能访问项目及草稿；本人身份来自可信 Actor，不接受客户端指定归属或操作人。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `E/drafts` | 本人在本项目的草稿数组 |
| POST | `E/drafts` | 首次创建，201；相同对象与用途已有草稿时返回 409 `draft_version_conflict` |
| GET | `E/drafts/{object_id}/{purpose}` | 有效对象的本人草稿，不存在时返回 null |
| PATCH | 同上 | 必带 `row_version`；冲突 409，保留输入后核对当前版本 |
| DELETE | 同上 | 删除本人的该范围草稿，204；不删除生成历史或作品 |

`purpose` 为 `novel_adaptation/asset_image/storyboard_image/storyboard_video`。小说改编的 `object_id` 必须等于当前项目 ID；素材须已关联本项目，分镜须是本项目活动镜头。POST 请求为：

```json
{
  "object_id": "360000000000000003",
  "purpose": "storyboard_image",
  "model_config_id": "201",
  "input_text": "突出侧光",
  "image_settings": {"aspect": "9:16", "resolution": "2K", "count": 2},
  "reference_media_ids": []
}
```

`model_config_id` 可空，以便保存尚未完成的输入；非空时必须是本人可用且用途匹配的模型。`input_text` 按用途限制：小说改编、素材图和分镜图最多 4000 字符，分镜视频最多 16000 字符，POST 与合并后的版本化 PATCH 均校验。图片用途才接受 `image_settings` 和参考媒体，视频用途才接受 `video_settings`；小说改编不接受这些媒体参数。图片画幅为 `16:9/9:16/1:1/4:3/3:4`，清晰度为 `1K/2K/4K`，张数仅 `1/2/4`；视频清晰度为 `480p/720p/1080p`，时长为 1000–3600000 整数毫秒且必须整秒，仍需通过生成时的具体模型限制。画幅、清晰度或视频时长可空，生成时使用相应模型默认或镜头时长；私人设置不包含隐藏的 `layout`。

PATCH 只更新提供的字段，`model_config_id:null` 清除模型。每次受理保存均推进 `row_version`；保存内容与来源版本未变化时保留 `draft_version`，避免无变化保存使生成参数失效。返回含 `id/user_id/project_id/object_id/purpose`、保存内容、`source_version/row_version/draft_version` 和 UTC 时间；全部 ID、版本为十进制字符串。`reference_media_ids` 最多 16 项，只保存可用的 MinIO 永久媒体 ID，允许本人私人图片或当前项目可访问图片；去重后另返回 `reference_images:[{media_id,url,width,height}]`，URL 每次读取现签，不作为持久化身份。

`source_version` 由服务端在保存时读取，用于追溯当时来源，不是前端缓存的生成许可。生成仍须提交来源的最新 `row_version/content_version/context_hash`。保存成功才能引用 `source.draft_id/draft_version`，二者必须成对；`shot_image` 还须使用 `context_mode=saved`，`asset_image` 须带当前项目 ID。服务端校验本人、对象、用途及版本后冻结草稿；未选模型、模型不一致、来源或草稿版本冲突均阻止生成。

私人图片输入替代本次补充提示词，模型与可见参数在该任务内使用已保存草稿；素材名称、描述、提示词与镜头正文仍由服务端读取，隐藏 `layout` 仍需匹配来源值。私人视频输入仅替代本次视频提示词段，保留镜头正文和当前采用图片，时长不修改脚本时长。任务 `source_snapshot.creation_draft` 保存私人输入、草稿版本及 `source_version/current_source_version`，实际参数在任务 `parameters` 中冻结。来源参考图与私人参考图按永久 ID 合并去重，共同校验 16 张及模型上限；视频仍只使用当前采用分镜图。无私人引用的旧请求沿用原来源语义，素材图、分镜图生成后直接保存到作品图片组，默认第一张主图；标准分镜视频成功归档后自动确定，文本及其他创作结果保留各自审核流程。

旧分集版本按[创作模式维护说明](../instruction/创作模式维护说明.md)离线切换，不叠加历史增量迁移。

## 模型配置

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `/ai-model-configs` | 类型 `text/image/video/audio` 分页；创建返回 201 |
| GET / PATCH / DELETE | `/ai-model-configs/{config_id}` | PATCH 需 body.row_version；DELETE 需 query.row_version，204 |
| PUT | `/ai-model-configs/{config_id}/default` | `{row_version}`，设置同类默认 |
| POST | `/ai-model-configs/discover-models` | 用地址/密钥读取模型目录，不保存、不生成 |
| GET | `/ai-model-configs/{config_id}/capabilities` | 本地协议适配能力；不保证真实供应商或账户可用 |

创建字段为 `service_type/name/provider/model_key/base_url/apikey/enabled`，每条配置一个模型。新建不自动成为默认；类型不可改，删除为软删除。`enabled/is_default/is_deleted` 使用 0/1。API Key 写入后加密，读取仅有 `has_api_key`；编辑时省略保留、null 清除、新值替换。

创作模式与画布统一使用此管理入口。可选 `runtime_profile` 保存协议、能力和默认参数，`secret_key/headers` 保存加密扩展鉴权；读取仅返回 `has_secret_key`、header 名称/存在标记与只读凭据来源。画布模型偏好不再写渠道目录。完整字段、旧选择兼容与任务冻结规则见[统一模型运行配置](model-runtime.md)。

模型探测请求 `{base_url,apikey?,config_id?}`。编辑时省略 apikey 可在地址一致时复用已存密钥；显式 null 表示无密钥，变更带密钥配置的地址需提供新密钥。返回 `{items:[{id}],truncated}`，仍允许手动输入模型名称；目录不推断文本/图片/视频能力。私网网关需配置精确主机白名单。

## 异步生成与任务

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/ai/generations/text` | 通用 messages 或服务端业务来源 |
| POST | `/ai/generations/image` | 通用 prompt/参考图、已保存素材或分镜来源 |
| POST | `/ai/generations/video` | 通用视频或已保存分镜视频来源 |
| GET | `/ai/generations` | 类型、状态、模型、来源、项目、时间筛选与分页 |
| GET | `/ai/generations/by-idempotency-key` | 用 `service_type/idempotency_key` 只读核对本人的受理回执，未找到返回 null |
| GET | `/ai/generations/{generation_id}` | 任务状态、错误、结果、`can_cancel/can_resume/can_retry` |
| GET | `/ai/generations/{generation_id}/records` | 调用记录，不返回密钥 |
| POST | `/ai/generations/{generation_id}/cancel` | 尽力取消 |
| POST | `/ai/generations/{generation_id}/resume` | 按证据恢复原任务 |
| POST | `/ai/generations/{generation_id}/retry` | 允许时创建新任务，可传 `{config_id}` |

生成/重试首次返回 202，幂等重放 200，响应含 `generation_id/status/service_type/source/config` 等，不等待模型完成。无私人草稿引用时，`config_id` 省略或 null 使用对应类型的启用默认配置；引用草稿时使用草稿中本人保存的模型，显式 `config_id` 必须相同。无可用配置则拒绝。

对外状态为 `queued/running/succeeded/failed/cancelled`。调用记录中的未知受理结果不是自动重发依据。取消不保证远端取消或免费；retry 可能再次计费，复用原输入快照；resume 不是再次付费生成的快捷方式。客户端依据 `can_*` 显示操作，不自行猜测。

受理回执查询不提交任务、不更新模型或草稿，也不读取他人同键任务。查询的 null 只表示本次未找到可访问任务；未知请求继续保留原请求和原键，显式重试仍复用它们，不能自动换键付费提交。

任务摘要及详情提供 `display_context:{project,subject,scope}`，新任务冻结可读名称，旧任务按现存来源解析。素材来源须携带 project_id，后端核对素材直接归属。前端输入区只呈现提示词，协议仍保留采用所需参数。

任务详情的 `parameters` 返回冻结的业务参数（例如 `aspect/resolution/count`），用于候选预览与采用；`resolved_parameters` 单独返回最新调用的模型接口参数（例如 OpenAI 的 `size/n`）。两者字段和单位可能不同，不能相互替代。历史任务同样从其已保存的原始请求读取业务参数。

通用输入：

| 类型 | input | parameters |
| --- | --- | --- |
| text | `messages:[{role,content}]`，1–100 条 | `temperature` 0–2、`max_output_tokens` |
| image | `prompt`、`reference_media_ids`（最多16） | `aspect/resolution/count`，count 1–4，默认1 |
| video | `prompt`、`first_frame_media_id/last_frame_media_id` | `aspect/resolution/duration_ms` |

媒体引用必须是可用的持久化图片。可选参数仍需满足所选协议与具体模型限制；不支持的字段被拒绝，不静默丢弃。视频时长接口单位为毫秒，某些适配器只接受整秒。

OpenAI Images 接入的 `gpt-image-*` 模型（包括网关别名）支持参考图请求：无参考图调用 `/images/generations`，有参考图调用 `/images/edits`，通过 multipart 的 `image[]` 上传全部图片。执行时解析媒体 ID、下载并校验 PNG/JPEG/WebP；最多 16 张，单张最多 50 MiB、合计最多 100 MiB。参考图下载不携带模型密钥，与生成请求共用任务调用预算；下载或编辑失败不回退为无参考图生成，也不自动重发 POST。

`gpt-image-2` 系列支持将应用的 `1K/2K` 预设转换为明确像素尺寸，例如 `2K + 16:9` 为 `2560x1440`、`2K + 9:16` 为 `1440x2560`。其他尺寸可通过通用接口传入明确像素值，由上游验证；`4K` 标签尚未映射。能力接口表示项目已实现的协议能力，不代表第三方网关、模型别名或账号已通过真实生成验证。

### 素材图片生成

在角色、场景、道具详情使用已保存素材生成图片。与通用图片请求复用接口和幂等 Header：

```json
{
  "config_id": "201",
  "input": {"prompt": "自然光，突出布料质感", "reference_media_ids": []},
  "parameters": {"count": 1},
  "source": {"scene": "asset_image", "project_id": "1", "asset_id": "101", "row_version": "7"}
}
```

ID/版本为示例值，调用时使用真实读取值。此来源 input.prompt 是用户本次提交的完整提示词（≤4000字符），服务端不额外拼接版式模板，reference_media_ids 可传额外输入参考图，并与素材已保存参考图合并去重（合计最多 16 张）。名称及描述/prompt 的已保存内容由服务端读取，版本冲突返回 `asset_version_conflict`，缺少必要内容返回 `asset_content_required`。参数省略时沿用模型默认；不隐式继承入口项目风格。

任务和媒体库列表都支持 `source_scene=asset_image&source_id=<素材ID>`。本人按项目内素材身份读取历史，新素材 ID 不继承其他素材任务。任务详情提供 source_snapshot 与 effective_prompt；幂等重放先返回既有任务，不因素材后来编辑而重新生成。

素材和分镜图片任务在准入时建立固定图位，回执返回 `image_group_id`。归档直接保存作品图片组，默认第一张主图，不进入候选采用步骤；第一张失败时在终态使用序号最小的成功图并给出提示。自动更新检查来源内容及原主图，迟到结果不覆盖手动选择。详见[图片组与工具 API](image-groups.md)。

流程素材 confirm 兼容入口仍校验 `row_version/expected_media_id` 及来源确认；媒体库 apply 的分镜入口校验当前目标版本、上下文和已采用媒体，不因生成记录较旧额外要求来源确认。创作模式的主图选择改用图片组 primary API，校验目标、图位和主图版本；未通过的并发写入不自动覆盖。

图片组通过外键保留来源及媒体。分镜被提取替换为历史记录后，已受理生成仍可保存结果，但不更新该镜头主图。部分输出失败沿用 `partial_result`，成功图保留原序号；恢复保存修复原任务，不重新调用模型。retry 使用旧快照，使用最新内容需新建任务。旧的非图片组任务仍保留其来源缺失警告兼容。

创作模式使用 OpenAI Images 或 Ark Images 同步协议时，`count=2..4` 与画布一样拆为对应数量的并行单图请求，每次只请求一张，按请求序号回填固定图位，不依赖供应商一次返回多张。执行后的 `resolved_parameters` 记录单次请求参数及总请求数 `request_count`。某个请求失败不丢弃其他成功图片，也不自动重发失败请求；异步图片协议仍沿用供应商任务查询流程。

`partial_result` 的安全错误文案区分生成数量不足和已返回结果保存失败，并说明请求、返回及保存数量。失败的图位保留原序号。旧 Worker 已归档但尚未回填图片组的结果仍可通过 `can_resume` 恢复本地回填；恢复保存不调用模型，如果只剩未生成成功的图位，不再提供恢复保存。

### 小说改编与分镜生成

小说改编示例（ID/版本需替换为真实读取值，Header 带新的 `Idempotency-Key`）：

```json
{
  "source": {
    "scene": "novel_script",
    "project_id": "360000000000000001",
    "content_version": "4"
  },
  "instructions": "保留主要冲突，用对白推进故事",
  "parameters": {"max_output_tokens": 8192}
}
```

业务请求禁止同时提交 `input.messages`；后端读取保存内容构建提示词，instructions 最长4000字符。分镜生成使用 `source.scene=script_shots`，另带 `script_id`；使用当前已保存的非空编辑剧本，内部旧 Agent 调用仍按其原确认合同校验。可传 `storyboard:{average_shot_duration_ms:3000}`，范围1000–10000毫秒。

分镜模型结果每次1–100镜，包含 `title/shot_size/source_excerpt/story_beat/script/duration_ms/asset_ids`；`shot_size` 可缺省或 null，非空须是下文固定枚举。原文依据须能在输入剧本中按序定位，引用素材须在输入快照内；单镜正文≤32 KiB，最多50个不重复素材引用。不合法/截断输出保留原文并失败，不部分写入镜头。

任务详情 `result.business` 包含类型、schema_version 和候选：小说结果含 script_id；分镜含 shots 和 applied；素材提取见下一节。历史分镜缺失的新字段有读取兼容，不降低新模型输出校验。

### 剧本素材提取

同一文本生成接口，source.scene=`script_assets`，带 project_id、script_id、content_version；可传 `extraction:{kinds:["character","scene","prop"]}` 和 instructions。标准工作台新任务使用当前已保存的非空剧本，不要求单独定稿。服务端冻结 `script_source_policy=current_saved_v1`；客户端不能设置或伪造该策略。生成分镜沿用同一来源规则。受理仍校验正文版本、当前编辑指针、项目权限和 Agent 候选采用来源；结果采用按剧本身份及正文哈希判定来源，纯格式变化不误判过期。未带新策略的历史任务和内部旧 Agent 调用保留原确认合同。默认输入≤30000字符、候选≤100、输出上限8192 Token；超限不静默截断。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `E/asset-extraction-results/{generation_id}` | 候选、result_version、content_version、stale、同名匹配与重复提示 |
| PATCH | 同上 | `{result_version,items:[{candidate_id,draft}]}`，保存人工修改 |
| POST | `E/asset-extraction-results/{generation_id}/apply` | 部分采用，需 Idempotency-Key |

候选 ID 为32位十六进制字符串，不是雪花 ID。候选 original 含类别、名称、别名、描述、提示词、`importance`（core/continuity）与叙事作用；draft 是可编辑素材字段。提取协议不再要求或返回 evidence，旧结果中的该字段在读取时忽略。叙事作用帮助判断是否需要这个素材，不把文本里每个名词都当成必须生成的对象。

采用请求：

```json
{
  "result_version": "2",
  "content_version": "4",
  "items": [
    {"candidate_id": "0123456789abcdef0123456789abcdef", "action": "create", "confirm_duplicate": false}
  ]
}
```

复用使用 `action=reuse` 并提供 `asset_id/expected_row_version`。重复新建需明确确认；版本冲突、来源过期和未解决的复用冲突应先重新核对。新建结果直接属于当前项目，不自动覆盖同名素材。

## 流程素材与图片接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `L` | `kind/q` 搜索与分页；新建需 Idempotency-Key，首次201/重放200 |
| GET / PATCH | `/assets/{asset_id}` | 本体详情；修改需 row_version |
| GET / POST | `/assets/{asset_id}/image-candidates` | 候选分页；关联已有图片 `{media_id}`，新建201/已有200 |
| POST | `/assets/{asset_id}/image-candidates/upload` | multipart 字段 file，真实上传，201/去重200 |
| POST | `/assets/{asset_id}/confirm` | 确认采用候选，返回素材详情 |

新建字段：`kind/name/label/description/prompt/tags/scene_time`。kind 为 character/scene/prop；名称必填且≤255、label≤120；tags最多20项，每项≤40字符，去首尾空格并去重，空白标签拒绝；scene_time≤60且仅场景可填。

公开 PATCH 支持 `kind`，沿用 `row_version`。每次受理保存均推进 `row_version`；无变化保存保留确认状态、内容 hash 和角色作品历史，不因无变化保存要求额外确认。转为非场景时必须明确清除已有 `scene_time`；角色已有不可变作品历史、声音绑定或对白引用时返回 422 `asset_kind_incompatible`，不删除这些依赖。新角色创建即产生不可变版本，因此不能通过改类型丢弃历史。允许的转换保留通用分镜关联，推进版本并取消确认；受影响媒体继续按原内容上下文判断过期。无权限与行版本冲突仍分别返回 404 与 409。

素材直接属于当前项目。全局库、项目素材库、跨库引用及共享确认已取消；流程内生成与分镜关联继续可用。

上传支持实际 PNG/JPEG/WebP，最大20 MiB、4000万像素。服务端检查文件内容并计算 SHA-256；不只信任文件扩展名或声明 MIME。上传只创建候选，不自动采用。

确认请求为 `{row_version,media_id,expected_media_id,acknowledge_stale_source?}`，expected_media_id为读取时当前图片或null。所选图片必须是有效候选，素材需非空名称且至少有描述或提示词。这些候选接口保留流程内旧客户端兼容；标准素材图、分镜图的生图与逐图工具使用作品图片组，生成即保存，详见[图片组合同](image-groups.md)。

## 分镜与图片生成

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `E/shots` | 列表默认100；新建需 Idempotency-Key，201/重放200 |
| GET / PATCH | `E/shots/{shot_id}` | 单镜详情/编辑；修改需行版本 |
| DELETE | `E/shots/{shot_id}` | `{row_version,storyboard_version}` → `{deleted_id,storyboard_version}`；软删除并移出活动列表 |
| PUT | `E/shots/order` | `{storyboard_version,shot_ids}`，必须包含全部活动镜头 |
| POST | `E/storyboard-results/{generation_id}/apply` | 将候选追加或替换到本项目 |

列表包含 `project_id/storyboard_version/items/total/offset/limit`。单镜读取/新建/编辑返回 `{shot,storyboard_version}`。shot 含 id、position、title、shot_size、script、duration_ms、source_excerpt、row_version、asset_ids、image_settings、context_hash、image、deleted_at，以及下文的视频字段。

新建需 storyboard_version；编辑需 row_version。可编辑 title（≤255 字符，默认空）、shot_size、script、duration_ms（1000–10000，默认3000）、asset_ids和image_settings。shot_size 可空，非空值固定为 `full_shot/medium_shot/close_up/extreme_close_up/wide_shot/two_shot`；缺失不从脚本猜测。生成候选采用时保存 title 与 shot_size，旧镜头的默认空名称和其他内容保留。名称与景别属于元信息，不加入图片或视频内容 hash，修改仍推进行版本。素材关联为完整集合替换；image_settings若提供必须包含全部字段：`resolution=1K|2K|4K`、`aspect=inherit|16:9|9:16|1:1|4:3|3:4`、`layout=single|four|five|nine`。source_excerpt为生成来源，不通过普通编辑伪造。

每次受理镜头编辑均推进行版本，包括内容未变化的保存；仅有效修改推进集合版本，无变化保存保留内容 hash。活动分镜最多500个。删除同时核验行版本和集合版本，冲突分别返回 409 `shot_version_conflict/storyboard_version_conflict`；删除后提升两个版本并解绑素材，将剩余活动镜头压紧为连续序号，序号变化的镜头递增 `row_version`，集合版本只提升一次。序号不进入图片或视频内容 hash，已有采用媒体保持有效。删除保留历史媒体与已入轨剪辑，成片按既有规则显示来源已归档，需要明确处理同步变化；不会自动移除剪辑或取消已发出的生成任务，也不再允许编辑、生成或采用到已删除镜头。重复删除返回 404。

分镜候选采用请求：

```json
{"mode":"append","content_version":"4","storyboard_version":"1","confirm_replace":false}
```

replace需confirm_replace=true；旧镜头归档，新镜头整批创建。来源剧本仍需当前且确认，内容与生成快照一致。重复同模式采用返回原shot_ids及already_applied=true，不重复写入；换模式冲突。

已保存分镜生图使用 `/ai/generations/image`：

```json
{
  "source": {
    "scene": "shot_image", "shot_id": "360000000000000003",
    "layout": "single", "context_mode": "saved", "row_version": "2",
    "context_hash": "0000000000000000000000000000000000000000000000000000000000000000"
  },
  "input": {"prompt": "突出人物眼神", "reference_media_ids": []},
  "parameters": {"aspect": "16:9", "resolution": "2K", "count": 1}
}
```

示例 hash 需替换为读取的真实 context_hash。无私人草稿引用时，参数需匹配已保存设置，不支持 inherit；空画幅使用模型默认值；引用草稿时按上文“本人创作草稿”冻结本次可见参数。两种请求的隐藏 layout 均需匹配保存值。已确认关联素材图片由服务端自动加入参考图；旧请求额外图片限生成资产或当前已确认素材；草稿中的本人私人参考图单独校验权限，两类合并去重后最多 16 张。saved 模式 prompt 仅为补充要求，最长 4000 字符，允许空。

图片组与逐图工具独立使用[图片组 API](image-groups.md)。创作模式与无限画布继续共用 `AIGenerationService`、生成执行、适配器和归档服务，两套 HTTP 入口各自保留。

## 媒体资产与采用

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/media-library/items` | 图片/视频、名称、来源、项目与时间筛选 |
| GET / PATCH | `/media-library/items/{asset_id}` | 详情、重命名 `{name,row_version}` |
| POST | `/media-library/items/{asset_id}/apply` | 明确采用到素材图片、分镜图片或视频 |

采用公共字段：`target:{type,id}`、`expected_media_id`、`parameters`。target.type为asset_image/shot_image/shot_video。不同目标的版本与参数校验范围不同，不能把分镜图片采用的保证推广到所有类型。

- asset_image：必须带expected_row_version；会添加候选并执行素材确认逻辑，不使用parameters中的生成参数。
- shot_image：必须带expected_row_version和expected_context_hash，并核对expected_media_id；历史生成记录可在核对当前目标后采用，acknowledge_stale_source保留兼容，不能跳过当前并发版本检查。parameters可补layout/aspect/resolution，但不得与已有生成记录中的值冲突。分镜候选页面省略parameters，采用原生成记录中的参数；当前图片设置不同不应被传成历史图片参数。
- shot_video：此采用接口保留兼容，标准分镜页面已改为成功归档后自动确定，不再调用它。兼容请求必须带 expected_row_version 和 expected_context_hash（来自分镜的 video_context_hash），并比较 expected_media_id。历史生成记录可在核对当前目标后采用，acknowledge_stale_source 保留兼容，不能绕过当前版本冲突；分镜视频任务的补充参数不得与生成快照冲突。通用视频仍可补充时长与清晰度，但同样需要目标版本与来源核对。单位统一毫秒；同一已采用且来源未变的视频允许重复采用请求返回当前状态。

生成输出不会因未采用而自动删除；替换旧分镜媒体保留回收记录。HTTP 当前未提供物理媒体删除或自动清理接口。签名URL过期后应重新读取详情。

## 依赖检查

`GET /test` 返回服务可达；`GET /test/db` 检查数据库；`GET /test/minio` 检查配置的两个bucket。它们不执行生成。部署和测试命令见[开发与运行](../instruction/开发运行与验证.md)。


## 生成参考图与分页分镜

- `GET /generation-references/{kind}/{owner_id}`：kind 为 asset 或 shot，返回 `{row_version,items:[{media_id,name,url,width,height}]}`。
- `POST` 同路径：multipart `file` 上传，`If-Match` 为所属素材/分镜当前 row_version。支持 PNG/JPEG/WebP、20 MiB、4000 万像素，最多 16 张；重复内容去重，不创建候选或自动采用。
- `DELETE /generation-references/{kind}/{owner_id}/{media_id}`：同样需要 `If-Match`；仅解除关联，保留历史生成所需文件。
- 参考图变化推进所属行版本，分镜同时推进集合版本及上下文摘要；旧版本返回 409 `reference_version_conflict`。保存图会与关联素材的确认图合并用于生成，不支持参考图的模型明确报错。
- `GET E/storyboard-results/{generation_id}/shots?offset=0&limit=20`：分页返回 `{generation_id,items,total,total_duration_ms,offset,limit,applied}`，只读取本人、本项目成功分镜任务。每项包含 `{position,title,shot_size,script,duration_ms,asset_ids,assets,source_excerpt,story_beat}`；`assets` 为 `{id,kind,name,available,snapshot_missing}`，名称和类别来自生成时冻结的素材快照，`available` 表示当前仍属于本项目，旧快照缺失时明确标记而不冒充当前名称。`total_duration_ms` 为整批计划时长，与分页无关。`applied` 为 `null` 或 `{mode,shot_ids,applied_at,storyboard_version}`，数据库 ID/版本均为十进制字符串。采用仍是整批操作，不限当前加载的页；预览不执行采用或自动生成。
- `POST E/shots/{shot_id}/move`：`{storyboard_version,direction}`，direction 为 -1 或 1。服务端交换相邻活动镜头，返回新集合版本，客户端无需加载整集排序 ID。

旧数据库须先执行 [参考图增量迁移](../database/migrations/2026-09-24-generation-references/README.md)。

## 分镜图生视频

旧库还需执行 [分镜视频迁移](../database/migrations/2026-09-28-shot-video/README.md)。分镜 PATCH 新增 `video_prompt`（最多 16000 字符，空表示使用默认内容）、`video_settings:{resolution}`（480p/720p/1080p）。GET 返回 `video_default_prompt`、`video_system_prompt`、`video_context_hash` 及 `video`（播放 URL、时长、清晰度和 is_stale）。已保存图片与视频的兼容字段 `is_stale` 固定为 false，后续修改参数不会使作品失效；图片组的来源变化通过 `notice` 表达。历史 `first_frame_media_id` 字段保留；全能参考任务不填该字段，参考信息保存在生成记录中。

`POST /ai/generations/video` 支持以下业务来源，保留通用 `input` 请求；业务来源与客户端 `input` 互斥：

```json
{
  "config_id": "123",
  "source": {
    "scene": "shot_video", "shot_id": "456", "row_version": "3",
    "context_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "reference_media_id": "789"
  }
}
```

ID、版本及 hash 必须替换为实际读取值。服务端从保存内容装配提示词、时长与清晰度，并校验参考图为本镜当前主图及分镜版本、上下文 token，允许 single/four/five/nine 排版。无私人引用时可传 parameters，但需完整匹配已保存时长与清晰度；服务端仅使用请求参数或模型默认值。引用 `source.draft_id/draft_version` 后，本次提示词段、画幅、时长和清晰度取本人已保存草稿，仍保留镜头正文及采用图校验，仍禁止同时传 input。ModelHub 发送 `functionMode=omni_reference`，输入为 `reference_media_ids`，不用首尾帧字段。生成历史与媒体库支持 `source_scene=shot_video&source_id=<shot_id>`。

模型能力返回可选 `video_input:{first_frame,reference_images,duration_seconds,resolutions}`。分镜视频需要 reference_images=true，仅支持首帧的模型不能替代全能参考模式。通用视频仍保留原首尾帧输入，另支持 reference_media_ids（最多 9 张），两种方式不可混用。数组为 NULL 表示尚无该模型的完整本地约束，不等于已验证全部参数；整数秒等协议规则仍由服务端校验。

视频设置另支持 `video_settings.duration_ms`：可空整数毫秒，1000–3600000 且必须整秒，实际生成范围由模型约束。缺省或 NULL 沿用分镜脚本时长；设置后独立保存，不修改 `shot.duration_ms` 或图片上下文。生成参数与提示词均使用有效视频时长，当前 ModelHub Mini 可设 4–15 秒。

标准 `source.scene=shot_video` 任务成功归档后，在同一事务内自动写入镜头当前视频并发布为项目作品，成片来源读取同一视频槽；不依赖浏览器打开或前端调用采用接口。自动确定仍验证任务租约、作者当前项目权限、活动镜头、来源版本、上下文、参考图和原生音轨，排除画布及 Agent 管理的任务。并行结果按任务提交时间与 ID 比较，较早任务晚到不覆盖较新结果；仅自动回填自身造成的行版本递增可沿同源回执继续，不绕过普通编辑或手动替换的冲突保护。未通过校验时产物保留私人历史、当前视频不变，任务详情通过 `shot_video_auto_apply_skipped` 的固定中文提示说明原因。未成功生成时保留旧视频；已有成片草稿仍须显式同步来源。此次未批量改写既有未采用的历史结果。

# 成片功能补充

[成片合成与导出 API](episode-assembly.md)：草稿、同步、检测、导出、取消、重试、下载与当前成片。

## 无限画布项目

创建项目增加默认 standard 的 workspace_mode，无限画布使用独立前端与画布资源合同。[无限画布接口](canvases.md)记录当前已接通的持久化、历史、账号保护和模型偏好能力；完整创作功能的迁移进度另见[实施记录](../plans/2026-10/2026-10-05-BeefTV画布迁移实施记录.md)。
