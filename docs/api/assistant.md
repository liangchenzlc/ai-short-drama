# AI 创作助手 API

> 2026-10-28 兼容清理：Worker 只执行 Harness；`TaskRead` 不再返回 `interruptions_supported`。停止或暂停后使用普通消息继续，前端不再附带旧 resume 控制。决策次数和输出 token 预算必须为正整数，工具次数与活动时长仍可不限；下文相应的旧引擎、旧字段与旧预算描述为历史记录。

前端只保存和重放待确认的停止控制；旧 `resume` / `redirect` 本地记录会显示无法恢复并保留原记录，不会自动重发。停止请求的相同幂等键先查回执，即使停止开关随后关闭，也能核对已经受理的原请求。

## 新任务 Harness 契约（2026-10-26）

新 V3 任务固定 `langgraph-harness.1`，旧任务保持原引擎。`TaskRead` 新增 `interruptions`（`stop/redirect/resume`）与兼容字段 `interruptions_supported`；新任务控制只开放 `stop`，`redirect/resume` 返回 409（`assistant_redirect_unavailable` / `assistant_resume_unavailable`），前端按能力对象隐藏按钮。运行中补充仍按原输入版本在下一边界生效。下文句式授权和 redirect／resume 控制仅适用于旧引擎任务，不约束新引擎的自主工具选择。

Harness 处于 `stopped/paused_error/paused_budget` 时，向原任务发送普通消息（包括“继续”）即在同一 Task 下创建新 Run 并调度；刷新后仍保留暂停任务作为发送目标。消息和成果、已结算工具历史及活动 Skill 继承，撤销来源和遗忘规则继续生效。冻结模型配置被修改时返回 `assistant_model_changed`，活跃操作未结算返回冲突；相同幂等键复用已受理结果。此续执行不经过旧中断 control。Harness 的任务创建时冻结单 Run 预算（`AGENT_RUN_DECISION_CAP`/`AGENT_RUN_OUTPUT_TOKEN_CAP`），超限暂停为 `paused_budget`，"继续"即新 Run 续期，不设 Task 总上限；升级前的 `null` 预算快照继续按无限解释。

当前新冻结请求使用工具 schema v4；已有 v3 及更早请求按原冻结协议重放。写工具不含 `request_message_id/request_hash` 等句式授权字段，主体、目标、版本与服务端可信 Actor 校验保留。`load_skill` 参数为 `name/content_version/purpose`；`read_resource` 参数为目录 `ref_id/purpose` 及可选 `detail/crop/offset/limit`；`read_skill_resource` 使用 `activation_id/path/offset/limit/purpose` 及可选图片 detail／crop。方法包仅支持文本和图片资源。schema 的唯一来源为 `agent/tools/registry.py`，客户端不可构造任意工具历史。

基础工具常驻，`tool_catalog` 列可用能力组、用途及启用状态。模型通过 `enable_tool_group(group)` 启用 `standard_write/canvas_write/media/memory_write/image_adoption`，下一决策追加该组 schema；仍按任务模式、媒体开关及可信范围裁剪，不额外授予权限。`confirm_requirements` 确认服务端按用户原话生成的 draft（`keys`），可附有原文引用的 `additions`，不能自行删除或降低数量；引用更新用户消息并包含明确数量修订时，旧定义转为 superseded，新定义成为有效要求；`record_task_observation` 保存引用成功读取操作的观察或推断；`release_skill` 只释放自动方法，不移除手选方法。具体配额和字段见 [Harness 运行机制](../instruction/AI创作助手Harness运行机制.md)。

需要验收的候选最终答复进入内部 Turn，由独立 Turn 的 `submit_completion_review` 结构化检查用户来源、实际操作、成果数量和答复引用；该检查不是业务工具调用。没有已确认要求且没有成功写操作时，普通讨论直接发布。验收 Turn 不继承执行器工具轨迹，而是接收服务端根据需求表重建的证据包与确定性检查结果，验收模型优先使用本人另一个文本模型。发布前另有确定性守卫：有成功写操作却没有任何已确认 `write` 要求时拒绝发布（`agent_requirements_unconfirmed`），未覆盖的成果类别自动补 `server_derived` 要求。监督请求暂存流式文本，工具旁白在结算时发布，需要验收的最终回复通过检查后发布；缺项返回原 Agent 继续。停滞按当前已应用输入版本的完整滑动窗口判定：不足窗口不累计空转，完整窗口内无新证据连续两个窗口以 `agent_progress_stalled` 暂停；同组完成缺项连续 6 次未解决以 `agent_completion_stalled` 暂停。未结算生成等待和编排类工具不触发空转暂停。暂停后普通消息可继续。

完成检查 v2 的已有要求只提交 `checks[].key/satisfied/evidence_operation_ids/answer_quote/reason`，服务端复用已登记定义；新增遗漏要求放入 `additional_requirements` 并提供真实用户来源。要求类型中的 `observation/contract` 分别核对有来源观察笔记和已登记要求，与 `write` 的业务成果分开计数。旧 v1 冻结检查仍可按原协议恢复。

内部任务 frame 的 `requirement_sources` 提供有配额的用户原文、消息 ID、seq 和截断状态，供 L4 压缩后的来源引用；不把摘要文本当作原文。来源遗忘后不再注入，缺失原文仍使用现有历史读取工具。

选入主体的 `read_image_content` 需要 `subject_key/group_id/item_id/media_id/purpose`，从 `read_image_group` 图位的 `read_content_args` 复制身份。新工具结果增加 `receipt_operation_id` 标记当前操作，不能当作原保存批次。`read_task_results(operation_id)` 必须引用 `result_sources` 中的成功保存 ID，正文为 `results[].content`；读取不创建成果引用。当前仍选入、由本任务更新的素材／分镜也能回读其保存快照。`describe_creation_target` 增加可选 `result_before_id/result_limit`（默认 10，最多 20），返回分页 `result_sources.items/next_result_before_id`，不读取正文。

工具正文只保存在 Operation，运行检查点、ToolCall、pending results 和冻结请求以操作引用访问；媒体只在本次模型请求水合，持久化历史无像素编码。压缩独立保留活动 Skill 的调用／结果对。纯讨论重答仍只读、原位替换，不带入未来历史。协议、预算与目录入口见 [Harness 运行机制](../instruction/AI创作助手Harness运行机制.md)。

标准模式和无限画布共用 `/api/v1/assistant`。当前入口使用本人项目会话（`scope_version=3`、绑定 `project_id`）、逻辑任务与可恢复的执行尝试；同项目切阶段不更换会话，跨项目分别读取。`/global` 保留为兼容路径前缀，历史未绑定项目的 V3 不自动回填或并入项目。V3 已开放目标说明、主动主体读取、操作回执、本任务成果读取、四个只读记忆工具、四个显式记忆写工具、三个 Skill 工具、有界标准／画布结构批次、标准图片组冻结目录与主图设置，以及标准素材／分镜图片、普通／原生有声分镜视频、角色音色、旧声音文档台词配音和画布图片／视频／音频批次生成与完整结算。多批次反序撤销、Skill 广义语义消歧及真实模型验收仍在实施。工具的自动测试不代表真实模型或媒体供应商验收。进度与证据见[实施记录](../plans/2026-10/2026-10-21-AI创作助手实施与验证记录.md)。

所有接口注入可信 Actor，禁止客户端指定 owner 或系统 Session。会话、消息、任务、操作、附件、模型和记忆仅本人可见；作品须另行校验所有权。数据库 ID、行版本、内容版本及控制水位使用十进制字符串；事件 seq/cursor、分页和消息位置使用数值。JSON 拒绝未声明字段，写请求沿用 Cookie、Origin 和 CSRF 校验。

## 时间线与可见性

消息与任务操作共用会话级 `item_seq`：`agent_messages` 与 `agent_operations` 在写入时领取同一发号器，客户端按该数值升序合并即可还原「叙述 → 步骤 → 叙述 → 产物」的真实顺序。历史会话已按 `(created_at, id)` 回填，缺失时前端回退到消息 `seq`。

| 字段 | 位置 | 含义 |
| --- | --- | --- |
| `item_seq` | 消息、操作、`step.started`/`step.finished` 事件 | 会话内单调序号，唯一的排序依据 |
| `kind` | 消息 | `reply` 正式回复、`narration` 工具前的过程叙述、`notice` 失败或迟到候选提示 |
| `visibility` | 操作 | `silent` 不渲染、`step` 折叠成步骤组、`artifact` 独立产物卡 |
| `label` | 操作 | 面向用户的一行文案，前端不再维护工具名映射 |

`silent` 工具（方法检索、记忆读写、读回执、读生成策略）不得出现在界面；方法读取（`load_skill` / `read_skill_resource`）属于 `step`，在步骤组内折叠展示，连续同名合并为一行。原始回执、工具参数与返回值一律不进入展示层，审计数据仍保留在库内。

## 上下文与用量

系统提示只保留稳定策略文本，本轮状态（授权、技能、历史、回执、待审计划）作为用户消息下发，续段仅在状态变化时重发，以保持可缓存前缀。`run.usage` 在供应商返回时包含 `cached_tokens`（chat 取 `prompt_tokens_details.cached_tokens`，responses 取 `input_tokens_details.cached_tokens`）；未上报时不出现该字段，不能当作命中数为 0。

## 停止与补充要求

`POST /tasks/{id}/control` 的语义按引擎与任务冻结策略区分：

| 请求 | Harness（`agent_policy.stop=boundary.v1`） | 旧引擎冻结任务 |
| --- | --- | --- |
| `{kind:'stop'}` | 放行：无在途段时立即结束；模型段进行中只置取消标记，段结束后把已流出文本落成 `narration` 并补 `run.interrupted`，Run 以 `cancelled` + `agent_stopped` 结束；工具执行中仍等当前工具结束 | 同左（历史行为） |
| `{kind:'redirect', content}` | 拒绝，`assistant_redirect_unavailable`；改向改为发送普通补充消息，在下一工具边界应用 | 写入新输入版本，同一任务内下一段生效 |
| `{kind:'resume', expected_control_seq}` | 拒绝，`assistant_resume_unavailable`；暂停或停止后直接发送消息，在原 Task 下创建新 Run | 复制原 Run 的模型快照继续 |

`TaskRead.interruptions` 分别给出 `{stop,redirect,resume}` 能力，前端据此显示按钮；`interruptions_supported` 保留为 `stop` 的兼容投影。停止后已生成的候选与文本都保留；`run.interrupted` 只表示本次运行被中断，不代表失败，可以继续发送新要求。

## 全局会话、逻辑任务与恢复

下表路径相对于 `/assistant`。

| 方法 | 路径 | 请求或响应 |
| --- | --- | --- |
| GET | `/status` | `enabled/schema_ready`，不调用模型 |
| GET / PATCH | `/generation-policy` | 本人媒体总开关；PATCH `{media_generation_enabled,policy_version}`，返回策略和媒体能力目录，见下文 |
| GET / POST | `/global/conversations` | GET 按本人分页，支持 `project_id/query` 并校验项目所有权；POST `{title?,project_id?}`，前端必传当前项目，必带 `Idempotency-Key`；省略项目保留旧账号会话兼容，不接受 archived |
| GET / PATCH | `/global/conversations/{id}` | 详情包含可空 `project_id`；PATCH `{row_version,title}`，不允许改绑项目 |
| GET | `/global/history` | 本人 V0/V1/V2 历史分页，支持 `project_id` 过滤，前端只读，不继承旧授权 |
| GET | `/global/conversations/{id}/deletion-memory` | 内部兼容的相关记忆预览，当前前端不调用，见下文 |
| DELETE | `/global/conversations/{id}` | 稳定幂等删除，见下文；返回实际 deleting/deleted 状态 |
| GET / POST | `/global/conversations/{id}/messages` | 分页历史；发送必带幂等键，返回 `message/task/run/cursor` |
| GET | `/global/conversations/{id}/messages/{mid}/revision-eligibility` | 核对纯讨论是否可修订，返回 `allowed/code/reason/row_version/current_content` |
| POST | `/global/conversations/{id}/messages/{mid}/revisions` | `{kind:'edit',row_version,content}` 或 `{kind:'regenerate',row_version}`；必带原 `Idempotency-Key`，返回 `message/task/run/cursor` |
| GET | `/global/conversations/{id}/messages/{mid}/revisions` | `offset/limit` 分页读取不可变的修订前正文、版本及来源／运行身份 |
| GET | `/global/conversations/{id}/state` | 一致快照，含任务、运行、操作、输入应用水位与 `snapshot_cursor/resume_cursor`；支持 `before_task_id/limit` |
| GET | `/global/conversations/{id}/runtime` | 运行兼容投影 |
| GET | `/global/conversations/{id}/events` | SSE，支持 cursor/Last-Event-ID；断线恢复不等于重新发送 |
| POST | `/tasks/{id}/control` | `{kind:'stop'|'redirect'|'resume',content?,expected_control_seq?,message_id?}`，必带幂等键 |
| GET | `/tasks/{id}/operations` | `after_id/limit` 分页，返回 `has_more/next_after_id/snapshot_cursor` |
| GET | `/tasks/{id}/subjects` | 当前或待生效引用的版本、校验值及可用性；返回输入水位、pending 和目录，不返回正文 |
| GET | `/tasks/{id}/attachments` | 当前或待生效任务附件；返回 `task_id/input_revision/applied_input_revision/pending/items`，每项只有 `id/kind/name/checksum_sha256/available`，无正文或媒体 URL |
| GET | `/subjects` | 必带 project_id/kind；query/offset/limit 分页读取可选主体的身份、名称、归属与版本，不读正文 |
| POST | `/subjects/metadata` | `{subjects:[主体引用]}`，只核对元数据，返回当前版本和校验值；不授予读取／写入权限，不调用模型 |
| GET | `/operations/{id}` | 本人操作及真实结果详情，不返回供应商原始请求／回执 |
| GET | `/operations/{id}/script-proposal` | 本人待审剧本建议及采用版本 |
| POST | `/operations/{id}/apply-script` | `{content_version}` → `{project_id,content_version}`，同建议重复采用返回原回执 |

`state` 默认取最近 20 个任务、50 条消息及最多 200 个操作，使用分页补齐，不能将有界快照当成全部历史。轨迹由操作账本和类型化事件生成，不根据回复文本猜测执行。

首次从快照的 `resume_cursor` 订阅，保留活动回复尚未持久成消息的 delta；轨迹按 `snapshot_cursor` 和实体版本去重。普通断线从已消费游标继续，不发送新消息或重做工具。V3 游标超出当前水位、缺少连续事件或末尾缺失时返回 `agent_event_resync_required`（首次读取为 HTTP 409，已开始流为 `event: resync`）；客户端重新读取一致快照、清空在途文本片段并从其恢复游标补流，保留已结算轨迹。前端发现序号缺口同样重新同步。

流中的暂时数据库等故障发送 `event: retry` 后关闭连接，客户端保留私人历史并按退避重连；只有认证／权限／会话访问终止或功能关闭才发送 `event: access-ended`，清空私人状态并停止自动重连。收到删除事件也立即结束访问。迟到快照不能覆盖更新水位、控制回执或访问终止状态；历史任务分页按实体版本合并，保留当前较高水位，不能因旧分页水位丢弃整页。

同会话串行执行；运行中普通新消息补充当前逻辑任务，显式 `new_task=true` 才创建独立排队任务。Harness 停止／暂停后普通消息直接续跑；旧引擎停止／暂停后补充仍保留，只有明确 resume 才恢复。resume 必须携带读取到的 `expected_control_seq`，可附 `message_id` 复用本任务已受理的用户输入，不追加“继续任务”消息；该字段只允许用于 resume，且不得与 content 同传，跨任务或非用户输入返回 404。省略字段保留旧调用行为及原幂等哈希。旧引擎前端在停止后发送新要求，先持久化消息，再携带该消息 ID 与发起时的停止水位继续；受理不明分别核对原消息和原控制请求，不越过后续新停止。新 attempt 继续原逻辑任务，不重做完成操作。stop/redirect/delete 先持久记录待生效请求，当前已开始工具完整结算后才生效，未开始的旧工具不得领取。媒体批次须等全部项生成及回填结果明确；受理不明保持核实及控制屏障，不把拿到任务 ID 当成完成。

## 明确选择的批次撤销

`GET /operations/{id}/undo-preview` 返回 `operation_id/preview_hash/items/receipts`。每项包含稳定 `key/kind/id/name/allowed/reason/requires`；读取预览不执行撤销。`POST /operations/{id}/undo` 必带原 `Idempotency-Key`，正文为 `{preview_hash,selected_keys}`，201 返回独立撤销回执。同键同体返回原回执，同键异体拒绝；受理不明保持原请求与选择，不能换键。

执行以会话、任务、操作与作品锁重新核对整份预览。预览过期返回 `assistant_undo_stale`，保护项返回 `assistant_undo_conflict`，未明确选择依赖项返回 `assistant_undo_dependencies`；这些情况均不部分执行。用户明确选中的安全子集在一个事务内逆向；行版本前进，不恢复旧版本号。原工具回执和生成费用保留，停止／改向不自动撤销。

当前接入 `apply_standard_patch` 的素材、分镜与旧声音文本，`apply_canvas_patch` 的节点和连线，`set_image_group_primary` 的主图，以及 `submit_generation_batch` 实际已采用的图片、视频、角色音色、台词配音和画布媒体绑定。标准结构含关联、排序、软删除恢复及原生对白。采用原操作的作品身份，检查当前对象、版本、关联、媒体、后续分镜／成片引用、活动 Run 与原生任务；旧回执缺少必要快照时保守拒绝。分镜排序依赖相关分镜一起选择，列表已有后续变化时拒绝。被原批次删除的素材只允许在本人可见原操作范围内回查，不扩大普通查询权限。

画布保守核对整份共享图版本，并校验选中节点的私人参数版本、连线顺序及依赖；后续图修改、引用、环或容量冲突均受保护。只合并选中差异，经原生保存服务写入，保留无关节点、视口和私人根数据；旧连线回执缺少恢复顺序时拒绝。声音文本及配音核对声音／成片双版本和时间线，保护后来媒体与多轨引用，只恢复选中行及其配音轨道。同一原操作可分次明确撤销安全子集，后续预览沿已保存的逆向版本链核对；不同操作之间的反序依赖撤销仍待专项验证。

媒体回退保留生成文件、候选和原回执，不退生成费用。图片恢复原绑定、当前图片组和主图；角色首次音色撤销保留空绑定版本行，后续采用必须使用递增的新版本。画布未实际绑定的项不能回退。已完整结算为 `failed/conflict` 的媒体批次也可预览其中有采用证明的安全项，原失败状态保持不变；受理不明或仍在执行的批次不能撤销。旧回执缺少采用／轨道快照时拒绝。

操作快照、分页与详情增加独立 `undo_receipts`；`operation.undo.completed` 携带 `receipt` 和真实逆向结果引用。客户端按回执身份去重，并在旧分页／终态事件重放时保留较新的撤销状态。原成果卡标示已撤销，已删除对象不再提供旧定位链接。模型上下文与 `read_operation_receipt` 同样附真实撤销回执，不能把历史创建结果当成仍存在的成果。

## 纯讨论历史编辑与重新回答

仅 V3 用户讨论消息可修订；不创建分支。编辑推进原用户消息版本，重答保留用户原文；使用原逻辑任务的新 attempt，成功后原位替换原回复 ID／seq 并推进版本，失败保留旧回复。历史消息读取包含十进制字符串 `row_version`，旧分页不能覆盖较新正文。

服务端以会话锁串行核对版本、活动运行、待处理输入／控制及依赖。自己或后续已执行工具、已保存有效记忆的来源不可改写，应发送新消息纠正；读取工具也算已执行。无法证明后续工具没有通过摘要间接依赖早期讨论，因此采用保守保护。修订前快照包含正文、版本、引用及成果，不能由客户端任意更新。

重答只使用原消息、原讨论之前的有限历史、原任务仍有效附件和冻结手动方法，不装入后来消息、旧回复或后来模型历史；所有工具禁用。停止后明确继续保持只读重答边界，补充／改向不能将其扩成业务执行。修订使本会话旧摘要及在途摘要／抽取失效，来源哈希包含消息版本，迟到输出不得重新发布旧事实。

本机修订草稿按账号／会话隔离。受理不明跨刷新保留原正文、版本和幂等键，仅用原请求核对；409 保留草稿，先读取当前消息，再明确采用新版本才可发送。损坏记录须明确清除。修订记录随会话删除清除，作品成果保留。前端历史操作当前接入标准模式，共享画布保持原交互边界。

## 任务创作方法与资料

V3 注册三个只读方法工具，与既有工具合计二十三项；它们不授予作品正文、写入、模型配置或媒体生成权限。

| 工具 | 参数与行为 |
| --- | --- |
| `search_skills` | `query` 最多 120 字符，`offset` 默认 0、最多 100000，`limit` 默认 12、最多 20；分页返回本人启用、允许自动、兼容当前目标且未被手动移除的方法摘要与 `total/next_offset`，无正文或资源目录 |
| `load_skill` | 使用搜索实际返回的 `skill_id/content_version`，可填最多 240 字符的 `purpose`；重新校验当前设置及版本、任务模式和必需工具，成功后返回 `activation_id`、冻结指令及资料 manifest |
| `read_skill_resource` | 使用本任务活动 `activation_id`、manifest 相对 `path`，`offset` 默认 0，`limit` 默认及最多 4000 字符；返回片段、checksum、范围及 `next_offset`，不接受 URL 或存储地址 |

手动选择在输入边界生效，覆盖完整逻辑任务；普通补充保留，显式选择空列表移除手动方法，自动加载不能加回已移除项。Harness 最多八个活动方法、正文估算 8000 tokens，手动方法优先挤出自动补充；只保留最近 16 条失活记录及活动记录，累计激活不设次数上限，原回执归档完整历史。`release_skill` 可释放自动方法，再加载复用仍有效的原正文回执；手选方法只能由用户移除。旧引擎仍沿用正文 128 KiB、每任务最多 32 条激活记录的旧配额。已读资料片段累计 32 KiB，同片段重复读取不重复计入。缺少任务必需能力时保留固定身份并显示原因，不装入方法正文。方法及资料不能触发脚本、表达式、链接或未获准工具。

输入请求省略 `skills` 表示保留原选择；显式 `skills:[]` 表示清空。前端草稿保存这一区别，刷新后仍能发送空选择。原任务仍选用的精确冻结版本可与新增方法一同发送，即使该旧版已更新、停用或从个人目录删除；新选择仍按当前目录、个人设置与版本校验，已明确移除的旧版不能借历史绕过停用。待应用选择同样参与判断，切换任务不继承未发送的方法变更。受理不明的重试使用原请求体及原幂等键。

轻量选择器由模型旁 Skill 图标打开锚定列表，搜索全部内置／个人方法，每次加载 50 项并支持加载更多，多选直接生效，最多 8 项，无选择弹框。展示不可用原因与当前手动冻结版本；旧版与目录新版分别呈现，点“改用 vN”才升级。输入 `/` 的建议项携带真实 ID／版本，为同名项展示来源和身份；Enter 只确认选择并在输入框内显示可移除的高亮块，“浏览全部 Skill”打开同一列表。`#` 是普通文字，纯文本名称不构成身份绑定。维护入口统一到 `/skills`，只接受导入与只读浏览，选择列表不承载管理表单。自由文本的广义语义消歧仍待实现与真实模型验收。

自动激活须先在本任务成功搜索过该版本。目录更新导致版本过期、停用或删除后首次激活会失败；Skill 不再保存自动／手动触发偏好；已激活的任务仍可读取原指令和原资料，模型不能通过重新搜索自动切换到新版本。用户明确重新选择的新版本在工具边界应用。停止／恢复、Worker 接管及历史压缩从持久激活重新装配，事务外资源读取完成后重新验证执行代次、租约、任务和 manifest，旧 Worker 的结果不得写回。

任务和操作快照增加 `skill_activations`：每项有 `activation_id/skill_id/name/content_version/package_checksum/source/purpose/active/plan_revision/created_at/issue`。`source` 为 `manual/auto`；`issue` 非空表示当前缺少能力。任务包含激活历史；操作只记录领取时实际可用的方法版本，后来的改向不改写旧步骤。字段不含正文、资料内容或存储位置；正文仅在按权限读取的工具结果中返回。`step.started/step.finished` 携带同名方法摘要，ThoughtChain 展示来源、固定版本、用途、资料路径和参与步骤。

## 助手媒体总开关

AI 配置的“创作助手”标签保存一个账号级 `media_generation_enabled`，默认开启。没有偏好行时 GET 返回 `true` 和 `policy_version:"0"`，不因读取补建记录；首次保存版本为 `"1"`。PATCH 要求严格布尔值与当前十进制策略版本，版本不符返回 409；相同版本保存相同值不推进版本。迁移不重置已保存的关闭设置。此开关不授予任务范围、作品读取或媒体生成授权，也不限制文本讨论、提示词或结构工具。

返回字段为 `media_generation_enabled/policy_version/configured_media/available_media`。`configured_media` 只表示本人对应类型有未删除且启用的模型配置，不包含端点、凭据或供应商参数；开关开启时 `available_media` 返回这些已接通的类型，关闭时为空。具体执行仍由目标模式、真实用户指令和模型能力限定：标准模式开放素材／分镜图片、普通／原生有声分镜视频、角色音色及台词配音，画布开放图片、视频和音频节点。

`get_generation_policy` 是无参数只读工具；Agent 不能修改设置。每次决策准备读取实时策略，最终模型发送前再次核对版本；变化使未发送的准备输入作废重装，不增加决策调用次数。媒体在本地受理、Worker 领取与最终供应商提交前核验实时开关及本人权限；尚未外发的项返回 `assistant_media_disabled` 并释放预占。已经外发的任务继续查询、保存及保护性回填，关闭开关不伪记为取消。待停止／改向／删除不撤销当前完整工具的生成与回填许可。

设置页面显式保存，409 保留本次选择并要求读取最新状态后核对；保存结果不明同样先读状态，不自动重提或覆盖。版本始终保留字符串精度。

## 标准图片批次生成

`submit_generation_batch` 是模型工具，授权目录为 `generation_requests`。当前接受“为已选素材生成图片：要求”“请给已选分镜各生成两张图片”“为本任务素材生成4张图”“为分镜 ID 123生成图片”等有界直接表达。缺省每目标一张；疑问、引文、否定、只搭建、模糊全量范围及超过四张不构成授权。每批最多 16 个唯一目标，每目标 1–4 张，必须一次包含原指令全部明确目标。任务目标和主动主体不互相扩权。

参数示例：

```json
{
  "request_message_id": "123",
  "request_hash": "0000000000000000000000000000000000000000000000000000000000000000",
  "retry_operation_id": null,
  "requests": [{
    "target_kind": "asset",
    "target_id": "456",
    "row_version": "1",
    "model_config_id": "789",
    "model_version": "1",
    "prompt": "雨中的人物",
    "count": 1,
    "aspect": null,
    "resolution": "2K",
    "reference_media_ids": []
  }]
}
```

`request_hash` 必须来自目录中的真实原消息摘要。执行端核对已应用输入水位、最新撤回、原指令和当前主体交集、本任务真实成果、目标／本人图片模型版本；分镜图片参数须与已保存设置一致。模型不能传 owner、URL 或内部生成任务身份。参考图可显式指定仍选入的明确图片主体，或本逻辑任务已完整结算回执中的真实图片 `media_id`。后者同时核对原生 `MediaAsset` 的任务、输出序号和本人持久媒体，跨任务、其他账号、未结算或伪造回执不授予使用权限。保存参考图和未选入关联素材不会自动进入供应商输入。

标准目标的 `describe_creation_target` 返回本人启用图片模型的 `image_models` 和视频模型的 `video_models`，仅含真实 `id/name/model_key/row_version`；两类分别按 `model_offset/model_limit`（默认 10、最多 20）分页，续页分别为 `next_model_offset/next_video_model_offset`。不包含端点和凭据，工具的 `model_version` 必须匹配该版本，执行时再次核对配置；目录存在不代表供应商已通过真实验收。

全批预检通过后先预占总数量，再逐项本地受理。`media_usage.images` 包含 `reserved/submitted/succeeded`：已外发的新尝试计入 submitted，明确未外发才释放，成功图位计入 succeeded。上限不足进入 `paused_budget`，不部分受理；本地单项受理失败释放该项并保留其他项。受理和私人 `agent_media_requests` 账本同事务提交，公共生成记录不携带私人操作关联。

已受理工具进入 `waiting_media`，scheduler 根据真实原生任务与图片组结算，不占用 Agent Worker 等待，也不重复调用模型。主输出按稳定序号选首个有效结果；第一张未明确失败前不因第二张先回调而切换。有效主图自动采用，其他结果和旧主图留历史。目标、所有者、版本、当前组或主图发生冲突时保留成功图片且暂停，不能覆盖人工修改。全部项结算后才完成工具并应用停止／改向／删除；结果卡显示真实图片组、已生成／请求数量和来源入口。

`get_generation_status {operation_id}` 只读取本任务原批次及其真实生成 ID、图位、预占和错误；重复调用返回原回执，不换键提交。部分失败先暂停；明确继续后，目录提供 `retry_operation_id/retry_targets`，新工具只能精确补齐已知缺失数量，并再次检查当前范围、版本、开关及预算。已保存结果不重新生成，新尝试计入用量，原失败回执不改写。供应商受理不明进入 `reconciling`，保留原身份和预占；没有可验证回执或查询能力时保持待核实，不以超时释放或盲目重发。尚未提供通用的人工核实供应商结果接口。

## 标准分镜视频批次生成

`submit_generation_batch` 接受“为已选分镜生成一段视频”“为本任务分镜生成视频”“为分镜 ID 123 生成视频”等有界指令。每目标一段，整批须包含原指令全部目标。每项使用 `target_kind:"shot_video"`、`target_id/row_version`、`media_kind:"video"`、`model_config_id/model_version`、`prompt`、`count:1` 和恰好一个 `reference_media_ids`。参考必须是当前分镜主图，并且已主动选入或来自本任务完整结算的真实图片；仅选入分镜不自动授权关联图片。

沿用已保存视频时长、分辨率及原生参考图能力校验，不接受工具暗中修改设置。只有明确工具提示词和获准参考图进入供应商输入；旧分镜正文、私人提示词和未选入角色不会自动拼接。普通视频指令在原生有声模式下拒绝受理；有声生成使用下节的明确指令和来源契约，也不自动切换作品声音模式。

`media_usage.videos` 沿用整批预占、已外发计数和明确继续后缺失项新尝试规则。Worker 领取及最终提交前核对本人、作品、分镜版本、当前主图及布局、视频上下文和当前视频；失效返回 `assistant_shot_video_source_changed`，尚未外发才释放预占。助手视频使用 FFprobe 检查真实轨道、尺寸和时长，永久归档后由 scheduler 在原生采用事务中再次核对并自动采用，替换前视频留历史。人工编辑、删除、替换或版本不足时保留产物并返回冲突。受理不明保留原请求和预占，不盲目重发。

工具等待归档与采用结算后才应用停止／改向／删除；回执包含真实原生输出、采用状态和目标版本，成果卡显示“已自动采用”、段数及私人任务／素材保留说明。有效来源链接使用 `source_media=video` 打开真实分镜的视频面板，失效来源不提供链接。没有新表或数据库迁移。

## 标准原生有声视频

“为已选分镜生成一段有声视频”“为本任务分镜生成原生有声视频”等明确指令沿用 `target_kind:"shot_video"`，增加十进制字符串 `native_dialogue_version` 与 `native_voice_versions:[{character_id,row_version,voice_version}]`。目录 `generation_requests.targets` 返回可信版本，最多两位说话角色，不接受模型自报台词、供应商音色 ID 或音频 URL。普通视频指令不能使用这些字段附带有声来源；有声指令缺少版本同样拒绝。

作品须已明确保存为原生模式，且 `NATIVE_VIDEO_ENABLED/AUDIO_PRODUCTION_ENABLED` 均开启。对白来自仍选入分镜的冻结快照或本任务真实保存成果，须已确认；每个说话角色及音色须主动选入或来自本任务真实创建／采用的可信来源。选入分镜不授权未选角色。受理核对素材、对白和独立音色版本以及音色记录／媒体身份；旧回执不会降低主动刷新的可信版本，猜中人工新版本也不能覆盖旧授权。

仅本次工具提示词、获准主图、已确认对白和对应样音进入生成请求，复用原生对白提示和样音 checksum 读取。Worker 领取及最终外发前复核声音模式、对白、全部角色／音色、主图和视频来源；已外发后变化继续归档，采用时保护人工内容。归档保留真实视频／音轨技术检查，对白镜头缺音轨或静音时保留候选并拒绝自动采用；已确认的无对白镜头允许无音轨。音色还原、表演和台词准确性仍需人工与真实供应商验收。

视频数量、稳定结果、旧视频历史、原身份核实、缺失项继续和完整工具控制边界与普通视频一致；成果卡及来源链接复用分镜视频入口。没有新增 HTTP 写接口、表或 DDL。

## 标准声音台词配音

任务已有或通过 API 明确选入声音草稿内已保存的 `sound_line` 后，可发送“为已选台词生成配音”或“为台词 ID line_a-9生成配音”。前端原“管理任务引用”目录已移除。`id` 是真实台词字符串（1–64 个英文字母、数字、下划线或连字符），不是数据库整数或分镜 ID；`revision` 是声音文档版本。主体目录仅提供角色与条目标识，正文、音色、已保存模型及开始时间由 `read_selected_subjects` 按需读取，不装入其他台词、字幕、声音轨或成片文档。选入分镜不授予旧声音文档权限。

媒体工具使用 `target_kind:"dialogue_audio"`、`target_id:line_id`、`media_kind:"audio"`，并传 `generation_requests.targets` 的 `row_version/assembly_version/timeline_hash`、本人模型目录的 `model_config_id/model_version`。每条固定一个结果，计入音频上限；不接受 `prompt/text/voice`、媒体地址或参考图。只能使用该台词已保存的模型、正文与音色，不由模型临时改写生成来源。原生有声视频模式不接受独立配音。

受理、Worker 领取和最终外发前复核作品归属、所选台词、声音文档、画面时间线和 `AUDIO_PRODUCTION_ENABLED`。同批及本任务已提交的真实配音采用可沿连续版本推进；人工编辑或版本断点不能冒充任务成果。已经外发的请求按原身份归档，scheduler 在同一事务内采用、更新真实声音文档及 v2 对白轨、保留历史媒体引用并写操作回执。既有片段保留稳定 UUID、音量及关联规则，不用旧文档覆盖多轨数据；未选条目与字幕保持原内容。采用冲突保留候选，受理不明保留请求身份和数量预占，不换键补发。

停止、改向和删除等待完整生成及采用。成果引用含 `kind:"sound_line"`、真实条目标识、`generation_id/media_id` 和声音版本；前端显示“已自动采用”或真实冲突状态，`/projects/{project_id}/assembly?source_line={line_id}` 打开原生声音面板并选中保存台词，失效来源不猜测其他条目。本文能力没有新增表或 DDL；新增主体 HTTP 契约须由同版 API 与前端使用。隔离数据库、真实 FFmpeg／ffprobe、Gateway／存储替身和浏览器夹具验证分别记录，不代表真实配音供应商或真人试听验收。

## 标准角色音色批次生成

同一工具接受“为已选角色生成一个音色：要求”“给本任务角色生成音色”“为角色 ID 123 生成音色”等有界指令，仅针对真实角色，每目标一个音色。角色须主动选入或为本任务真实新建／已采用音色的成果；场景、道具和仅选入图片的主体不扩大角色授权。`describe_creation_target.audio_models` 提供本人启用的音频模型及版本，使用 `next_audio_model_offset` 分页。

每项使用 `target_kind:"character_voice"`、`target_id/row_version`、独立的 `voice_version`、`media_kind:"audio"`、`model_config_id/model_version`、`prompt`、`preview_text`、`count:1` 和空 `reference_media_ids`。`prompt` 为最多 500 字符的声音描述，试音文本为 15–200 字符；均不可全空白，不接受调用方指定音色 ID、URL 或媒体参考。音色版本使用十进制字符串，无当前音色为 `"0"`；与素材版本分别校验，不推进素材正文版本。主动角色快照只投影音色版本和媒体身份，内部另冻结完整音色绑定；缺少可信音色版本的旧引用须主动刷新，模型猜中当前版本也不能覆盖人工变化。

受理、Worker 领取与最终外发前复核本人、角色／音色版本、模型版本及 `NATIVE_VIDEO_ENABLED/AUDIO_PRODUCTION_ENABLED`；供应商输入只含本次声音描述和试音文本，不附带旧角色正文。计入 `media_usage.audio`，沿用整批预占、原身份核实和明确继续仅补缺失项的规则。原生归档后复用角色音色采用，校验真实音色 ID、3–7.5 秒且不超过 10 MiB 的样音及校验值，自动采用与助手结算同事务保存，旧样音保留历史。角色／音色变化或采用失败保留产物并返回冲突；媒体总开关关闭不阻止已外发请求结算，原生部署开关关闭仍可能阻止采用。停止／改向／删除等待生成和采用完整结束。

成果引用含真实 `generation_id/media_id`、`media_role:"character_voice"`、素材版本和独立音色版本。前端显示“已自动采用”、音色数量和“试听生成音色”，通过既有原生候选接口同时匹配原生成任务与媒体身份，不能播放后来替换的当前音色；局部弹窗提供读取、错误、重试和关闭操作。失效来源不提供定位或试听。此能力没有新增 HTTP 写契约、表或 DDL。

## 画布媒体批次生成

同一 `submit_generation_batch` 工具接受“为已选图片节点生成两张图片”“为本任务视频节点生成视频”“为已选音频节点生成一段音频”等有界指令。节点引用来自当前仍选入的主体或本任务真实新建节点，不能借写入目标读取整图或邻居正文。每批最多 16 个明确目标；图片每目标 1–4 张，视频和音频每目标一段，缺省一个结果。

每项使用 `target_kind:"node"`、字符串 `target_id`、`media_kind`、`row_version`、`private_revision`、`model_config_id/model_version`、`count`、`prompt` 及可选 `reference_media_ids`。共享与私人版本分别核对，模型版本来自 `describe_creation_target.models`。沿用节点已保存的白名单设置；节点已有模型选择时不能暗中换模型。参考图必须明确选入或来自本任务完整结算的真实产物，音频不接受图片参考；不自动拼接邻居、原节点私人提示词或聊天正文。

生成参考不会使图片正文或原节点私人输入变为可读。受理时冻结真实来源身份，Worker 领取及最终供应商提交前再核对：来源节点／卡片删除、画布／作品归档或失权返回 `assistant_generation_reference_changed`，尚未外发才释放数量预占；已经外发继续保存原产物，不换身份重发。

准入、原生任务关联及助手账本同事务保存；原生归档、私人素材交付和稳定序号主输出回填全部完成后才结束工具。节点编辑、删除、任务替换或版本不足时保留成功产物并返回冲突，不重建节点、不覆盖新输入。`result_refs` 返回真实节点和画布键、共享／私人版本、媒体类型、数量及原任务 ID；来源失效显示 `source_unavailable`，不提供定位链接。原生任务不开放旁路重试，失败后的缺失项处理沿用原助手批次、明确继续和数量上限。

## 标准结构批次工具

`apply_standard_patch` 是模型工具，没有额外 HTTP 批次写入口。工具参数为 `request_message_id/request_hash/patch`；授权来自本任务已应用的真实用户原文，选择主体或指定写入目标本身不授权。当前支持有界直接表达，例如“创建一个角色和两个分镜并关联：要求”“修改已选素材的提示词：要求”“删除素材“名称””“删除分镜 ID 123”“调整已选分镜顺序：要求”“设置已选分镜的素材关联”“编辑已选分镜的对白：要求”“创建两条台词：要求”“修改已选台词的音色：要求”“编辑本任务台词的正文：要求”。没有数量的创建默认一个；疑问、引用、否定、全量范围或未识别表达先澄清，不能称为任意自然语言授权解析。

声音文本使用 `create_sound_line`／`update_sound_line`，与原生分镜的 `edit_dialogue` 分开。创建传 `key/values`，真实字符串 ID 由服务端产生；局部修改传 `line/patch`，批内可引用先前创建的 `$key`。允许字段仅 `text/character/voice/config_id`；不能传整份声音文档、媒体地址、`media_id/adopted_hash`、字幕、时间或音轨。明确字段指令不能附带其他修改。

声音动作须传该原指令 `standard_write_requests.sound_revision` 中的 `row_version/assembly_version/timeline_hash`。创建时服务端已在用户输入中冻结声音／画面版本，支持声音尚未创建的 `row_version:"0"`；没有成片草稿时先保存原生草稿再发送创建指令。已有台词须在原指令与当前选入主体的交集，或为本逻辑任务真实新建成果；后续本任务保存／采用只能沿连续真实版本推进，不能猜中人工新版本绕过保护。

适配层合并获准字段后复用原生声音保存，保留原文档版本、字幕、时间、音轨、已有媒体与 staleness 规则。声音保存及同批素材／分镜效果与持久回执原子提交，晚期失败全部回滚；原生视频模式拒绝独立配音台词。来源保存不生成配音；明确要求“为本任务台词生成配音”时，媒体工具才使用已保存正文、音色及模型。`read_task_results` 按真实回执读取本任务新建台词的保存快照，后来的人工内容只标记变化，未选台词与字幕不回传。成果引用提供真实 `sound_line`、声音／画面版本和原生来源定位。

每批最多 50 个动作、合计 1 MiB；`$hero` 等临时引用只能指向更早创建的正确类型。支持素材／分镜创建与允许字段修改、关联替换、局部排序、明确范围软删除和真实原生对白文本。分镜动作必带 `storyboard_version`，对象修改另带 `row_version`，对白还带可为 `"0"` 的 `dialogue_version`。创建累计数量按原用户指令扣减；相同语义参数的重复工具调用复用原操作，不重复创建或消耗数量。

已有对象必须同时在指令当时和当前的主动主体范围内，或属于本任务真实新建成果；名称／ID 指令须唯一匹配。其他作品主体、只选图片组／图位、旧回执和模型自报 ID 不扩大标准对象权限。字段授权及可信快照／本任务写入版本由执行端复核，模型猜到更高版本也不能覆盖后来的人工编辑。替换关联还校验将移除的素材；删除仍被分镜引用的素材返回 `assistant_delete_impact` 和 `details.shot_ids`，不会自动级联。删除分镜的必要排序压紧记入内部差异，不把未选分镜正文或名称回传给模型，也不授予后续修改权限。

业务效果、不可变角色历史、内部前后差异和操作成功回执在同一事务提交；任一动作失败整批回滚并保存错误回执。完整内部差异只用于账本和后续撤销，写工具仅返回结果引用、版本及错误；`/operations/{id}` 返回该操作的允许结果，读取工具操作可以包含其已读取正文。前端成果卡显示名称、类型、创建／修改／删除状态、保存版本、批次和真实来源入口；已删除对象保留回执且不提供失效定位链接。

授权目录返回 `allowed_assets/allowed_shots`（真实 ID 与可信 `row_version`）及 `create_remaining`（按原指令累计剩余数量），不含正文。`read_task_results` 按 `operation_id` 读取本任务真实新建素材／分镜在该次操作后的保存快照，`ids` 可筛选最多 50 项；`character_offset/character_limit` 及 `next` 支持分段，单次最多 6000 字符。只返回允许字段，不暴露完整内部差异；已选旧对象仍使用 `read_selected_subjects`。人工后续编辑只提示 `source_changed/current_revision`，不自动读取新正文；删除、归档或失权拒绝读取。发送模型前再次复核成果可用性，失效使旧准备输入作废并清除相关模型历史；新任务未重新选入时，旧回复及摘要不能回流成果正文。

对白适配 `ShotDialogue`，保留最多 20 行、最多两位说话角色、同项目角色且关联当前分镜等原生规则；必须同时启用 `NATIVE_VIDEO_ENABLED` 与 `AUDIO_PRODUCTION_ENABLED`。选入分镜的快照包含已保存对白文档和版本；修改推进对白、分镜及列表版本，不改变旧声音／多轨文档，不发起音频任务或自动开启功能。新文档默认未确认，`reviewed` 沿用原生文档契约。

## 画布增量工具

`apply_canvas_patch` 同样使用 `request_message_id/request_hash/patch`，仅在本任务已应用的明确画布指令下开放。当前支持“创建一个文本节点和一个图片节点并连线：要求”“搭建文本节点、图片节点、视频节点和音频节点”“修改已选节点的提示词：要求”“移动已选节点：要求”“删除节点“名称””“连接已选节点”“断开本任务节点”等有界表达；不将引文、疑问、否定或无法确定数量的范围当成授权。`canvas_write_requests` 返回动作、字段、类型数量上限、累计 `create_remaining` 和 `allowed_nodes`，后者包含真实 ID、`row_version/private_revision`，不包含私人提示词或其他正文。

`patch` 必带 `expected_revision` 和 1–100 个动作，最多 50 个新节点、合计 1 MiB。动作仅为 `create_node/update_node/move_node/delete_node/connect_nodes/disconnect_nodes`；`$key` 只能指向本批更早创建的节点。创建只支持 text/image/video/audio，媒体节点为空的原生 generator，只保存结构及提示词，不生成媒体。修改只开放标题、文本正文、私人提示词、本人启用的对应类型模型和闭合设置；不接收整图、任意 metadata、结果 URL、原生端口或复合 relation。

`describe_creation_target` 返回实际 `canvas_revision`、版本 1 的四类原生节点尺寸，以及本人模型的 ID／名称、媒体参考上限和有界输入配置目录。模型目录支持 `model_offset/model_limit`（默认 10、最多 20），用 `next_model_offset` 继续；不包含端点或凭据。连线按目标节点实际选择的模型校验，不能取模型库最大容量；拒绝自连、生成依赖环、图片接入音视频、音频接入普通媒体或多个角色卡等不兼容输入。已有复合连线需要在原生画布中明确处理。

既有节点须同时处于原指令当时与当前的主动选择范围，或为本任务真实新建成果。写入独立核对共享版本与私人版本；猜中后来的私人版本也不能越过冻结授权。服务端只在快照内部记录私人版本，普通主体读取仍不读取私人提示词。私人编辑显示来源变化；升级前没有可信私人版本的旧引用需主动刷新。删除保护未获准端点、子节点、其他节点元数据中的引用、导演场景和时间线；`assistant_delete_impact` 的详情仅含必要 `node_ids/areas`，不会自动清理这些关联。

增量在锁内合并并复用原生保存、私人状态和历史；失败连同内部差异一起回滚。重复原参数返回原回执。新增节点按依赖确定性分层布局，保留所有原节点的位置／大小及当前视口。内部账本记录删除产生的机械排序变化，不能借此读取或修改其他节点。

`read_task_results` 也支持成功画布创建操作：`ids` 保留节点原字符串，只能读取本批实际新建节点在首次保存后的白名单快照，不读取旧节点、私人对话、任意 metadata 或后续人工正文。后续移动／修改回执不作为私人正文读取来源，仍需引用原创建批次；避免从必要的撤销快照读入后来私人编辑。结果另含 `private_revision/current_private_revision`；编辑只提示版本变化，删除、画布归档或失权拒绝读取。成果来源同样受历史和发送前可用性屏障保护。前端节点成果卡显示保存／参数版本、真实定位及批次，已删除项无链接。此链路不改变原生画布生成回填或提议确认边界。

## 图片组目录与主图工具

`read_image_group` 使用 `image_sources` 中的 `subject_key`，或 `image_results` 中的 `operation_id/group_id`；两种来源互斥，本任务成果读取必须指定真实图片组。支持 `view:current/detail/history`、可选 `group_id`、`offset/limit`（最多 10 组）及 `character_offset/character_limit`（最多 6000 字符）。返回冻结目录的 JSON `content`、`range/total`、`source_changed` 和可继续传入的 `next`。目录只含真实组／图位／历史媒体身份、版本、状态与主图，不返回私人提示词、供应商任务或签名地址，也不表示模型已经读取这些图片内容。

本任务生成目录在结算时以原生生成资产记录固定产物身份，不能把后来人工替换的图计作本次生成。读取保留原保存身份、提示后续变化，不刷新未选主体正文；其他任务无法引用该回执。作品、目标、组、媒体与发送前来源再次校验，删除／归档／失权阻止新增读取并作废旧准备输入；正常移除原参考主体不移除本任务已经生成的成果。

`read_image_content` 按需发送一张真实图片，仍是模型工具，没有新增 HTTP 写入口。选入主体使用 `subject_key/group_id/item_id/media_id`，其中媒体须为该图位冻结目录中的当前或历史身份；仅选一个图位不能读取同组其他图位。标准本任务结果可用 `operation_id/group_id/item_id/media_id`；画布本任务结果使用 `operation_id/media_id`，身份来自 `image_results` 和完整结算回执。两类来源互斥，不接受 URL 或模型自报的存储位置。画布产物复核原生任务、资产、输出序号和本人媒体，不能从节点后来的私人参数推导图片。

工具回执含 `image_read_id`、来源、媒体身份、`source_changed` 和 `delivery:next_model_input`，仅表示已安排下一段输入；实际图片在事务外从受管存储读取，校验 SHA-256，按现有附件规则转为最长边 1280 的 JPEG，再以内联内容连同工具结果发送。公开回执不含存储位置或图片字节。每次执行最多保留八项冻结读取来源，与附件合计最多 16 项，处理后媒体合计最多 4 MiB；已有媒体预算和模型输入校验继续适用。

装配下一段输入及最终外发前均复核读取身份。主体移除、重新选入／刷新为其他快照、删除、归档、失权或文件身份变化时，作废旧输入并清除包含旧像素的模型续接历史；不会自动改读新图。未选主体不会因为猜中媒体 ID 获得读取权，本任务独立生成的真实成果不依赖原参考主体是否仍被选入。两种 OpenAI 协议的工具后内联图片及离线重放已有本地协议测试，实际多模态供应商理解质量仍需独立验收。前端轨迹显示“读取指定图片”，可查看该次媒体身份回执。

选入素材／分镜时在任务快照内部冻结其图片组目录；选入一个组仅包含该组，单图位仅包含本图位，不扩大到相邻图位或其他历史组。内部目录不出现在主体元数据和自动模型指令中，仍计入 1 MiB 总快照上限。选入后新建的组和人工修改不自动替换目录，读取时只提示变化；主动刷新引用才能取得新目录。升级前没有目录的引用要求主动刷新，读取不隐式补建 legacy 组。移除、删除、归档及失权阻止新增读取。

`set_image_group_primary` 只按 `image_primary_requests` 中的真实原消息 ID、hash 和唯一 `choice` 执行，参数为 `request_message_id/request_hash/subject_key/group_id/item_id` 及 `expected_primary_version/expected_item_version/expected_target_version`。当前支持“把第二张设为主图”“把已选图片组的第二张设为主图”“将已选图位设为主图”“把已选素材的第一张设为主图”“把图片组 ID 123 的第二张设为主图”等有界直接表达；多个可能目标、未就绪图片、未选入主体、疑问、否定与引文先澄清。素材／分镜默认使用冻结目录中的当前组，明确指定已选历史组可以恢复该组；单图位不能授权其他图位。

执行端重新校验原指令、当前相同写入目标、原指令与当前选入范围交集、可信的三类版本、实际媒体身份和当前组状态；模型自报更新版本不能覆盖人工编辑。复用 `ImageGroupService.primary_locked` 的真实采用规则：素材确认并追加角色不可变版本，分镜保留替换前媒体；恢复历史组时停用旧当前组。全部业务效果、内部差异和回执同事务提交，晚期失败回滚。一次真实指令只产生一次主图效果，重复调用复用原回执，选择其他图片需要新的明确指令。已领取工具完整结算后再应用停止；轨迹卡显示“已设为主图”、保存／主图／图位版本和真实来源定位。这些工具不提交媒体生成，不代表自动生成采用链路已经完成。

## 发送、参考来源和执行范围

```json
{
  "content": "分析林晚的动机",
  "model_config_id": "123",
  "new_task": true,
  "subject_selection": {
    "expected_input_revision": "0",
    "retain_keys": [],
    "subjects": [{
      "project_id": "456",
      "source_kind": "project",
      "source_id": "456",
      "kind": "asset",
      "id": "789",
      "revision": "3"
    }]
  },
  "execution_target": null,
  "memory_project_ids": [],
  "media_limits": {"images": null, "videos": null, "audio": null},
  "attachment_ids": [],
  "skills": [],
  "video_audio": "include"
}
```

已有任务的补充可使用 `task_id`；不能同时设置 `task_id` 和 `new_task=true`。项目 V3 的新任务在幂等回执查找之后补入所属项目的 `execution_target/memory_project_ids`，默认值不改变原请求哈希；创作模式可省略目标，画布模式新任务须指定真实当前画布。补充从任务及待应用输入恢复原目标，拒绝跨项目、跨模式、改绑原画布或指定其他项目记忆。旧 `project_id=NULL` 会话保持旧兼容语义。引用不自动授予写入，设置目标不读取其他正文，新任务不继承原任务主体，不自动载入小说、剧本或整图。

`subject_selection` 是本条输入希望保留的完整引用集合：`retain_keys` 仅保留本任务已存在的冻结快照；`subjects` 新选入或主动刷新指定主体；二者合计最多 16 项。省略整个字段沿用当前选择，空集合明确移除全部。`expected_input_revision` 必须等于当前任务输入水位（新任务为 `"0"`），并核对待应用输入，防止另一标签页的选择被静默覆盖；同幂等键重放先返回原回执。刷新不能同时 retain 同一主体。引用与其他补充同事务保存，在完整工具边界应用；停止时只保留输入，继续后才应用。

主体引用使用 `project_id/source_kind/source_id/kind/id/revision`。标准作品 source_id 必须等于项目 ID，支持 asset/shot/image_group/image_slot/sound_line；画布 source_id 是 source_key，仅支持 node。图位 ID 是真实 ImageGroupItem.id，不能用展示序号代替；同名节点在不同画布有不同 subject_key。可附当前 checksum；画布可附 content_revision，以正文版本校验而不因单纯移动位置拒绝。服务端记录实际版本、allowed_fields、checksum、snapshot_id 和完整允许字段，合计最多 1 MiB，超限明确拒绝，不静默截去正文。图片组不读取私人生成参数；组引用附主图，单图位引用只附该图位。画布图片结果按原生节点身份引用，不冒用标准图片组表。

任务目录返回 current/changed/unavailable。正文编辑或媒体变化只显示新版本，原快照保持；新增无关对象、节点布局变化不自动视为正文变化。前端在页面变化、重新聚焦或主动核对时只读这些元数据；用户主动刷新才提交新版本。删除／权限失效阻止新读取，发送模型前再次复核可用性，失效输入重新装配且不增加调用次数。助手历史回复保存当时的主体范围；移除后不借旧回复或摘要重新注入该主体正文。

目录的可空 `source_location` 提供已校验的真实归属：`project_id/kind/id`，kind 为 asset/shot/node/sound_line；节点附 `canvas_id`，标准图片组／图位附 `image_group_id/image_slot_id` 并定位其所属素材或分镜。前端“查看来源”按此身份打开真实对象；图片组／图位另行读取并核对归属，只读查看保存的图片，画布节点使用原生选择和居中，不修改业务内容。

`read_selected_subjects` 使用目录中的 subject_key（裸 ID 只兼容唯一匹配），支持 character_offset/character_limit，单次最多 6000 字符。content 是允许字段的 JSON 文本；range 给实际读取范围，next 给继续所需的 ids 与偏移，可无损回查长正文。未选择、同名歧义、已删除或无权限明确拒绝；不递归读取邻居或其他图位的提示词，不返回存储定位和凭据。

发送时使用 `subject_selection`，每个主体按自身版本校验；数据库 ID 不转数值，节点与画布键保留原字符串。服务端只读取已保存内容，不接受客户端正文或图文档；失效的新引用整条输入拒绝。当前页面选入／发送前等待来源编辑保存成功，409、未确认恢复及等待期间的新编辑保留草稿并阻止发送。

执行目标为 `{mode:'standard',project_id}` 或 `{mode:'infinite_canvas',project_id,canvas_id}`，仅声明任务目标，不自动授予任意业务操作。项目 V3 自动将 `memory_project_ids` 限定为所属项目；旧未绑定会话兼容原先最多 16 项的显式范围。`media_limits.images/videos/audio` 沿用默认规则，分别约束标准图片／普通及原生有声分镜视频／角色音色／旧声音台词配音及画布三类媒体。

前端任务设置和上下文管理入口及弹窗已移除，新任务使用当前项目／画布目标，补充不发送新目标；受理不明仍逐字重放原请求及原幂等键。引用通过 `@`、当前主体和画布选择添加，已有引用标签支持移除、主动刷新和来源定位，来源版本与任务输入水位校验保留。添加图标只列图片、文本、当前画布和资产库，创作模式禁用当前画布项；原视频／音频附件 API 为兼容保留。

文本最多 32000 字，附件最多 16 项、所选 Skill 最多 8 项。附件继续使用 `/conversations/{id}/attachments`（可筛选 pending）、`/uploads`、`/references` 和 `/{attachment_id}` DELETE；上传与引用必带幂等键。共享附件路径同时支持 V2/V3 会话，继续校验本人及项目归属；V0/V1 保持只读限制，旧聊天消息接口仍仅接受 V2。前端自动衔接当前任务，完成后开始新任务，不提供“独立新任务”下拉框。多模态大小、校验和及本人媒体访问沿用[Agent 上下文](agent.md)。本次未进行新增多模态真实供应商验收。

任务补充可传 `attachment_selection:{expected_input_revision:'4',retain_ids:['123']}`，仅保留此任务已有的数字附件 ID；省略沿用当前集合，显式空列表移除全部，新增附件仍使用 `attachment_ids`。水位须匹配并计入待应用选择，跨任务伪造保留拒绝。全部任务附件与主体媒体合计最多 16 项，文本附件合计最多 128 KiB，按保留后加新增的集合校验。变更在完整工具边界应用，停止后等待明确继续；移除只改变本任务引用，不删除原上传记录。发送模型前再次校验本人附件可用性，失效输入作废重装且不增加调用数。附件范围随历史回复保存，移除或失效后旧回复／摘要不能重新注入正文。受理不明保留原选择、水位、请求体及幂等键。

## 记忆管理与按需读取

当前前端不提供记忆管理、抽取澄清、维护状态和摘要详情入口，不展示内部记忆工具步骤；记忆、阶段摘要及来源保护继续在后台运行。以下路由为内部与兼容契约，不代表存在用户管理页面。

| 方法 | 路径 | 契约 |
| --- | --- | --- |
| GET / POST | `/memory` | GET 分页，支持 conversation_id/project_id/query/include_forgotten；POST 必带幂等键 |
| GET / PATCH | `/memory/{id}` | 当前条目；PATCH 必带 content_version，仅更新标题、正文、别名、标签和置顶 |
| DELETE | `/memory/{id}` | `{content_version}`，遗忘，立即停止自动使用 |
| POST | `/memory/{id}/restore` | `{content_version}`，用户明确重新记住 |
| GET | `/memory/{id}/revisions` | 最近 50 个不可变内容版本及变更来源 |
| GET | `/memory/conversations/{id}/context` | 当前有效摘要、覆盖位置、摘要代次与最近整理任务；不返回模型配置及原始供应商报文 |
| GET | `/memory/conversations/{id}/summaries/{summary_id}` | 查看当前会话的有效阶段摘要；包含 `base_summary_id` 及事实来源索引，已失效摘要返回 404 |
| GET | `/memory/conversations/{id}/reviews` | `offset/limit` 分页查看抽取冲突；当前事实、候选、来源、基准／当前版本与选择状态 |
| POST | `/memory/jobs/{id}/clarifications/{index}` | 必带幂等键，`{content_version,choice:'keep_current'|'use_candidate'}`，按当前版本和真实来源确认 |
| POST | `/memory/jobs/{id}/retry` | 必带幂等键，`{message_version}`；仅明确失败可创建独立后继作业，201 返回 `MemoryJobRead` |

创建字段为 `scope_type/kind/memory_key/title/content`，可带 aliases/tags/pinned。user 范围不带 conversation_id/project_id；conversation 只带 conversation_id；project 只带 project_id。可选 `source_conversation_id/source_message_ids` 保存真实消息来源，不能借来源扩大范围。kind 为 preference/correction/setting/decision/reference。别名和记忆键规范化；正文最多 12000 字。编辑、遗忘与恢复均按内容版本校验，409 保留草稿，不能自动覆盖。

管理列表始终含账号记忆，指定 conversation_id/project_id 后叠加相应范围。模型工具的范围更窄：只允许本人账号、当前会话及本任务明确绑定的作品。目录包含真实 ID 与版本，正文通过 read_memory 按需读取。现有工具为 `memory_catalog/search_memory/read_memory/read_conversation_history`，关键词查询使用精确键、别名、标签、MySQL FULLTEXT ngram 和有界短词兜底。单次正文及 Run 总读取量受限，返回分页或继续位置；调用失败回传明确工具结果，允许模型随后继续回答。

显式写工具为 `remember_memory/update_memory/forget_memory/restore_memory`，只在已应用用户输入含对应直接指令时开放。支持“记住：内容”“记住账号偏好：内容”“记住当前作品：内容”“修改记忆“标题”为：内容”“忘掉记忆“标题””“重新记住记忆“标题””；正文可有多行，一条输入不混合多个记忆命令。新记忆默认当前会话，作品记忆须只明确绑定一个作品。修改／遗忘／恢复按允许范围内的精确标题、键或 ID 匹配唯一条目；不明确、重名、引用示例、疑问或否定先澄清，不猜对象。

工具须引用本任务真实已应用用户消息的 ID 与原文 hash；正文和作用域由服务端解析，不能传自造正文或授权标志。修改等操作还校验真实内容版本及指令时间，旧指令不能覆盖后来的编辑。重复调用／执行尝试返回原操作效果，不追加重复版本；旧记住指令不能换标题绕过遗忘，恢复须新的明确指令。回执只给身份、范围、状态与版本，正文仍按需读取。已成功显式处理的来源不会再被后台抽取成另一条记忆。当前是有界直接指令通道，不能表述为支持任意自然语言消歧。

后台摘要／抽取使用同一 Agent 队列及独立持久作业。Run 终态同事务安排增量整理，来源 hash、连续处理水位、摘要代次及 CAS 保护迟到结果。网络调用在事务外；完整原始回执可离线重放，已发送无回执的受理不明任务不自动重发；明确限流最多三次。遗忘、纠正会失效相应摘要及在途模型输入，旧来源不能换标题重新生成事实。

同范围、类型和键的不同抽取内容保留为待澄清候选，不自动覆盖。选择候选会追加记忆版本和来源；来源已被纠正或遗忘时不显示候选正文，也不允许借澄清恢复。选择请求同键同体重放，旧版本或不同选择返回冲突。明确失败的整理记录可手动重试，保留原记录及回执且最多创建一个后继；`retry_job_id` 指向后继。受理不明／发送中、来源或模型配置已变化时禁止重试；丢响应后先刷新读取后继，不换键重复提交。

整理作业 `status` 支持 `pending/running/succeeded/failed/stale/cancelled/abandoned`。凭据／配置及明确鉴权失败直接 `failed`；明确限流，以及抽取任务的已持久化无效输出，最多三次发送。只有这类可重试抽取耗尽后成为 `abandoned`，允许抽取水位越过，不扩大手动重试入口（仍仅接受 `failed`）。一般失败及受理不明不推进水位，后者不自动重发；此处次数不限制创作 Task 的累计执行。

模型输入在冻结及最终发送前检查总预算，纳入任务、Skill、工具、历史和待回传工具结果；已完整表示的近期用户输入不重复装配。Harness 默认平台窗口为 65536 tokens，可使用有效的[模型容量声明](agent.md#模型选择与运行反馈)，平台上限 262144；预留输出（最多 8192，且不超过窗口八分之一）、工具结果 4096 和安全余量 2048，近期完整工具组配额随容量缩小。使用 JSON UTF-8 字节／3 及媒体估算，不是供应商 tokenizer；实际输入和缓存用量另存 Turn／Run usage，不将声明或估算当作供应商窗口实测。超限且有有效的任务约束整理在途时，保留原输入并等待作业；没有可推进作业或整理失败时进入 `paused_budget`，保留明确约束及未闭合工具配对，不发送请求、不增加调用数。

长逻辑任务另使用 `kind=task_state` 的后台作业整理已应用的旧输入前缀，近期原文与新纠正继续直接注入。软阈值为 16000 UTF-8 字节；每轮新来源不超过 48000 字节／128 条，尾部约 8000 字节且至少保留最后一条。有效约束放在任务 `effective_state.input_compaction`；模型接收 `task_constraints` 和近期 `inputs`，原始消息、任务目标及完整来源保留，仍可用 `read_conversation_history` 回查。事实最多24条、估算5000 tokens，必须覆盖新增未受限用户来源及全部父事实；旧来源通过固定父作业及事实索引追溯，不无限展开消息 ID。

约束整理在领取、外发、结算及注入前核对整个原始前缀的版本、内容哈希、应用及遗忘状态，并用父作业身份和覆盖修订做 CAS；新增补充不使旧前缀失效，纠正或遗忘会停止使用旧事实。网络调用不持数据库事务，完成整理只更新约束状态及行版本，不改变停止状态、控制序号、目标、主体、Skill 或工具权限。写入目录仍从真实原始用户指令生成，整理内容不能扩权。硬预算不裁剪工具调用／回执；SDK 历史中只将已压缩的原始任务提示替换为约束引用，保留其他用户内容、媒体及完整工具配对。失败重试沿用原来源及模型，`sending/unknown` 仍禁止重发。结构与来源校验不等于真实模型约束保真质量验收。

阶段摘要最多 24 条事实、估算 5000 tokens。新消息用 `source_message_ids` 引用，旧事实通过 `prior_fact_indexes` 和服务端固定的 `source_summary_id` 指向上一摘要；旧摘要与区间来源保留，模型只接收上一阶段的有界事实正文和索引。必须覆盖旧事实及新增已应用用户来源，不能把助手提议升级成用户事实或无回执的完成结果；主体限制随引用合并。管理详情可逐阶段回查；纠正／遗忘会使受影响摘要链失效。来源链有界不等于摘要语义质量、真实长期成本及召回效果已验收。

遗忘与删除原始消息是不同操作。普通遗忘保留来源及版本供明确恢复，不声称物理擦除原文。

## 删除与记忆保护

当前前端永久删除仅确认一次，提交 `{row_version,clear_derived_memory:false}`，不请求 `deletion-memory`，也不显示记忆选项。作品成果保留。历史已持久化的待核对删除继续使用原体、原键，不能为适配新界面改变旧请求。内部兼容的 GET `deletion-memory` 返回 `conversation_id/row_version/items/total/offset/limit`；每项含真实 id、content_version、作用域、标题、有界正文预览及 protection_reasons（user_edited/independent_sources），相应显式选择契约仍保留。

DELETE 请求示例：

```json
{
  "row_version": "7",
  "clear_derived_memory": true,
  "memory_selections": [{"id": "901", "content_version": "3"}]
}
```

memory_selections 最多 200 项、不能重复，非空选择要求 clear_derived_memory=true。仅清理明确选择且仍与本会话关联、版本匹配的账号／作品条目；预览后变化或无关条目返回 409，整个删除不生效。只传清理开关而不勾选条目，不会扩大为清理全部账号事实。本会话摘要和会话级记忆始终退出使用，遗忘规则阻止旧来源复活。

删除开始阻止新输入、任务领取和后台整理，等待当前操作及在途 Run 完整结算；Task 已停止而模型段仍在返回时，保持 deleting，不能提前投递正文清理。deleted 后停止在线读取与上下文使用，原删除请求仍可凭原键、原请求取得最小幂等回执。无回收站与产品恢复入口，作品成果保留。

收尾同事务投递 purge 作业，由 scheduler 在无需模型凭据／RabbitMQ 的本地路径清理消息正文、输入快照、模型报文、摘要与会话附件引用；即使 AGENT_ENABLED=false 仍可执行。仅由本会话持有、无任何已登记外键引用的上传媒体才进入物理清理；其他会话、作品、历史和任务引用保护媒体。存储失败保留定位值并重试。后来明确恢复／编辑的共享记忆再次受版本保护。备份淘汰与 MinIO 版本保留遵守部署策略，删除按钮不表示所有备份版本即时擦除。

浏览器按账号保存未确认的发送、控制、删除和记忆创建请求；刷新核对必须使用原请求体及原幂等键。损坏记录不能被新请求静默覆盖。

历史 V3 的 resume 在回执查重后核对停止水位，过期返回 `409 assistant_control_changed`。此错误明确表示本次恢复未受理，客户端清理对应待核对控制并刷新状态，不自动跨越其他标签页的新停止；普通 `conflict` 仍保留原请求供核对。任务终态不显示持续恢复连接的加载动画，会话事件监听继续保留。

## 旧接口与部署边界

旧 `/assistant/conversations`、`/resolve`、项目消息／运行及 `/runs/{id}/stop` 仍保留 V2 纯聊天兼容服务，不能声称这些服务端写入口已全部退役。V2 的 tools 与工具预算为空，仍按项目归属；V3 不接受 V2 的自动整份正文和归档字段。当前前端将 V0/V1/V2 列为历史只读，旧 `/agent` 的既有运行／候选继续遵守原权限与版本规则，不转换为新任务授权。

模型列表、本人配置、现有 Skill 管理仍复用 `/agent/models`、`/agent/skills`。V3 使用当前已接入工具清单；账号 Skill 包、按需加载和轻量选择已接入，Skill 不得扩大权限，广义语义消歧与真实模型效果仍待验收。模型 Gateway 当前支持 OpenAI Chat Completions 与 Responses；真实远端能力和计费验收必须独立记录。

升级须先执行[全局任务与记忆迁移](../database/migrations/2026-10-21-agent-system/README.md)，再执行[项目会话约束增量](../database/migrations/2026-10-22-project-assistant/README.md)，部署同版 API、scheduler 与 Agent Worker。应用不自动建表／迁移。历史真实供应商证据见[原验收记录](../reviews/Agent模式实施与真实模型验收.md)，不能替代本次系统的真实运行验收。

## 当前剧本引用与待审修改

> 2026-10-28：标准助手已移除“引用当前剧本”按钮、正文／选区绑定和草稿引用开关。新消息显式发送 `script_reference=null`，在输入应用时清除旧任务绑定；普通聊天无需等待正文保存。以下合同保留用于历史已受理任务、原键核对和已有待审建议，前端不再提供新建剧本引用入口。

普通 `/messages` 的可选 `script_reference` 为 `{project_id,script_id,content_version,start?,end?}`。仅标准模式且项目匹配时可用；`script_id=null` 只允许当前空稿。选区为 Unicode 码点半开区间，两个边界须同时提供。受理时验证当前身份和版本，冻结到 TaskInput；不把正文或临时 URL写入浏览器引用记录。运行中补充使用完整普通消息合同，保留主体、附件和 Skill，在下一边界应用，界面显示待应用水位。

`read_current_script(offset?,limit?)` 分段读取已应用输入绑定的当前剧本或选区，最多6000码点。`propose_script_patch(content_version,script_id,start,end,replacement)` 只建立 `pending_review` 建议，不能自动写正文；范围须在引用内，空稿允许 `[0,0)` 插入，最终正文最大1 MiB。采用前等待本机项目编辑保存，重新校验建议原文、版本、归属和写作互斥；成功原位更新正文、保存前后快照、回执及 `artifact.script.applied` 事件。`assistant_script_changed`（409）拒绝旧建议覆盖后来编辑；前端保留当前输入，提示在剧本正文中核对修改。

任务停止尚未生效返回 `assistant_task_stopping`；前一 Run 尚未结算返回 `assistant_task_settling`。这些明确未受理状态和已知 admission 422 可解除本机 pending 并保留输入；未知4xx、鉴权和普通冲突不能推定原消息未受理。请求已受理或仍不明确时继续使用冻结体与原键。需求 draft key 含真实消息身份，跨消息不会碰撞；明确缩减只接受更新、已应用的真实用户来源，模型不能自行缩水。

助手写入、主图、生成结算和撤销按操作回执刷新当前项目资产／分镜；干净表单更新，脏表单保留本机输入进入版本冲突。最小主体引用可随本机草稿恢复，恢复后先读取 metadata 核对身份、版本和 checksum；变化或失败时禁止静默发送，提供重试或明确移除。

## 当前附件与模型入口（2026-10-28）

仅接收当前 V3 会话；先校验账号与项目访问权，再校验会话。旧 scope 0–2 不转换为新任务许可。

| 方法 | 路径（均在 /api/v1 下） | 说明 |
| --- | --- | --- |
| GET | `/assistant/conversations/{id}/attachments` | 分页、pending 过滤 |
| POST | `/assistant/conversations/{id}/attachments/uploads` | multipart file，幂等上传 |
| POST | `/assistant/conversations/{id}/attachments/references` | 引用本人有权使用的媒体，幂等受理 |
| DELETE | `/assistant/conversations/{id}/attachments/{attachment_id}` | 204，已冻结输入不变 |
| GET | `/agent/models` | 本人可用文本模型及输入能力 |
| PATCH | `/agent/models/{id}/inputs` | 更新已声明输入能力与上下文容量 |
| POST | `/agent/models/{id}/verify` | 显式模型验证，可能计费；本轮重构未调用 |

## 模型选择与运行反馈

选择只检查本人配置、启用状态和类型，不要求 verified，也不自动调用供应商探测。工具和多模态能力未知时尝试用户的实际请求；系统不能编码的协议组合在运行中本地反馈。模型不匹配、认证失败、限流和受理不明分别处理，不静默丢弃附件或切换模型。文字回复不能代替真实候选执行证据。

`GET /agent/models` 的每项新增 `input_capabilities`：

```json
{
  "text": true,
  "image": true,
  "audio": false,
  "video": "sampled_frames",
  "evidence": "declared",
  "context_window_tokens": 131072,
  "context_window_evidence": "declared"
}
```

`video` 为 `sampled_frames` 或 `unsupported`；`evidence` 兼容 `declared/model_family/text_only`，增加 `runtime` 表示等待实际请求证据。未知 Chat 配置不再按模型名称判为仅文本。以下旧声明和 `/verify` 接口保留兼容，当前创作界面不提供手动验证或声明入口：

```json
{"row_version":"1","image":true,"audio":false,"context_window_tokens":131072}
```

模型行版本输出十进制字符串，输入兼容正整数与十进制字符串；前端始终保留字符串。声明绑定配置版本、地址、模型标识和凭据身份；更新声明会增加模型版本，并保留未变更的已验证工具协议证据。配置发生变化后，旧声明失效。声明本身不调用模型，也不代表真实供应商验证。

`PATCH /agent/models/{id}/inputs` 可选 `context_window_tokens` 为 16384–1048576 的整数。省略时保留仍有效的原容量声明，显式 `null` 清除；无有效声明使用平台默认 65536，平台有效上限 262144。返回的 `context_window_tokens` 是实际参与预算的值，`context_window_evidence` 为 `declared/platform_default`，与图像／音频的 `evidence` 分开。当前前端仅同步 DTO 与请求函数，不提供容量配置界面；声明不是供应商窗口探测或视觉语义验收。

当前 SDK 适配中，Chat 协议允许图片和 MP3/WAV 音频内联；Responses 协议允许图片，不能编码音频输入；视频由图片采样与可选音轨组合输入。协议或供应商明确拒绝所选组合时，运行在对话内给出安全中文反馈。用户决定更换模型、减少附件或对视频明确选择仅画面，系统不自动降级。
