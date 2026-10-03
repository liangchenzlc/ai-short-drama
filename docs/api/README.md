# HTTP API 约定

当前接口统一使用 `/api/v1`。本文描述已实现契约；完整字段、类型和默认值以运行中的 `/openapi.json`、`/docs` 及 [Pydantic schemas](../../backend/src/short_drama/schemas) 为准。账号验证默认启用；项目访问取决于所有者或有效成员关系，个人素材和模型配置只对本人开放。

下文 `E` 表示 `/projects/{project_id}/episodes/{episode_id}`；`L` 表示以下三种素材库路径之一：`/libraries/global/assets`、`/projects/{project_id}/assets`、`E/assets`。`asset_id` 在素材接口表示角色/场景/道具本体，在 `/media-library/items` 表示生成媒体资产，两者不能混用；`media_id` 则是文件元数据 ID。

## 公共规则

- ID 输出为十进制字符串，前端不得转为 `Number`。版本类型以各 schema 为准：原工作流多为字符串，Agent 的行版本、审核版本及成果来源版本为整数。时间输出 UTC ISO 8601。
- JSON 输入拒绝未声明字段。省略、`null` 和空字符串按各模型定义处理，不能互换。
- 分页一般为 `{items,total,offset,limit}`，`offset>=0`，HTTP `limit` 为 1–100，默认 20；分镜列表默认 100，并额外返回集合版本。
- 错误格式为 `{error:{code,message,fields?,details?}}`。字段错误为 `{field,message}`；业务 details 仅包含允许的版本或引用信息，不回显任意请求、凭据或供应商异常。
- 404 表示不存在或嵌套归属错误；409 为版本/业务冲突；422 为参数/内容校验；413 为上传超限；503 为依赖不可用。
- `Idempotency-Key` 通常长度 1–128，生成、新任务重试、素材/分镜新建、素材提取采用必填；Agent 消息必填且长度 1–64，创建 Agent 会话可选且最长 64。同键同请求返回原结果，同键异参 409。项目/分集创建不提供该保障。
- 读取内容不创建空白稿，不自动生成或采用。未保存内容应先成功保存，再提交依赖它的操作。

## 账号与协作

写请求需要与 `PUBLIC_ORIGIN` 相同的 Origin。登录后附带 HttpOnly `sd_session` Cookie 和 `X-CSRF-Token`（对应 `sd_csrf` Cookie）。未登录返回 401，角色不允许返回 403，无资源权限返回 404。错误邮箱证明返回 422，失效邀请返回 410，限流返回 429。

退出接口允许过期会话清除浏览器 Cookie，仍校验 Origin；有效会话退出仍需 CSRF 验证。邀请匿名预览只返回状态和 `requires_login`，受邀账号登录后才返回项目名称。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/auth/capabilities` | 是否启用账号验证 |
| POST | `/auth/register` | username/display_name/email/password；201 返回 challenge_id，不返回验证码 |
| POST | `/auth/verification/request`、`/auth/verification/confirm` | 注册邮箱证明，发送请求不泄露邮箱是否存在 |
| POST | `/auth/login`、`/auth/logout` | 账号名/密码登录，退出撤销会话 |
| GET | `/auth/me` | 当前账号信息 |
| POST | `/auth/password/request`、`/auth/password/reset` | 邮箱证明重置密码，并撤销既有会话 |
| GET | `/users/search?q=...` | 已验证账号搜索，不返回其他人的邮箱 |
| GET / DELETE | `/projects/{project_id}/members`、`/projects/{project_id}/members/{user_id}` | 查看成员、主人移除成员 |
| POST | `/projects/{project_id}/leave` | 协作者退出，产出保留 |
| GET / POST / DELETE | `/projects/{project_id}/invitations[/{invitation_id}]` | 主人管理绑定账号或邮箱的七天邀请；创建时才返回完整链接 |
| GET / POST | `/invitations/{token}`、`/invitations/{token}/verification`、`/invitations/{token}/accept` | 预览、发送本次邀请码邮箱证明、提交 challenge_id/code 接受 |
| GET / PUT | `/users/me/model-preferences` | 按 context_key 保存本人 config_id；null 清除偏好 |
| GET | `/projects/{project_id}/activity` | 最近 100 条无正文/凭据的操作记录 |
| POST / GET | `/projects/{project_id}/imports[/{import_id}]` | 异步独立复制 asset/media；POST 需要 Idempotency-Key，202 返回可查询收据 |

项目和分集设置 PATCH 带 `row_version`，冲突返回 409。账号模式的项目 DELETE 是归档，保留历史数据。`/libraries/global` 为个人库；跨范围素材或文件先导入，再引用。任务与媒体历史支持 `resource_scope=all|personal|project`，项目筛选按真实项目归属执行。

新提交、重试、恢复与取消遵守 [协作权限矩阵](../plans/2026-10-02-project-collaboration.md)。API 不接受客户端提供的 owner、scope_user_id、initiated_by 或操作人字段。

## Agent 创作与共享候选

Agent 默认开启；运行须完成七表迁移、保持账号认证并启动独立 Agent Worker，执行可通过 `AGENT_ENABLED=false` 显式关闭。实现与验收状态见[实施契约](../plans/2026-10-02-agent-mode-implementation.md)，配置和故障处理见[部署说明](../agent-deployment.md)。所选配置的真实文本、图片与视频验收已完成，详见[验收记录](../agent-verification.md)；以下接口与本地协议测试仍不能证明其他供应商配置的远端兼容性。

私有入口以 [Agent 路由](../../backend/src/short_drama/api/v1/agent.py)、[会话 schema](../../backend/src/short_drama/schemas/agent.py) 和[运行 schema](../../backend/src/short_drama/schemas/agent_runtime.py) 为准。会话、消息、运行、审核与事件仅对拥有者且仍有当前项目访问权的账号开放，项目成员身份不能读取别人的对话或模型配置。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/agent/status` | `enabled/schema_ready` 状态；不调用模型 |
| GET / POST | `/agent/conversations` | 按项目/分集分页读取本人会话；创建支持可选 Idempotency-Key |
| GET / PATCH | `/agent/conversations/{conversation_id}` | 本人会话详情；携带 row_version 改名或归档，活动运行须先停止 |
| GET | `/agent/models` | 本人协作文本模型与版本绑定的能力状态 |
| POST | `/agent/models/{model_id}/verify` | 显式验证当前 row_version，最多两次可能计费的文本请求 |
| GET / POST | `/agent/conversations/{conversation_id}/messages` | 分页消息；发送必带 Idempotency-Key，返回消息、运行与事件 cursor |
| GET | `/agent/conversations/{conversation_id}/runs`、`/agent/runs/{run_id}` | 本人运行历史与当前状态 |
| POST | `/agent/runs/{run_id}/stop` | 停止新增决策与提交；已受理原生媒体仍可归档 |
| POST | `/agent/runs/{run_id}/reviews/{tool_call_id}` | 按 review_version/review_hash 明确批准或拒绝当前计划 |
| GET | `/agent/conversations/{conversation_id}/events` | SSE，支持 cursor/Last-Event-ID 增量回放；15 秒心跳与 access-ended |
| POST | `/agent/runs/{run_id}/continue` | 按 artifact_id/artifact_row_version 继续指定运行，独立于采用 |

消息 `mode=discuss` 不授权创作任务；`mode=generate` 可携带明确单项 `task`，没有单项时先进入计划审核。决策模型使用 text 配置，与任务里的图片/视频执行模型分开。媒体计划冻结参数与参考图；参考图改变需要新计划审核或新的明确单项授权。镜头没有已采用且不过期的图片时，不能冻结视频任务（`video_reference_required`），应先生成并采用图片，再提出视频任务。同一对话仅有一个活动运行，不自动排队付费消息。未知发送结果应以同一请求和幂等键明确核对，不能自动创建新请求；供应商受理未知的模型段不会自动重发。运行 DTO 不暴露 SDK 私有历史或原始模型回复。

共享成果以 [Agent artifact 路由](../../backend/src/short_drama/api/v1/agent_artifacts.py) 与 [artifact schema](../../backend/src/short_drama/schemas/agent_artifacts.py) 为准，受项目访问权约束，独立于私有会话 API。七表 schema 就绪时，即使执行开关关闭，成员仍可读取和采用已有成果；schema 不可用时返回 `agent_schema_unavailable`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `E/agent-artifacts` | 按 kind/status 筛选、分页共享候选 |
| GET | `E/agent-artifacts/{artifact_id}` | 共享正文/patch/差异与来源快照 |
| POST | `E/agent-artifacts/{artifact_id}/adopt` | 按候选与当前作品版本明确采用，返回稳定采用回执 |

采用携带整数 `row_version/content_version` 以及适用的 `storyboard_version/target_row_version`；共享媒体影响需要明确确认。提取与分镜候选通过 `native_review` 提交原流程的完整逐项选项或追加/替换审核；媒体省略该对象，由后端从冻结成果推导并校验原生采用契约。来源过期或版本冲突不能被确认选项绕过。共享 DTO 不包含私有会话/Run/Tool 来源、工具参数、模型密钥或原始供应商响应；共享采用不会自动唤醒任何人的付费运行。

## 项目与分集

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `/projects` | 名称搜索 `q`、分页；新建 `name/aspect` 必填，`synopsis/style` 可选，201 |
| GET / PATCH / DELETE | `/projects/{project_id}` | 详情、修改、删除（204） |
| POST | `/projects/{project_id}/open` | 只更新最近打开时间，不改变内容修改时间 |
| GET / POST | `/projects/{project_id}/episodes` | 按排序分页；创建 `title` 必填，201 |
| GET / PATCH / DELETE | `E` | 分集详情含 `episode_number`；修改、删除（204） |

项目按最近打开时间及 ID 倒序；分集 position 由服务端在项目锁内分配。分集省略画幅/风格时继承项目，显式空风格表示清空；创建后独立修改。客户端不能指定归属、排序、审计字段或已移除的 `target_ms`。

分集列表与详情新增只读 `cover_url: string | null`：取 position 最小的未归档镜头当前采用图片的签名 URL，无镜头、未采用图片或媒体不可用时返回 null。列表批量读取封面，不逐集加载分镜。该值仅用于展示，过期后重新读取，不持久化至分集表。

存在关联内容时删除返回 409，不隐式级联删除；归档分镜仍保留数据库引用。创建不自动添加示例内容，也不触发 AI。

## 小说、剧本与候选

| 方法 | 路径 | 请求 / 返回 |
| --- | --- | --- |
| GET | `E/writing` | `content_version/novel/editing_script/confirmed_script_id`；尚未创建的正文为 null |
| PUT | `E/novel` | `{content,content_version}` → 最新版本与小说 |
| PUT | `E/script` | `{content,content_version,script_id}` → 最新版本与剧本；首次创建 script_id=null |
| PUT | `E/editing-script` | `{script_id,content_version}` → 完整 writing |
| POST | `E/scripts/{script_id}/confirm` | `{content_version}` → 完整 writing |
| GET | `E/scripts` | 候选分页、200 字符预览、编辑/确认标记、生成来源 |
| GET | `E/scripts/{script_id}` | 候选元信息及完整 content |

正文保留空白与换行，最大 1 MiB UTF-8，允许空字符串但不接受 null。空白剧本不能确认。小说、剧本和选择共用 `content_version`，过期返回 409 `writing_version_conflict`；无变化保存不推进版本。

后台新增剧本候选不改变编辑指针和正文版本。切换候选不会自动确认；编辑已确认剧本会取消该稿确认；确认另一稿在同一事务取消旧稿确认。小说编辑不改变剧本内容或确认状态。

## 模型配置

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `/ai-model-configs` | 类型 `text/image/video` 分页；创建返回 201 |
| GET / PATCH / DELETE | `/ai-model-configs/{config_id}` | PATCH 需 body.row_version；DELETE 需 query.row_version，204 |
| PUT | `/ai-model-configs/{config_id}/default` | `{row_version}`，设置同类默认 |
| POST | `/ai-model-configs/discover-models` | 用地址/密钥读取模型目录，不保存、不生成 |
| GET | `/ai-model-configs/{config_id}/capabilities` | 本地协议适配能力；不保证真实供应商或账户可用 |

创建字段为 `service_type/name/provider/model_key/base_url/apikey/enabled`，每条配置一个模型。新建不自动成为默认；类型不可改，删除为软删除。`enabled/is_default/is_deleted` 使用 0/1。API Key 写入后加密，读取仅有 `has_api_key`；编辑时省略保留、null 清除、新值替换。

模型探测请求 `{base_url,apikey?,config_id?}`。编辑时省略 apikey 可在地址一致时复用已存密钥；显式 null 表示无密钥，变更带密钥配置的地址需提供新密钥。返回 `{items:[{id}],truncated}`，仍允许手动输入模型名称；目录不推断文本/图片/视频能力。私网网关需配置精确主机白名单。

## 异步生成与任务

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/ai/generations/text` | 通用 messages 或服务端业务来源 |
| POST | `/ai/generations/image` | 通用 prompt/参考图、已保存素材或分镜来源 |
| POST | `/ai/generations/video` | 通用视频；尚无分镜业务 source |
| GET | `/ai/generations` | 类型、状态、模型、来源、项目/分集、时间筛选与分页 |
| GET | `/ai/generations/{generation_id}` | 任务状态、错误、结果、`can_cancel/can_resume/can_retry` |
| GET | `/ai/generations/{generation_id}/records` | 调用记录，不返回密钥 |
| POST | `/ai/generations/{generation_id}/cancel` | 尽力取消 |
| POST | `/ai/generations/{generation_id}/resume` | 按证据恢复原任务 |
| POST | `/ai/generations/{generation_id}/retry` | 允许时创建新任务，可传 `{config_id}` |

生成/重试首次返回 202，幂等重放 200，响应含 `generation_id/status/service_type/source/config` 等，不等待模型完成。`config_id` 省略或 null 使用对应类型的启用默认配置；无可用配置则拒绝。

对外状态为 `queued/running/succeeded/failed/cancelled`。调用记录中的未知受理结果不是自动重发依据。取消不保证远端取消或免费；retry 可能再次计费，复用原输入快照；resume 不是再次付费生成的快捷方式。客户端依据 `can_*` 显示操作，不自行猜测。

任务摘要及详情提供 `display_context:{project,episode,subject,scope}`，新任务冻结可读名称，旧任务按现存来源解析。素材来源可携带 project_id/episode_id，后端核对素材库归属。前端输入区只呈现提示词，协议仍保留采用所需参数。

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
  "source": {"scene": "asset_image", "asset_id": "101", "row_version": "7"}
}
```

ID/版本为示例值，调用时使用真实读取值。此来源 input.prompt 是可为空的补充要求（≤4000字符），reference_media_ids 可传额外输入参考图，并与素材已保存参考图合并去重（合计最多 16 张）。名称及描述/prompt 的已保存内容由服务端读取，版本冲突返回 `asset_version_conflict`，缺少必要内容返回 `asset_content_required`。参数省略时沿用模型默认；不隐式继承入口项目风格。

任务和媒体库列表都支持 `source_scene=asset_image&source_id=<素材ID>`。同一共享素材跨库使用相同历史，复制素材的新 ID 不继承原任务。任务详情提供 source_snapshot 与 effective_prompt；幂等重放先返回既有任务，不因素材后来编辑而重新生成。

图片归档后自动进入 `/assets/{asset_id}/image-candidates`，不会自动确认素材。候选可选 `generation` 对象包含 generation_id、record_id、source_asset_id、source_row_version、source_content_hash、is_stale、stale_reason。旧来源原因包括 content_changed、source_mismatch、snapshot_missing；上传、通用生成或无法关联生成记录的旧候选返回 null。

素材 confirm 新增可选 `acknowledge_stale_source`，默认 false；旧来源返回409 `stale_source`，需明确确认后重新提交。媒体库 apply 到 asset_image 执行相同规则，仍需 row_version、expected_media_id 及必要的 confirm_shared。仅采用图片引起的版本增长不会使同批其他候选过期。

原素材在归档前消失时，图片仍保存到媒体库，任务详情 `result.warnings` 返回 `{code:"asset_source_missing",message:"原素材已不存在，图片已保存至媒体库",output_index:1}`。部分输出失败沿用 partial_result，已保存候选仍可采用。恢复保存不重新调用模型；retry 继续使用旧快照，使用最新素材应新建任务。

### 小说改编与分镜生成

小说改编示例（ID/版本需替换为真实读取值，Header 带新的 `Idempotency-Key`）：

```json
{
  "source": {
    "scene": "novel_script",
    "project_id": "360000000000000001",
    "episode_id": "360000000000000002",
    "content_version": "4"
  },
  "instructions": "保留主要冲突，用对白推进故事",
  "parameters": {"max_output_tokens": 8192}
}
```

业务请求禁止同时提交 `input.messages`；后端读取保存内容构建提示词，instructions 最长4000字符。分镜生成使用 `source.scene=script_shots`，另带 `script_id`；必须是当前编辑且已确认的非空剧本。可传 `storyboard:{average_shot_duration_ms:3000}`，范围1000–10000毫秒。

分镜模型结果每次1–100镜，包含 `title/source_excerpt/story_beat/script/duration_ms/asset_ids`。原文依据须能在输入剧本中按序定位，引用素材须在输入快照内；单镜正文≤32 KiB，最多50个不重复素材引用。不合法/截断输出保留原文并失败，不部分写入镜头。

任务详情 `result.business` 包含类型、schema_version 和候选：小说结果含 script_id；分镜含 shots 和 applied；素材提取见下一节。历史分镜缺失的新字段有读取兼容，不降低新模型输出校验。

### 剧本素材提取

同一文本生成接口，source.scene=`script_assets`，带 project_id、episode_id、script_id、content_version；可传 `extraction:{kinds:["character","scene","prop"]}` 和 instructions。必须使用当前已确认剧本。默认输入≤30000字符、候选≤100、输出上限8192 Token；超限不静默截断。

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

复用使用 `action=reuse` 并提供 `asset_id/expected_row_version`。重复新建需明确确认；版本冲突、来源过期和未解决的复用冲突应先重新核对。新建结果只进入本集素材库，不自动覆盖同名素材或共享到项目。

## 素材库与图片候选

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `L` | `kind/q` 搜索与分页；新建需 Idempotency-Key，首次201/重放200 |
| PUT / DELETE | `L/{asset_id}` | 添加已有本体引用；移除需 `If-Match: "行版本"`，204 |
| GET / PATCH | `/assets/{asset_id}` | 本体详情；修改需 row_version，可用 confirm_shared 明确共享修改 |
| GET / POST | `/assets/{asset_id}/image-candidates` | 候选分页；关联已有图片 `{media_id}`，新建201/已有200 |
| POST | `/assets/{asset_id}/image-candidates/upload` | multipart 字段 file，真实上传，201/去重200 |
| POST | `/assets/{asset_id}/confirm` | 确认采用候选，返回素材详情 |

新建字段：`kind/name/label/description/prompt/tags/scene_time`。kind为character/scene/prop且不可改；名称≤255、label≤120；tags最多20项，每项≤40字符，去首尾空格并去重，空白标签拒绝；scene_time≤60且仅场景可填。

允许从全局库添加到项目、从当前项目添加到分集、将分集素材共享回所属项目。移除库引用不删除素材本体；活动分镜仍引用的分集素材不能移除。共享素材的内容修改需明确确认影响多个引用。

上传支持实际 PNG/JPEG/WebP，最大20 MiB、4000万像素。服务端检查文件内容并计算 SHA-256；不只信任文件扩展名或声明 MIME。上传只创建候选，不自动采用。

确认请求为 `{row_version,media_id,expected_media_id,confirm_shared,acknowledge_stale_source?}`，expected_media_id为读取时当前图片或null。所选图片必须是有效候选，素材需非空名称且至少有描述或提示词。素材详情生图自动保存候选，采用仍需单独确认；旧来源校验见上文“素材图片生成”。

## 分镜与图片生成

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET / POST | `E/shots` | 列表默认100；新建需 Idempotency-Key，201/重放200 |
| GET / PATCH / DELETE | `E/shots/{shot_id}` | 单镜详情/编辑；DELETE为归档，需强 If-Match 行版本 |
| PUT | `E/shots/order` | `{storyboard_version,shot_ids}`，必须包含全部活动镜头 |
| POST | `E/storyboard-results/{generation_id}/apply` | 将候选追加或替换到本集 |

列表包含 `episode_id/storyboard_version/items/total/offset/limit`，可用include_archived查看归档。单镜读取/新建/编辑返回 `{shot,storyboard_version}`。shot含id、position、script、duration_ms、source_excerpt、row_version、asset_ids、image_settings、context_hash、image、deleted_at。

新建需 storyboard_version；编辑需 row_version。可编辑 script、duration_ms（1000–10000，默认3000）、asset_ids和image_settings。素材关联为完整集合替换；image_settings若提供必须包含全部字段：`resolution=1K|2K|4K`、`aspect=inherit|16:9|9:16|1:1|4:3|3:4`、`layout=single|four|five|nine`。source_excerpt为生成来源，不通过普通编辑伪造。

有效修改推进行和集合版本；无变化不推进。活动分镜最多500个。归档保留历史媒体与关系，不取消已发出的生成任务，但不再允许采用到该镜头。

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

示例hash需替换为读取的真实context_hash，参数需匹配已保存设置，inherit在客户端解析为本集画幅。已确认关联素材图片由服务端自动加入参考图；额外图片限生成资产或当前已确认素材；去重后最多16张。saved模式prompt仅为补充要求，最长4000字符，允许空。

## 媒体资产与采用

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/media-library/items` | 图片/视频、名称、来源、项目/分集与时间筛选 |
| GET / PATCH | `/media-library/items/{asset_id}` | 详情、重命名 `{name,row_version}` |
| POST | `/media-library/items/{asset_id}/apply` | 明确采用到素材图片、分镜图片或视频 |

采用公共字段：`target:{type,id}`、`expected_media_id`、`parameters`。target.type为asset_image/shot_image/shot_video。不同目标的版本与参数校验范围不同，不能把分镜图片采用的保证推广到所有类型。

- asset_image：必须带expected_row_version，可用confirm_shared；会添加候选并执行素材确认逻辑，不使用parameters中的生成参数。
- shot_image：必须带expected_row_version和expected_context_hash；来源不同/过期需acknowledge_stale_source=true，但不能跳过当前并发版本检查。parameters可补layout/aspect/resolution，但不得与已有生成记录中的值冲突。分镜候选页面省略parameters，采用原生成记录中的参数；当前图片设置不同不应被传成历史图片参数。
- shot_video：必须带 expected_row_version 和 expected_context_hash（来自分镜的 video_context_hash），并比较 expected_media_id。不同或过期来源需明确 acknowledge_stale_source，不能绕过当前版本冲突；分镜视频任务的补充参数不得与生成快照冲突，页面直接沿用原参数。通用视频仍可补充时长与清晰度，但同样需要目标版本与来源核对。单位统一毫秒；同一已采用且来源未变的视频允许重复采用请求返回当前状态。

生成输出不会因未采用而自动删除；替换旧分镜媒体保留回收记录。HTTP 当前未提供物理媒体删除或自动清理接口。签名URL过期后应重新读取详情。

## 依赖检查

`GET /test` 返回服务可达；`GET /test/db` 检查数据库；`GET /test/minio` 检查配置的两个bucket。它们不执行生成。部署和测试命令见[开发与运行](../development.md)。


## 生成参考图与分页分镜

- `GET /generation-references/{kind}/{owner_id}`：kind 为 asset 或 shot，返回 `{row_version,items:[{media_id,name,url,width,height}]}`。
- `POST` 同路径：multipart `file` 上传，`If-Match` 为所属素材/分镜当前 row_version。支持 PNG/JPEG/WebP、20 MiB、4000 万像素，最多 16 张；重复内容去重，不创建候选或自动采用。
- `DELETE /generation-references/{kind}/{owner_id}/{media_id}`：同样需要 `If-Match`；仅解除关联，保留历史生成所需文件。
- 参考图变化推进所属行版本，分镜同时推进集合版本及上下文摘要；旧版本返回 409 `reference_version_conflict`。保存图会与关联素材的确认图合并用于生成，不支持参考图的模型明确报错。
- `GET E/storyboard-results/{generation_id}/shots?offset=0&limit=20`：分页返回 `{generation_id,items:[{position,script}],total,offset,limit,applied}`，只读取本集成功分镜任务。采用仍是整批操作，不限当前加载的页。
- `POST E/shots/{shot_id}/move`：`{storyboard_version,direction}`，direction 为 -1 或 1。服务端交换相邻活动镜头，返回新集合版本，客户端无需加载整集排序 ID。

旧数据库须先执行 [参考图增量迁移](../数据库模型/migrations/2026-09-24-generation-references/README.md)。

## 分镜图生视频

旧库还需执行 [分镜视频迁移](../数据库模型/migrations/2026-09-28-shot-video/README.md)。分镜 PATCH 新增 `video_prompt`（最多 16000 字符，空表示使用默认内容）、`video_settings:{resolution}`（480p/720p/1080p）。GET 返回 `video_default_prompt`、`video_system_prompt`、`video_context_hash` 及 `video`（播放 URL、时长、清晰度和 is_stale）。历史 `first_frame_media_id` 字段保留；全能参考任务不填该字段，参考信息保存在生成记录中。

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

ID、版本及 hash 必须替换为实际读取值。服务端从保存内容装配提示词、时长与清晰度，并校验参考图为本镜当前未过期图片，允许 single/four/five/nine 排版。可传 parameters，但需完整匹配已保存时长与清晰度；服务端从分集设置补充 aspect，避免宫格整图比例决定视频比例。ModelHub 发送 `functionMode=omni_reference`，输入为 `reference_media_ids`，不用首尾帧字段。生成历史与媒体库支持 `source_scene=shot_video&source_id=<shot_id>`。

模型能力返回可选 `video_input:{first_frame,reference_images,duration_seconds,resolutions}`。分镜视频需要 reference_images=true，仅支持首帧的模型不能替代全能参考模式。通用视频仍保留原首尾帧输入，另支持 reference_media_ids（最多 9 张），两种方式不可混用。数组为 NULL 表示尚无该模型的完整本地约束，不等于已验证全部参数；整数秒等协议规则仍由服务端校验。

视频设置另支持 `video_settings.duration_ms`：可空整数毫秒，1000–3600000 且必须整秒，实际生成范围由模型约束。缺省或 NULL 沿用分镜脚本时长；设置后独立保存，不修改 `shot.duration_ms` 或图片上下文。生成参数与提示词均使用有效视频时长，当前 ModelHub Mini 可设 4–15 秒。
# 成片功能补充

[成片合成与导出 API](episode-assembly.md)：草稿、同步、检测、导出、取消、重试、下载与当前成片。
