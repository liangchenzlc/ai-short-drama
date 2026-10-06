# BeefTV 文字多模态、思考及工具请求审计

日期：2026-10-06。源版本：`4ca2a65a7780a8dfcaaa86c33679f84fb04e055c`，已用源仓库 `git rev-parse HEAD` 核对。本文件只记录代码调用链和下一实施切片，不代表功能已实现或真实模型已验收。

本文的“当前”和“缺口”指源码审计完成时的基线。后续实施和实际验证状态见[实施记录](2026-10-05-beeftv-implementation-log.md)。审计之后已增加保存能力派生缓存、旧绑定模型的准入刷新和旧任务回写版本保护；这些改动不能作为供应商或完整画布一比一验收的证据。

## 结论与下一切片

下一最小片应完成 **普通画布文字节点引用已保存图片，先接 `openai_chat.v1`，继续复用已有任务、正文 SSE 回放和源生成回填**。画布节点的图片引用是真实调用路径；思考开关不是普通画布节点已启用的功能。Gemini、Claude、结构化工具回复、提示词优化和图片审美分析分别有自己的调用链，不能用一个“文字多模态支持”状态覆盖。

审计阶段只新增本文，没有修改生产代码、数据库、视频实现或源仓库，没有启动服务或调用模型。

## 1. 源真实路径与可达矩阵

| 功能 | 真实入口与请求 | 源实际执行范围 | 宿主当前缺口 |
| --- | --- | --- | --- |
| 普通文字生成 | `pages/canvas/canvas-text-generation-executor.ts:70` 调 `runCanvasGenerationTaskToConsumer`；`lib/canvas/canvas-project-generation.ts:20` 转 `runBackendGenerationTask` | `canvas_text` / `operation=text`，送 `effectivePrompt`、图片与视频引用、节点关联；没有传 `enableThinking` 或 `textHistory` | 纯文字已接通，`generation_payload` 拒绝文字图片和视频引用 |
| 上游文本、连线和 `@` 引用 | `components/canvas/canvas-node-generation.ts:71` 的 `buildNodeGenerationContext` | 连线文本按源顺序组成 prompt，媒体单独传 references；composer 与显式引用走 `buildComposerGenerationContext` | 源算法已保留，不应重写为后端拼接或重新排序 |
| 普通文字图片引用 | 同一 executor 的 `referenceImages: generationContext.referenceImages` | 引用先保存为资源，然后 Go `HydrateMedia` 读取或签发地址，协议构造时才加入正文消息 | 缺少 text 专用稳定引用冻结、运行时多模态 body 构造 |
| 普通文字视频引用 | 同一 executor 的 `referenceVideos` | 请求路径可达；是否可用取决于保存的模型能力、协议和实际上游；Claude fallback 明确拒绝视频 | 当前明确拒绝；应作为后续独立片，不能本片顺带放行 |
| 思考开关 | `pages/create/index.tsx:742` 与 `creation-workspace.tsx:619` | 独立创作页面将 `enableThinking` 写入 `textOptions.thinking`；普通画布 wrapper 未传该选项 | 此创作页不在当前普通画布片中；不新增画布思考 UI |
| 思考增强或 reasoning 档位 | 普通画布目录没有可达的独立增强开关 | 不能把 `thinking` 类型、助手过滤 `<think>` 文本或任意 providerOptions 认作一个启用功能 | 不增设 UI 或新增字段；具体来源另审计 |
| 提示词优化 | `canvas-prompt-optimizer-drawer.tsx:434` → builtin `prompt-optimizer.ts:332` → `services/plugin-host.ts:16` | 插件取得 `ai.text` 权限后调 `image.requestToolResponse`；使用 `optimize_prompt` 工具、模型适配规则和参考消息 | 此路径走 source relay，当前不能靠普通 `canvas_text` 片闭合 |
| 图片审美分析 | `canvas-project-editor-dialogs.tsx:278` → `ai-art-critique-modal.tsx:209` → `services/art-critique-execution.ts:13` | 真可达；图片先读取为稳定资源，每阶段一个强制工具请求，最多 9 个不同阶段 | `agentRequests` 契约、工具响应解析和此工作流任务关联仍缺失 |
| 当前画布助手 | 官方替换内核与 `/assistant/*` | 源 AGENTS 明确旧 `/agent/*` 和旧 cloud_agent Worker 已退场；不能恢复旧模型循环 | 仍属 M5 单独交付，不用审美分析 helper 证明助手全部完成 |
| mask | 普通文字 executor 没传 mask；源只有通用 task options 定义 | `HydrateMedia` 可以水合 mask，不代表 text 协议会使用；文字请求构造未把 mask 加入正文 | 保持文字 mask 拒绝；当前图片蒙版已实现的合同不动 |

`BackendGenerationTaskOptions` 中出现 `enableThinking`、`mask`、`textHistory` 等字段，只表示 helper 可以接受。普通画布执行器实际传了什么，应以调用者为准。

## 2. 准入、保存与最终 provider payload

### 2.1 浏览器请求

`web/src/services/api/generation-task.ts:346` 的 `backendGenerationTaskInput` 写出：

- `textOptions = {stream: streamText !== false, thinking: enableThinking === true}`；普通画布没有传这两个选项，所以默认 `true / false`。
- 引用经 `prepareBackendImageReference` / `prepareBackendMediaReference` 保存，任务使用稳定资源定位值，保留数组顺序。
- 只有 `logicalModelId` 存在时才附 `capabilityOptions`；当前 text 的 `logicalCapabilityOptions` 没有生成额外 text 选项。
- `generationMetadata` 可从保存的 profile defaultOptions 产生带 protocol namespace 的 `providerOptions`；这是独立来源，不等同 `textOptions.thinking`。
- `backendProviderConfig` 保留源的 `systemPrompt`。已有宿主 bridge 将渠道和凭据解析为本人模型身份；不得把浏览器凭据、URL 或 header 带进新 text envelope。

准入顺序仍是 task normalize → managed credential resolve → catalog select → capability / media 校验 → protect secrets → persist。源私有渠道、managed BeefAPI 与 hosted logical 路由不是同一种默认值处理路径；不要为 text 重复浏览器 transport 配置。

### 2.2 Go 执行路径有优先级

源 `backend/internal/generation/executor.go:38` 先解析配置、计算实际 streaming，再做 media transport 检查和水合。`dispatch:143` 中有 `AgentRequests` 才走 `RunAgentToolTask`，普通 text 走 `RunTextTask`。

**`text.go:719` 的 `RunTextTask` 优先采用已安装 declarative adapter。** 只有没有该 adapter 才走内置 Chat / Responses / Claude helper。因此下面两组 wire 必须分开描述。

| 协议 | 已安装官方 manifest 的普通 text wire | 非 declarative fallback helper |
| --- | --- | --- |
| Chat Completions | `/chat/completions`；`messages`；图片为 `{type:image_url,image_url:{url}}`；视频为 `video_url` | 图片、视频相同；system → history → 当前 user |
| Responses | `/responses`；`input` 过滤 system，`instructions` 单独传；当前消息 content 仍来自共用 `manifestRequestValues`，图片仍是 `image_url` | `TextResponseContent:933` 才使用 `input_text / input_image / input_video` |
| Claude | 官方 manifest `/v1/messages`；system 独立；messages 过滤 system；引用消息仍保留共用 `image_url` 形状 | `ClaudeTextContent:864` 才转 `{type:image,source:{type:base64|url,...}}`；视频明确拒绝 |
| Gemini | 官方 `gemini-generate-content` manifest `/v1beta/models/{{model}}:generateContent`；消息被映射到 `parts:[{text:message.content}]` | 普通 text 没有对应内置 fallback；`apiFormat=gemini` 且无 `AgentRequests` 时执行校验拒绝 |

共用 `backend/internal/protocol/manifest.go:1034` 的 `manifestRequestValues` 生成当前 user：先 `text`，再按输入顺序追加 `image_url / video_url / audio_url`；没有媒体时 content 为字符串。官方 manifest 具体定义位于 `plugin-packages/openai-chat-completions/manifest.json`、`openai-responses/manifest.json`、`anthropic-messages/manifest.json`、`google-gemini-generate-content/manifest.json`。

以上是固定源码产生的形状，**不是供应商已接受的证据**。Responses、Claude、Gemini 的普通任务 manifest 与各自专用 helper 的多模态形状不同。迁移时不能悄悄拿 helper 的规范形状替代源默认路径，再声称 wire 一比一；若改正该源差异，应在实施记录列为功能修复并重新验收。本片选 Chat 图片，就是先闭合没有上述 wire 分歧的实际路径。

### 2.3 system 与历史也存在分支差异

declarative 的 `ProtocolRequestFromInput` 接受非空 user / assistant / system 历史；`manifestRequestValues` 只在没有历史 system 时追加配置 instructions。Responses / Claude / Gemini 再由 manifest 拆开 system。

fallback 的 `ValidatedTextHistory:920` 仅保留 user / assistant，trim 后跳过空文本。宿主当前 `generation_payload` 直接保留 `TextMessage`，再在开头加入 `config.systemPrompt`。普通画布没有历史参数，因此本片只需确保配置 system + 当前 effectivePrompt 的顺序与内容；不能把普通节点扩展为聊天 UI。

## 3. thinking 实际行为

源 `text.go:821` 的 `ApplyTextThinking` 仅在 `thinking=true` 时追加：

| 已知 wire | 追加字段 |
| --- | --- |
| chat-completion | `reasoning_effort: medium` |
| responses | `reasoning: {effort: medium, summary: auto}` |
| claude-api | `thinking: {type: enabled, budget_tokens: 1024}` |

它由内置普通 text helper、内置 Agent task、已知 wire 的 declarative Agent task 调用。**普通 declarative create 没有调用此函数**：`ProtocolRequestFromInput` 未放入 TextOptions，`tryStreamingDeclarativeTextCreate:196` 只追加 stream（Chat 另加 usage）。因此 `textOptions.thinking=true` 在这条默认普通任务路径不会自行变成上述字段。

官方 Responses / Claude manifest 可以直接读取 profile 的 `providerOptions.reasoning / thinking`；那是另一条显式配置路径。不能把它归因于创作页开关。未知 Agent wire 开启 thinking 会明确拒绝，不会构造通用 Gemini 思考 payload。

普通画布文本最终回填只读取 `result.text`。reasoning 不拼入作品正文。源独立创作页可以展示模型返回的 reasoning；普通任务 SSE 归档仍是正文，因为 `app/provider.go:126` 只连接 `OnTextDelta`。

## 4. 宿主现状与可复用部分

| 位置 | 当前实际能力 | 新片如何使用 |
| --- | --- | --- |
| [canvas_generation_inputs.py](../../backend/src/short_drama/service/canvas_generation_inputs.py) | 纯 text 的 system/history/current user；明确拒绝 text 图片、视频、thinking；provider/capabilityOptions 受限 | 保留标准文本输入；为可信画布来源增加独立文字引用 envelope |
| [canvas_task_runtime.py](../../backend/src/short_drama/schemas/canvas_task_runtime.py) | 原 `textOptions`、稳定 reference 结构；普通非视频最多 16 图；没有 agentRequests | 本片无需添加浏览器工具 JSON；已有字段可表达图片引用 |
| [ai/adapters.py](../../backend/src/short_drama/ai/adapters.py) | text adapter 只有 `openai_chat.v1 / openai_responses.v1`，content 只接受字符串 | 图片 recipe 只对可信 canvas text 分支开放，标准模式字符串合同保持 |
| [agent/model_gateway.py](../../backend/src/short_drama/agent/model_gateway.py) | 同样只允许两类 OpenAI adapter；Pydantic AI binary prompt / tools / history 是另一条业务合同 | 不直接复用为普通 canvas task，不借它声称 Gemini/Claude 已迁移 |
| [canvas_model_catalog_service.py](../../backend/src/short_drama/service/canvas_model_catalog_service.py) | 只有两类 text protocol 映射；当前专门冻结 video capability | 保存的 text profile 能力需写入 trusted capability cache 并冻结到任务 |
| [ai_generation_service.py](../../backend/src/short_drama/service/ai_generation_service.py) | 已验证永久 MinIO 图片、资源范围、本人模型，创建后冻结 snapshot；锁内重查 scope | text 图片必须沿用同样永久资源和范围检查，不能只新增 URL 字符串 |
| [generation_execution_service.py](../../backend/src/short_drama/service/generation_execution_service.py) | `StoredImageReferences`、内置 `on_text_delta`、租约、冻结凭据和最终归档 | 增加 text 的 loader 连接，不改视频和已完成 image/mask 分支 |
| [canvas_text_stream.py](../../backend/src/short_drama/service/canvas_text_stream.py) | 正文分块归档、作者 quota、4096 sequence、TTL、租约失效保护 | 继续使用；多模态输入不应改变 SSE 输出合同 |
| [canvas_task_events.py](../../backend/src/short_drama/api/canvas_task_events.py) | `progress / delta / terminal`，delta 的事件 ID 是 sequence；重连重验账号与项目权限 | 继续使用；不得把 task ID 作为游标或将 reasoning 混进 content |

目标不是已有四类 Python text provider 可直接开关。源 React `image-streaming.ts` 有 Gemini / Claude，但这是 source browser relay 的实现；当前 Python Gateway 和 Agent Gateway 均没有这些 text adapter。

## 5. 建议的最小可实施片

### 5.1 片 A：Chat 文字节点 + 已保存图片

建议改动范围（待父任务正式实施时落地）：

1. `service/canvas_model_catalog_service.py`：从已保存 profile 冻结 `canvas_text_capability`（streaming、promptMaxChars 和图片限额），保留本人渠道权限；补保存/恢复回归。
2. `service/canvas_generation_inputs.py` 与新的独立 text admission helper：仅 `mode=text / operation=text / adapter=openai_chat.v1` 开放引用，检查来源 resource、顺序、数量及稳定 media ID；schema 不开放任意 content JSON。`create_locked` transform 中验证、冻结 scope 和能力。
3. `service/ai_generation_service.py` / `canvas_generation_service.py`：将 trusted text envelope 放入 prepared request / config snapshot，沿现有 scope 二次检查；不将签名 URL 持久化。
4. 独立 `ai/canvas_text_adapters.py`：以源 Chat payload 组成 system/history/current user，多图按源顺序生成 `image_url`；标准文本 `messages[].content` 保持字符串约束。以 typed envelope 限定此分支，而不是放宽全局 `_input`。
5. `ai/gateway.py` / `service/generation_execution_service.py`：Worker 才读取已授权 MinIO bytes 或签发执行期 URL；使用现有 `StoredImageReferences` 和预算/大小限制。不得根据浏览器 data URL、URL 或 MIME 跳过授权与媒体校验。
6. 单元、native 集成和源 UI 浏览器用例；维护 `docs/api/canvases.md` 与实施记录。本片预计不改 ORM/表结构，现有 JSON 冻结和 text delta 表足够；没有 DDL 就不生成空迁移。

实际实施前应列出上述跨层文件计划。推荐先写拒绝图片的现状复现、再做准入和 transport 红转绿，不改 React 布局/交互。普通源 wrapper 仍固定 `stream=true / thinking=false`。

保存能力是服务器当前可接受的 profile，而不是浏览器本次 task 的 `capabilityConfig`。没有明确视觉能力不能从模型名推测开启；源默认 text references 最大图片数为 0。源私有路径能力校验与 hosted logical 约束不能混称，本片应明确权限边界适配而不是伪装成 source hosted 路线。

### 5.2 后续片分别列验收

- **Responses 图片**：先确认默认 manifest wire 与已迁入 source UI 的协议选择，列清是否保持源形状或修正 source 默认请求；补真实接受证据后再计功能完成。
- **Claude / Gemini**：新增可信 adapter、catalog 映射、auth、URL join、非流/流 response decoder、异常与恢复证据；不单靠前端已经有函数认定可复用。Gemini普通 task gate与 Agent plugin gate要保留来源差异。
- **文字视频**：保存/水合、typed video引用、模型能力、长媒体限制及 wire 自成一片；不要顺带修改当前视频生成 adapter。
- **独立创作页 thinking**：只有该页纳入迁移后才闭合 UI → task → providerOptions / 已知 wire；必须覆盖普通 declarative 未应用的源事实，不能把增强效果写为已保证。
- **审美分析、提示词优化、其他工具**：保留源强制工具/自动兼容回退、structured output 校验、阶段数量和工具回复解析。审美分析的 `agentRequests` 走持久任务；提示词优化源走 relay，迁入 Python 时需独立本人模型和工具 API，不直接恢复接受客户端任意 URL/header 的 `/ai/custom`。
- **画布助手**：源官方 pi SDK / assistant 会话与授权、提议/图操作是 M5 范围；禁止用旧 cloud_agent 路由替代。Timeline / Director 对应 M4。

## 6. 验证与完成界限

片 A 正常路径至少包括：图片 → 文字入边；配置节点 composer `@图片N`；绘图预览作为稳定图片；单图与多图顺序；带 system；关闭 stream 的任务 API；任务正文增量、刷新后 replay、最终节点直接回填及文本份数布局。任务 SSE 能力与原画布普通文字节点的展示行为分开验收：普通文字节点保持加载态至任务成功，不逐段展示正文。

边界至少包括：未保存 reference、跨项目/跨作者 resource、已删除资源、非图片 MIME、超模型张数/字节、引用顺序变动后的幂等冲突、保存冲突、租约失效、流中途断线保留 partial draft、重连仅补 sequence、改账号或移除成员后关闭流、限额停止读取、无浏览器重新 submit。仅获取 replay 不得触发 paid POST。

源和宿主任务 SSE 都归档正文；reasoning 不得进入共享作品和正文 delta。任务、模型参数、凭据、对话和生成历史按本人隔离；已进入共享图的最终文本沿源直接回填行为共享。

已阅读的现有验证证据位置：`tests/unit/test_canvas_text_stream.py`（正文 observer、reasoning 忽略、无重复 final、invalid SSE）；`tests/integration/test_canvas_text_stream.py`（作者/成员隔离、游标、断线草稿、租约/配额、权限撤销）；`frontend/canvas/tests/canvas-generation-text-replay.test.mjs`。本次没有重新执行这些测试，也没有把它们描述为真实供应商验收。

本次验证仅为固定源码版本、可达调用者、已安装协议优先级、宿主当前实现和本文相对链接的静态核对。全部文字多模态/思考/工具功能及一比一运行对照仍需各切片实施和真实运行验收。

### 6.1 普通文字节点与任务 SSE 的调用者边界

固定 BeefTV 源 `web/src/pages/canvas/canvas-text-generation-executor.ts` 经
`runCanvasGenerationTaskToConsumer` → `runBackendCanvasGenerationTask` 调用任务 wrapper，
没有传递 `onTextDelta` 或 `useTextEvents`。宿主对应前端同样保留此链路。
`task-center.ts` 的 `shouldUseTaskTextEvents` 仅在上述选项之一存在时启用 `text-events`；
普通文字节点使用任务查询，成功终态后原消费者直接回填。
`canvas-node-content.tsx` 对 `metadata.status === "loading"` 显示加载内容，
所以不能将服务端正文 delta 或供应商 `stream=true` 描述为源普通节点实时展示。

后续原 UI 切片应证明：两张图片经原上传 input、原连接点创建文字及拖拽入边、
原生成按钮提交；供应商真实 HTTP stream 与 MySQL 私人第一段确已产生时，
原文字节点仍为加载态且没有提前写正文；释放供应商终态后由原消费者回填，
全新 context 恢复最终正文和原图片。测试不得为满足逐段显示假设增加 callback，
不得用 API 代建生成结果，也不能将该切片等同为所有文本多模态功能验收。
