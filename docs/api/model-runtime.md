# 宿主统一模型配置与画布兼容

管理入口为 `/ai_config`，标准模式与无限画布使用同一套本人 `AIModelConfig`。创建、更新、删除、默认设置继续使用 `/api/v1/ai-model-configs`，编辑和删除沿用 `row_version`，冲突保留草稿。

## 新增字段

创建和更新可提交 `runtime_profile`：

| 字段 | 合同 |
| --- | --- |
| `version` | 整数 1 |
| `api_format` | `openai/gemini/claude` |
| `protocol` | 明确调用协议；未接通协议可保存但生成准入拒绝，不静默回退 |
| `reference_asset_origin` | 可选合法 HTTP(S) 参考资源根，不含用户名/密码、查询或片段 |
| `capability_config` | 可选能力对象，沿源能力结构保存；客户端声明经过服务端校验 |
| `default_options` | 可选默认参数和规格变体 |
| `logical_capability_spec` | 可选逻辑规格 |
| `logical_capability_profiles` | 可选逻辑规格集合 |
| `video_capabilities_version` | 可选视频能力版本 |
| `runninghub_workflow` | 内置 RunningHub 的公开描述：`kind=workflow|app`、`workflow_id/webapp_id`、`title`、`workflow_json`、`fields`；仅三个内置协议可保存 |
| `concurrency_limit` | 可选 1–1024 整数；保存元数据不代表已新增渠道调度执行能力 |

公开 JSON 禁止凭据字段和非有限数字，大小受限；参考数量、字节限制和能力开关需满足实际类型。模型类型仍由不可变 `service_type` 决定。`runtime_profile` 省略保留，显式 null 清除高级配置，回到旧标准协议解析；不同模型类型不能通过 profile 冒充。读取时 profile 的可选字段可能为 null，表示未设置，编辑表单按空值恢复；用户填写的 JSON `null` 仍不能替代对象或对象数组。

新增秘密输入为 `secret_key?: string|null`、`headers?: [{name,value}]`。API Key 继续使用原 `apikey`：省略保留、null 清除、新值替换。扩展秘密规则：

- `secret_key` 省略保留，null 清除，非空值替换。第二密钥可保存历史配置，尚未接通的签名协议不会因此自动可用。
- `headers` 省略保留，空数组清除；已存同名 header 的空 value 保留原值，移除该行清除该 header。
- Header 名称规范化、大小写不敏感去重，最多 32 项，单值 4096 字节、总计 16 KiB；禁止 Cookie、认证覆盖、代理身份和保留的 transport 字段。
- 密钥和全部 header 值均服务端加密，不返回浏览器，不写浏览器持久化或日志。

保留旧遮罩值并新增请求头后再次验证总量；合并超限返回 `422 model_runtime_credentials_invalid`，不改原配置或加密信封。

读取新增 `runtime_profile`、`has_secret_key`、`headers:[{name,has_value}]`、只读 `credential_source: manual|beefapi`。读取响应不含 `secret_key`、header 值或任何加密信封。托管配置的来源、地址、模型身份、协议与企业密钥由服务端控制；普通 CRUD 不能冒充或替换企业授权。

## 画布目录和偏好

内置 RunningHub 本人配置使用 `api_format=openai` 和
`protocol=runninghub-workflow-image|runninghub-workflow-video|runninghub-workflow-audio`。
`apikey` 为积分 Key，负责结构查询、提交和轮询；`secret_key` 为企业级素材上传 Key。
浏览器只接收公开描述、稳定模型 ID 和凭据存在标记。宿主目录增加三个
`sourceKind=builtin` 条目，来源为固定 commit 的 `web/src/lib/plugins/builtin/workflows.ts`，
只开放本人自定义渠道与画布 scope；不冒充原 85 份声明式 manifest。

`POST /api/v1/canvas-runtime/ai/models/{id}/runninghub-schema` 正文仅为
`{"row_version":"…"}`。只能读取本人未删除配置，版本不符409，其他账号404，额外字段422；
上游HTTP/结构错误502，超时504。Workflow查询`/api/openapi/getJsonApiFormat`，
App查询`/api/webapp/apiCallDemo`，均不提交生成、不改保存配置；字段回填后仍须明确保存。
内置生成、输出和恢复范围见[画布内置 RunningHub](canvases.md#画布内置-runninghub)。
同名声明式`runninghub-workflow`选择独立`canvas_runninghub_workflow.v1`，仅支持视频；
使用Bearer创建及无任务ID参数的GET查询，不使用内置`runninghub_workflow`描述或上传Key。
公开动态参数保存在`default_options`并由原UI提交到本次`providerOptions`命名空间，
完整输入、响应、归档及原任务恢复边界见[声明式 RunningHub](canvases.md#画布声明式-runninghub)。
收费模型与企业授权按用户“先记录起来，暂不用真实供应商验收”保留为未验证项。

`GET /api/v1/canvas-runtime/workspace/model-config` 保留 `{models,channels,preferences,row_version}` 外形。`models` 是全部本人可见、未删除宿主模型的安全投影，包括已迁移旧绑定；附带完整运行 profile、默认、扩展凭据存在标记及 `selection_aliases`。`channels` 不再提供独立可编辑的模型目录。

画布将其投影为只读 `host-<config_id>` 渠道，每个 profile 的 `logicalModelId` 为稳定宿主 ID。旧绑定提供原 `channel::model` 到该 ID 的别名；改模型编码后仍可恢复原逻辑选择，不按同名模型猜测。无法恢复或已停用/删除的明确选择提示不可用，不自动更换供应商。

`PUT /api/v1/canvas-runtime/workspace/model-config` 只提交 `{expected_row_version,preferences}`。保留本人画布默认、助手模型与生成参数，不与标准业务 `context_key` 偏好混成同一个默认。任何带 `channels` 的旧客户端请求都明确拒绝，包括空数组；不能据此清空或覆盖宿主模型。

旧 `/canvas-app/settings` 与所有画布模型配置引导统一进入 `/ai_config`，跨 HTML 离开前等待模型偏好与画布保存；失败或 409 留在当前编辑器保稿。`return_to` 仅允许同源具体画布路径，宿主页返回后重新读取目录；不能跳任意外部 URL。

## 执行、测试和历史

新任务只读取服务端已保存运行 profile 和凭据，派生 `capability_cache` 并冻结受理时配置。能力和协议变更不改历史记录；旧结果仅在模型版本仍一致时回写派生缓存。首次提交仍检查当前配置启停/删除状态，停用可能阻止尚未提交的排队任务。

模型发现不保存、不生成；发现到模型 ID 不代表该协议全部生成能力已经验收。仅在用户主动点击“获取模型”时，向当前填写的服务地址发送 API Key 和自定义请求头，第二密钥不参与发现。普通模式含请求头或明确删除请求头时使用 OpenAI 兼容发现合同，高级模式使用所选 API 格式；不因打开表单或修改字段自动探测。保存凭据仅能按本人的确切 `host:<ID>` 与同地址引用；旧普通渠道引用返回 `409 canvas_model_legacy_credential_reference`，不能恢复旧目录密钥。换地址不附旧凭据引用，且仍有遮罩请求头时阻止探测；清除 API Key 时显式 null，删除所有请求头时显式空数组，不复用旧值。

宿主模型测试复用本人独立画布模型测试任务，冻结已保存配置，不采用浏览器伪造的协议或凭据；测试影子配置在同事务中隐藏，不显示为新增模型，也不改变原模型的版本和派生缓存。原 BeefAPI 连接、取消、重连、断开与钱包入口在宿主提供，保留原服务端授权和恢复机制；不再要求打开 BeefTV 整站设置页。

扩展凭据变更会失效派生能力缓存；凭据身份同时包含 API Key 与扩展信封，标准生成、画布、模型测试及 Agent 的能力证据不能跨请求头账户复用。配置读取使用当前应用的 Settings 和加密主密钥，不改现有主密钥。

Agent 按保存的 profile 选择 OpenAI Chat Completions 或 Responses；未知、非 OpenAI 或非这两类协议的能力验证返回 `422 unsupported_agent_protocol`，异步执行明确记录失败，不按旧地址推测回退。能力验证、实际执行及回放从私有冻结快照解密请求头，经现有 SafeTransport 发送；公开回复、流式输出和候选正文按 API Key 与请求头值脱敏，私人协议历史保持原文。旧任务缺少新增字段时兼容原信封。Secret Key 不参与 Agent 签名调用。

无高级 profile 的既有 ModelHub 视频配置仍沿原适配器运行；画布鉴权范围包含原已可执行的 `modelhub_video.v1`，未因此开放其他音频、插件或未知协议。

标准配置 `runtime_profile=null` 保留原行为；显式高级协议按当前已接通的 Python 适配器运行。画布普通纯文字及独立模型测试已接通 `api_format=claude / protocol=claude-api` 的 `claude_messages.v1`，范围见[画布 Claude 合同](canvases.md#画布-claude-普通纯文字)。Gemini 普通纯文字接通 `api_format=openai / protocol=gemini-generate-content|google-gemini-generate-content` 的非流 `gemini_generate_content.v1`，对应固定源 hosted 派生格式；`api_format=gemini` 保留 custom gate，节点受理和宿主测试分别返回 422 与 400，详见[画布 Gemini 合同](canvases.md#画布-gemini-普通纯文字)。标准生成和 Agent 不开放这些画布专用适配器；尚未完成的多模态、thinking、工具和签名协议分支仍按各自准入边界拒绝。配置表单存在不等于完整协议接通或真实供应商验收通过。最新授权允许下述 Agnes 三种免费模型真实调用；Claude、Gemini 供应商本身仍未验。

数据库部署顺序见[统一模型迁移](../数据库模型/migrations/2026-10-06-host-model-runtime/README.md)。

画布音频现支持本人保存的 `api_format=openai / protocol=async-audio`，
使用 `canvas_async_audio.v1` 的创建、原 ID 轮询及 content 下载合同；
`openai-audio` 保留同步二进制合同。凭据和音频参数均从服务端冻结，
已生成音频的归档失败不能通过 `retryOf` 再生成。
MP3/WAV/Opus/AAC/FLAC在画布路径解码归档；PCM保留原字节和未知时长。
音色默认值与两协议manifest命名空间按源优先级解析，`default_options`随模型冻结。
范围、错误/恢复和其余未完成边界见[画布音频协议](canvases.md#画布音频协议)。
本轮无需新增表或迁移；音频真实供应商和企业授权仍未验。

Chat/Responses普通画布文字图片引用使用保存的 `capability_config.text.references` 限额，
两协议受控HTTP/MySQL/MinIO与原UI专项30项通过。文字视频新增 `maxVideos/maxVideoBytes` 校验，
冻结永久资源与真实元信息，视频运行/原UI仍在验证；详见[文字引用合同](canvases.md#画布文字图片引用)。
Responses保持固定源默认manifest的text/image_url/video_url与独立instructions，
不得将受控协议测试记为真实供应商验收。

## Agnes 免费模型

2026-10-10 用户授权按[官方指南](https://wiki.agnes-ai.cn/zh-Hans/docs/overview)
调用下列准确模型名，并允许修复后重复验收。保存地址为
`https://api.agnes-ai.cn/v1`，`runtime_profile.version=1`、`api_format=openai`；
API Key 沿既有服务端加密配置保存，公开目录和浏览器仅接收存在标记。

| 类型 | `model_key` | 保存的 `protocol` | 画布执行合同 |
| --- | --- | --- | --- |
| 文字 | `agnes-3.0-flash` | `chat-completion` | 既有 `/v1/chat/completions` SSE |
| 图片 | `agnes-image-2.5-flash` | `openai-image` | JSON `/v1/images/generations`；文生图和内联参考图 |
| 视频 | `agnes-video-2.5-flash` | `openai-videos` | JSON `/v1/videos`；按原 `video_id` 查询 `/agnesapi` |

图片准确模型名仅在画布 OpenAI 图片路径选择 Agnes 合同。源 `auto/low/medium/high`
质量映射为 `1K/1K/2K/4K`，宽高比使用官方 `ratio`；Base64 格式放入
`extra_body.response_format`。参考图由 Worker 从已授权永久媒体读取原字节，
转换为 `extra_body.image` 中的 Data URI，不发送私有 MinIO 展示 URL，
不持久化内联图像到 `resolved_parameters`。沿源每图 30 MiB、最多 16 图及宿主
总量 100 MiB 限制，mask 和透明背景在调用前拒绝。

视频 Flash 仅接受 `720P`、字符串 4–12 秒、单个输出；保存能力和节点参数应与此
一致。纯文字为 `mode=text`，首尾帧为 `keyframe`，图片/音频参考为 `reference`；
最多 5 图、3 音频，不接视频参考。参考媒体须为可公开访问的 HTTPS URL；
本机永久资源返回 `422 reference_media_requires_url`，沿既有补链接流程处理。
创建只保存 `video_id`，不以响应的 `id/task_id` 替换轮询身份；查询带
`model_name=agnes-video-2.5-flash`。完成以公共 `status` 和顶层 `url` 为准，
内部进度及未完成时已有 URL 均不算成功，也不编造 content 下载入口。

仅 Agnes 视频创建显式读取最多 64 KiB 的服务端错误体；实测 HTTP 503、顶层
`code=video_queue_full`、`data=null` 且无任务 ID 时，记录
`provider_queue_full` 和“未创建供应商任务”，允许用户稍后显式重试。
不会自动再次 POST；其他 5xx 或不确定回执仍保留受理未知保护。
原节点与任务详情保留“队列已满、本次未创建供应商任务”，显示原显式重试入口；
消费者持久化分类后二次解释仍保留原因，受理未知阶段始终禁止重提。

真实应用验收、红批、媒体字节、运行方式和未验范围见
[Agnes 验收审计](../plans/2026-10-10-beeftv-agnes-live-audit.md)。
当前文字、文生图、图生图×两屏宽六场完整通过；视频390px已通过真实生成、归档、
原UI播放/fresh恢复及可见无文字内容，实际1280×720/4.48秒。桌面仍队列满未通过；
视频存在音频流，听觉内容未验，明确队列满不记为成功。
共用纯对话助手在画布和标准模式中已分批实际验证 Chat Completions 与 Responses
两协议/两屏宽，已保存作品上下文、移除作品后的历史、复制和仅cookies恢复通过。
画布和标准Chat图片/Skill四场分批完整通过；Agnes Responses图片输入实测HTTP400，
普通文字Responses通过不能当图片通过。当前图片上下文优先使用已验Chat配置。
实际范围与供应商拒绝原因见
[助手真实审计](../plans/2026-10-10-beeftv-assistant-agnes-live-audit.md)。
两端/两协议/两屏宽的真实停止和完整回执后的进程中断恢复16场已分批通过，原SDK
仅本地重放原回执、供应商POST仍一次；受理未知八场也分批完整通过，共24个独立
生命周期场。真实200响应头后中断保留unknown/raw空、精确系统失败提示和零模型
结果/增量，stale/current送达均不重发。使用任务独占执行器
和自然150秒有效租约，不代表正式Agent Worker/AMQP或同版八角色部署。
本片没有数据库结构或依赖变更，完整迁移和正式部署仍按各自门禁核验。
