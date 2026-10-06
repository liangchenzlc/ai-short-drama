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

标准配置 `runtime_profile=null` 保留原行为；显式高级协议按当前已接通的 Python 适配器运行。Claude、Gemini、RunningHub、签名协议及其他尚未实现分支仍明确拒绝，配置表单存在不等于完整协议已接通或真实供应商验收通过。

数据库部署顺序见[统一模型迁移](../数据库模型/migrations/2026-10-06-host-model-runtime/README.md)。
