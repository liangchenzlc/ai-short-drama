# AI 创作助手 Harness 运行机制

2026-10-26 起，新 V3 任务冻结 `langgraph-harness.1` 引擎；已有任务继续使用原引擎，不在恢复中切换协议。LangGraph 决定模型／工具节点次序，LangChain 每次只完成一个模型决策，function calling 由模型自主选择。服务端校验所有权、选入范围、输入版本、工具版本、模型能力、预算和执行租约，不再以用户句式正则作为新任务的工具授权条件。

## 目录与职责

| 位置（相对 `backend/src/short_drama/`） | 职责 |
| --- | --- |
| `agent/harness/` | 执行图、只含 ID 与阶段的图状态、MySQL saver、完成验收及局部进展监督 |
| `agent/gateway/langchain.py` | Chat Completions、Responses、Claude 单次决策、保护传输与回执重放 |
| `agent/resources/` | `ResourceRef`、`HydrationIntent`、资源目录与唯一实体水合边界 |
| `agent/context/assembly.py`、`transcript.py`、`working_set.py` | L1–L5 投影、有界完整工具组、增量进度及有来源工作笔记 |
| `agent/task/contract.py` | 用户来源交付要求、结构化完成检查及证据核验 |
| `agent/skill/progressive.py` | 小目录分页、不可变版本加载、去重、释放／复用及独立配额 |
| `agent/tools/registry.py`、`autonomous.py`、`scope.py` | v4 工具 schema、按需能力组、处理器、可信任务范围；旧冻结请求保留原版本 |
| `agent/coordinator/`、`task/`、`memory/` | 复用 Run／Turn／Operation、可靠调度、记忆水位与 CAS |
| `service/agent_graph_service.py`、`dao/agent_graph_dao.py`、`domain/agent_graph.py` | checkpoint 与 pending writes 的事务、查询与 ORM |
| `ai/prompts/assistant/` | 五段系统提示词、continuation 续执行提示与独立 verification 验收提示 |

## 执行与恢复

API 在任务及 Run 固定引擎，Agent Worker 按固定引擎派发。每次 broker 投递推进图到下一个持久化调度边界：模型节点完成一次决策，存在工具请求时进入工具节点；下一轮仍由现有 Celery 发布账本调度。数据库事务之外执行模型及 MinIO I/O，graph 使用同步持久化，避免后台 saver 与实体 I/O 争用连接。

一次投递只有一条租约看护任务，覆盖模型段与工具段：每 `max(5, lease/3)` 秒续期。续期失败只把租约标记为可疑并停止续期，**不取消已发出的模型请求**——回执保存本身不依赖租约（`save_raw` 只校验会话未删除与 Turn 未处于 prepared），已发请求必须有机会落回执；结果结算仍按 message_version 与租约令牌围栏，未通过围栏时结果保留待恢复。工具段没有在途请求时不必续期，`claim_operation` 在每次领取前重新校验租约。

租约在工具执行期间失效时，`complete_operation` 不再静默丢弃：同一投递内仍处 `running` 的操作被收敛为 `failed`（`agent_lease_lost_operation`，可重试），对应 ToolCall 同时失败并写 `step.requeued`。业务效果与回执同事务，因此未提交即无效果；已提交的操作不会进入该分支，重放不会重复写入。

图状态与 pending writes 只存 `run_id/task_id/turn_id/phase` 等轻数据。命名空间绑定账号、任务、Run 与投递版本；写入锁顺序为 Project → Conversation → Task → Run。幂等判断使用 MySQL 锁定读取，避免 REPEATABLE READ 旧快照漏读并发提交。图不保存完整 messages、私有 LangChain state 或工具 artifact。

生产恢复依据 Run、Turn、Operation 的冻结输入、原请求和原始回执重建执行；当前 Worker 不以 `graph.invoke(None)` 作为生产恢复入口。saver 支持 LangGraph 原生 pending writes 恢复并有独立数据库测试。原始 HTTP 回执已经保存时离线重放，禁止重复 POST；请求受理不明且无完整回执时停止自动重发。锁、租约、围栏及原生媒体请求账本继续生效。

新引擎不支持改向与恢复中断：`redirect` 返回 `assistant_redirect_unavailable`，`resume` 返回 `assistant_resume_unavailable`。停止（`stop`）在新任务上按边界生效：无在途段时立即结束，模型段进行中只置取消标记，由 Worker 在段结束后落已流出文本并关闭 Run，错误码为 `agent_stopped` 而不是权限失效。`waiting_generation` 期间停止要等媒体批次结算后才生效，已结算的媒体仍按原规则采用（如需撤回走既有批次撤销），任务随后停止。`TaskRead.interruptions` 分别给出 `stop/redirect/resume` 能力，旧字段 `interruptions_supported` 保留为 `stop` 的兼容投影。运行中补充输入仍在下一工具边界应用。纯讨论历史重答保留原位置之前的输入与历史，禁用工具，不加载后续 L4／L5 或未来工具轨迹。能力校验缓存绑定当前 LangChain framework 版本；旧引擎或旧框架的证据不再显示为已验证，模型选择仍不要求先执行付费能力探测。

任务停止或因错误、旧预算暂停（`stopped/paused_error/paused_budget`）后，发送普通消息（如“继续”）会在原 Task 下创建新的 Run 并调度，无需旧 control／resume API。沿用冻结模型，模型配置变更或活跃操作尚未结算时明确拒绝；重复消息键复用已受理结果。续执行恢复该任务以往 Run 的已结算工具组、读取记录及活动 Skill，不重新创建已有成果。撤销来源、方法换版及遗忘仍推进历史下界；前端刷新后仍把暂停任务作为发送目标。此行为不使用旧 resume 控制，停止仍按边界生效，立即改向不开放。

## 交付要求、完成验收与局部进展

复杂执行任务的要求分两层：服务端在发送消息时用规则（非模型）从用户原话抽取 draft，写入 `agent_task_requirements`（含真实 `quote_start/quote_end`、`origin=server_draft`、`state=draft`）；Agent 只能用 `confirm_requirements(keys, additions, intent)` 确认这些 draft 或补充有原文引用的要求，不能自行删除或降低数量。用户在更新的已应用消息中明确修订数量时，可建立新confirmed定义并将旧定义标记superseded。最多 32 项，`key/description/kind/source_message_id/minimum_count/artifact_kind/asset_kind` 与旧契约一致。来源必须是本任务已应用的真实用户消息；改述保留原表述。普通“继续”保留旧要求；修改约束须引用更新用户输入，遗忘来源退出使用。旧任务没有 `agent_policy.contract=server_draft.v1` 时继续使用旧 `set_task_requirements` 与 JSON 契约。

要求类型区分 `answer/read/write/image/skill/observation/contract`。`write` 只计算真实业务保存的分类成果；`observation` 核对当前工作集中的有来源观察，推断、同来源同引文的重复笔记及历史下界前的笔记不计数；`contract` 核对已登记要求。不能把登记或笔记当成作品写入。检查协议 v2 对已有要求只接收 `checks[].key` 及核验字段，由服务端取权威定义；新任务不再接受 `additional_requirements`（未登记 key 直接拒绝，避免被验收方定义标准）。旧 v1 检查回执按原结构解析和重放。

发布前有一道确定性守卫（不调用模型）：本 Run 有成功写操作但没有任何已确认的 `write` 要求时拒绝发布，回填 `completion_feedback`（`agent_requirements_unconfirmed`）；实际写入的成果类别未被已确认要求覆盖时，自动补一条 `server_derived` 要求后放行；纯讨论且无写操作时跳过。守卫以 `agent_policy.contract` 为开关，旧任务不经过它。

`describe_creation_target` 的成功目录结果属于读取证据，可核对目标身份、版本及可用能力；目录不能证明已读取正文、已看过像素或已保存业务成果，这些语义仍须分别核验。

`requirement_sources` 以独立约 4000 tokens 的字节估算配额保留用户原文，优先初始目标及最新输入，最多 32 条；每条附 message ID、seq 和截断标记。来源已遗忘时退出投影，超过配额的内容按原消息序号显式读取。登记和检查可以直接复制连续原文，避免把 L4 摘要改述误当引文；不改变 L1–L5 数据存储、摘要生成或水位。

模型给出候选最终答复时，先将该 Turn 标记为内部，不立即发布最终消息。新监督请求的流式文本暂存于执行器，工具旁白在结算时发布，候选回复通过检查后发布，避免未验收的完成声明先进入 SSE。下一独立 Turn 只开放 `submit_completion_review`。新任务（`agent_policy.verification=evidence.v3`）的验收 Turn **不继承执行器的工具轨迹**：服务端先按 `agent_task_requirements` 重建证据包（成功操作、成果引用、正文摘录、Turn 身份）并跑确定性检查（操作存在/成功/在范围内、类别与数量、版本一致），再把未通过或有歧义的要求、候选答复与证据缺口交给验收模型；验收模型优先选择本人**另一个文本模型**，没有时降级为同模型并在 `run.checkpoint.reviewer_degraded` 标记。程序仍核验真实用户引文、同任务成功操作、成果类别及去重数量、当前范围、答复实际引文，以及媒体已经交付给模型的事实。读取意图不能当作看过像素。缺项反馈原 Agent 继续；通过后发布原候选答复。新输入、记忆代次或历史下界变化会作废候选。验收使用原请求与原始回执账本，恢复不重复外发，也不创建业务 Operation。旧冻结请求没有监督版本时沿用原行为。

完成检查提供可观测证据约束，不能确定性证明任意自然语言意图和创作质量。`evidence.v3` 任务没有已确认要求且没有成功写操作时，普通讨论直接发布，不创建独立验收 Turn；已有要求的答复仍进入检查，不因此要求业务写入。有成功写操作但要求未确认时仍由发布守卫拒绝。旧冻结监督协议保持原检查行为。内部验收 Turn 计入实际模型用量，不作为可执行业务工具，也不进入近期业务工具轨迹。

`task_contract.reviewed_evidence` 为此前通过核验的执行项保留实际操作 ID、要求定义 hash 及来源 Turn。定义变化、来源遗忘或越过历史下界时退出投影。该记录供后续检查复用正确证据身份，仍按本次检查重新查询真实操作、数量和状态，不自动放行本次缺项。图片目录可辅助核对，但只有已交付像素的读取计入图片数量。

`record_task_observation(operation_id,kind,source_quote,note)` 保存观察或推断，须引用成功且当前可用的读取操作；文本检查真实原文短句，图片检查实际交付证据。笔记保存 Operation／Turn 身份和结果 checksum，最多 32 条、估算 3000 tokens，跨窗口压缩和“继续”保留，历史下界失效的笔记停止注入。这是任务工作集，不替代 L5 长期记忆。

文本引文必须是连续原文，不能拼接分散句子或省略中间内容；校验失败返回来源操作身份和最多 500 字符的实际正文片段，供 Agent 直接复制短句后修正。保存检查可以同时接收写入和回读证据，但保存数量只从真实写入去重计算，回读不能增加成果数量。可重做步骤顺序有误时，应重做相应流程并按最新完整执行核验，不重复创建已有正确成果。

局部监督按**滑动窗口**判定：每次工具结算写出 `result_digest` 与 `evidence_kind`，读取该 Task 当前已应用输入版本中最近至多两个窗口的成功、失败或冲突操作，排除编排类工具。操作不足 `window`（默认 24）条时不累计空转；完整窗口内没有首次出现的新成果（成功写入、新结果摘要、新观察、首次加载的方法）时记一次零成果。连续 `AGENT_PROGRESS_ZERO_WINDOWS`（默认 2）个完整滑动窗口零成果以 `agent_progress_stalled` 暂停。重复读取、重复已加载、空读与重复写入不计为新成果；未结算的媒体等待与登记/启用/检索等编排类工具既不计进展也不触发空转暂停。补充输入应用后重新观察，保留纠正参数的机会。同一完成缺项连续 6 次未解决仍以 `agent_completion_stalled` 暂停。暂停保留已有成果，用户补充后可继续。逐项尝试次数已在 `agent_task_requirements.attempts` 预留，当前只记录不参与冻结判定。

## 统一引用与按需水合

`ResourceRef` 统一字段为 `schema_version/kind/ref_id/uri/source/version/checksum/metadata/availability`。图片使用受管 `minio://`、artifact 或附件身份；方法使用 `skill://`。索引只列名称、用途、身份、版本及可用性。`HydrationIntent` 记录用途、调用身份、detail、裁剪及视频音轨策略；只有显式读取工具产生意图，普通决策不自动读取像素或方法全文。附件用 `read_resource(ref_id)`，Skill 深层资源用 `read_skill_resource(activation_id, path)`，图片组／本任务图片用 `read_image_content`；三类身份不能互换，但复用同一 Resolver。

| 资源 | 请求内注入 | 持久化 |
| --- | --- | --- |
| 图片 | Claude 的原生 tool_result image；Responses 原生工具输出；Chat 的 tool 占位及后续 user 视觉块 | 历史、请求模板和图状态仅留引用；当前请求结束后不再次注入像素 |
| Skill 正文 | 原 load_skill 调用对应的文本 tool result | 正文只保存于首次 Operation；重复调用返回原正文操作身份及已加载说明 |
| references、scripts | 分段文本工具结果，返回 next_offset | Operation 保存本次实际读取；脚本仅可读，不执行 |
| Skill 示例图 | 与普通图片相同 | 不写入 Skill 目录或常驻正文 |
| 音视频附件 | 受管 FFmpeg 转换、均匀采样帧，按用户策略包含或忽略音轨 | 冻结衍生对象及校验引用，不保存媒体编码 |

Resolver 先校验原件，再冻结处理后的 JPEG／MP3 到每 Turn 的确定性 MinIO 路径，最后在 SQL 事务外读取并编码。本次媒体总量最多 4 MiB，单张原图最多 4000 万像素。`low/high/original` 控制像素处理，裁剪须处于原图范围；供应商 detail 不等于原始像素无损传输。音频理解目前只支持 OpenAI Chat，不能把 Claude／Responses 视觉能力当作音频支持。

首次发送前的裁剪越界、资源不可用等水合业务错误冻结为 `resource_outcomes`，本次工具反馈包含错误及 `pixels_delivered=false`；越界错误提供真实宽高，Agent 可修正后再次读取。只有媒体请求成功结算后才记录 `media_evidence`，两类状态各保留最近 64 项，不保存媒体实体。已发送或已冻结的请求不能改变其资源结果；恢复时冻结素材缺失仍报错，不能伪造成功或重写原请求。

主动选入主体的图片读取须携带 `subject_key/group_id/item_id/media_id/purpose`。`read_image_group` 的图位目录提供可直接复制的 `read_content_args`，补上用途后调用 `read_image_content`；生成成果改用原始生成 `operation_id`，两种来源互斥。JSON schema 同时表达来源约束，服务端仍逐项校验；不按单个 media ID 猜测图位。

工具结算入口将新引擎结果集中保存到 Operation，ToolCall 和 pending results 仅存操作引用。HTTP 请求快照把工具正文替换为 `operation_ref`，媒体替换为 `image_ref/audio_ref`；实际发给模型的请求仍含本次所需正文和媒体。实体检测同时覆盖工具结算、请求、归一化响应、checkpoint 与 pending writes，发现媒体实体即拒绝写入并记录 `agent_media_entity_persistence_blocked`。原始响应的 `body_b64` 是文本 HTTP 回执编码，不能与图片实体混为一谈。

每轮另提供 `result_sources` 轻目录，只列成功标准／画布保存批次的原始 `operation_id` 和成果引用，默认最近 10 批，不被后续读取回执挤占。更早批次通过 `describe_creation_target(result_before_id, result_limit)` 分页，单页最多 20 批。新工具结果的 `receipt_operation_id` 是本次操作自身身份；`read_task_results` 返回的 `operation_id` 才是原保存来源，正文位于 `results[].content`。读取结果的 `result_refs` 为空不表示失败。传错读取操作 ID 时返回来源目录供纠正。标准回读还允许当前仍主动选入、被本任务更新的素材／分镜保存快照；取消选择后失去该资格，人工后续修改不进入保存快照。

## L1–L5、配额与 Skill 生命周期

保留原 MySQL 五层体系：L1 原始消息、L2 当前窗口、L3 阶段摘要链、L4 会话及任务约束压缩、L5 用户／会话／明确绑定作品的持久记忆。原来源校验、摘要水位、父摘要 CAS、遗忘与作用域限制继续生效。每段用当前投影替换状态，不重复堆叠完整快照；主体和附件正文仍需按需读取。

Skill 轻目录估算上限为 2000 tokens，手选优先，`next_offset` 按本页真正消费的候选推进，配额截断后仍能继续查询。活动正文估算配额 8000 tokens，最多 8 个活动方法；Task 只保留活动记录及最近 16 条失活记录，原 Operation 归档全部历史，不设累计激活上限。近期工具组默认估算配额 12000 tokens，随模型容量和其他输入成本缩小，保留完整调用／结果对；活动 Skill 的原正文调用对独立保留，仍计入总请求预算。单次最新工具组超配额不会被悄悄截断，总预算不足时明确拒绝。估算采用平台字节近似，不能当作供应商 tokenizer 实测。

历史默认只查最近 64 个成功 Turn ID、活动正文 pin 及上轮窗口 ID，再按 ID 读取所需 JSON；排序阶段不读取／筛选大 JSON，避免 MySQL filesort 携带整行。工具领取、模型调度及恢复查询最新 Turn 也统一先取 ID 再读取整行。恢复只查询 manifest 指定的 ID，不全量扫描旧正文。离开窗口的摘录增量合并到 Task 的有界 `tool_progress`，撤销／遗忘通过历史下界淘汰，不重新注入已失效正文。

L1–L5 来源核对仍按相关 Task 查询成功操作的轻元数据，仅投影 ID、Task、工具名、完成时间和显式记忆的来源消息 ID，不加载完整参数、工具结果或变更正文。已完成操作身份、显式记忆处理标记、来源 hash 和原有遗忘规则保持原语义。

记忆维护遇到凭据／配置或明确鉴权错误直接 `failed`，发送前失败不循环排队。明确限流可退避重试；抽取任务只有已持久化的无效输出允许再次请求，最多三次发送。可重试的抽取耗尽后记为 `abandoned` 并允许水位越过；普通失败及受理不明仍阻塞该范围，受理不明不重发。该限制仅适用于后台记忆作业，不是创作 Task 的累计执行上限。状态 CHECK 升级见[增量说明](../database/migrations/2026-10-26-agent-memory-retry/README.md)。

离开近期窗口的工具组额外投影为最多 2000 估算 tokens 的 `tool_progress`：优先保留已读证据，再保留已有模型旁白。提取顶层及 `results[]/items[]/subjects[]` 中的文本首尾短摘录，每项最多 256 字符，标注 Turn／Operation、原保存批次、资源身份、读取区间与截断状态，同来源同范围正文去重。不额外请求模型总结，不复制完整方法或媒体，不替代原回执和精确正文读取。额度不足时优先保留较近记录并报告省略数量；只有通过当前历史下界和作用域校验的工具组才能贡献摘录，撤销、换版或遗忘不会通过进度投影重新带回旧内容。`operation_receipts` 另保留最近 64 项完成状态与读取游标，保存来源始终由独立 `result_sources` 目录提供。工作笔记分别记录来源读取 ID 和自身记录操作 ID，重复记录不受新操作身份影响。

手选方法只冻结目录和版本，执行其任务前由 Agent 加载；自动方法由 SOP 缺口触发加载。同一活动版本重复加载不复制正文；新 Run 可恢复原正文操作。手选版本变化会失活旧激活并移动历史窗口下界，防止近期工具结果继续携带旧正文；其他仍活动的方法通过原正文 pin 保留。移除的方法加入排除目录，直到用户重新选入；Agent 不能自动替换手选或已激活的冻结版本。遗忘原来源会撤销其上下文保留资格。方法不能扩大作品读取、写入目标或媒体预算；自动方法不能覆盖用户约束。

阶段结束可用 `release_skill(activation_id)` 释放自动方法，停止正文独立保留；手选方法拒绝自动释放。重新加载同一不可变版本时优先复用仍有效的原正文 Operation，归档失活记录也可恢复；来源已遗忘或原正文位于历史下界之前时不复活旧正文。名称／slug 歧义先报错并要求唯一 ID，不因已有活动记录而错误去重。方法所需工具可来自已启用或可按需启用的能力组，具体工具执行仍受当轮 manifest 约束。

Harness 采用"单 Run 有限、可续期"的预算：任务创建时把 `AGENT_RUN_DECISION_CAP`（默认 240 次决策）与 `AGENT_RUN_OUTPUT_TOKEN_CAP`（默认 200 万输出 tokens）冻结进 `agent_policy.budget_caps`，每次建 Run 写入 `run.budget`。超限以 `agent_budget_exhausted` 暂停为 `paused_budget` 并保留成果；发送"继续"会在原 Task 下创建新 Run，**等于一次显式续期**，不设 Task 总上限。`active_seconds` 仍为 `null`：媒体等待会累计活跃秒数，设上限会误杀合法长任务。升级前的 Run 若冻结的是 `null` 四字段，继续按原无限策略解释，不改写历史记录。单次输出最多 8192 tokens，单次最多 16 个工具请求、请求超时最多 120 秒；上下文、媒体及来源配额继续生效。单次 graph 投递的递归保护不限制 Task 总轮数。旧引擎预算保持原规则，预算暂停不代表受理不明请求可以自动重发。

## 提示词与观测

提示词由 registry 组合并冻结版本，当前为 `assistant-harness-v8`。system 定义目标与可信边界；tool_use 定义自主选择、按需工具组、交付要求、保存来源与回读正文；resources 定义像素缺口、完整身份与单次水合；skills 定义手选优先、自动加载、版本、去重及阶段释放；compaction 定义水位、已读证据与活动正文保留。独立的 continuation 作为本轮用户续执行提示，冻结在 Turn 输入内并放在完整工具轨迹和动态状态之后，只核对当前缺口的前置依赖和未完成项，避免重复登记要求、每轮复述快照或把正文离开窗口误判为读取失败。关键事实进入有来源工作笔记，可恢复错误据回执纠正；没有真实保存来源时不能跳到回读。独立 verification 只提交检查结构，不执行创作工具，分别核对分镜剧本文字 `script` 与结构化配音台词 `dialogue.document.lines`。这些提示不指定固定业务工具序列。

请求布局 v2 先放稳定系统提示、轻任务身份和完整工具历史，再放动态任务 frame 及 continuation，以增加可复用前缀。工具 schema 按需启用：基础读取和编排工具常驻，`standard_write/canvas_write/media/memory_write/image_adoption` 由 `enable_tool_group` 启用后按启用顺序追加；schema 生成有缓存，返回独立副本，已有工具顺序稳定。旧冻结请求保留原布局和 schema，不改写已发送报文。提示词不代替 Pydantic schema、业务版本或权限校验。工具参数错误返回最多八个字段位置、错误类型和原因，不附输入值、异常上下文或内部堆栈，模型据此修正。

容量声明绑定模型版本、地址、模型键与凭据身份。有效声明参与平台预算（16384–1048576，平台最多使用 262144），默认 65536；窗口、输出预留与工具 schema 共同影响近期组额度。声明省略保留有效旧值，`null` 清除。当前只提供 API／DTO，未新增配置界面，也未把协议兼容当作视觉语义能力实测。

结构化日志记录 `agent_resource_hydrated` 的资源、字节与估算成本，`agent_context_compacted` 的 dropped_groups、retained_skills、recent_tokens，以及 `agent_context_assembled` 的 assembly_ms、tool_count、tool_schema_bytes、estimated_tool_tokens。Turn usage 保存当轮 context_metrics，Run 汇总实际 input_tokens、cached_tokens、uncached_input_tokens，重放不重复计量；缓存命中来自供应商 usage，不由平台估算替代。重复加载由 `already_loaded` 回执计量。当前提供日志与回执，未新增指标看板或告警通知服务；媒体泄漏日志为 ERROR 且写入失败，应由部署日志告警规则接入。

删除会话先冻结清理清单，再重试删除该 Turn 的衍生对象及图记录，不删除业务原件。上传完成但 Turn 引用尚未保存时崩溃，可能留下未引用衍生对象；目前未实现这类对象的自动 GC。Skill 原资源继续使用已有显式回收流程。

部署须执行 [Harness 增量](../database/migrations/2026-10-26-agent-harness/README.md)，同步 API、scheduler、Agent Worker 与前端。验证证据及供应商范围以 [验收记录](../reviews/2026-10-26-AI创作助手Harness重构验收.md) 为准。

长任务回读、普通消息续执行与累计预算策略的后续修复证据见 [修复验收](../reviews/2026-10-26-Harness长任务回读与续执行修复验收.md)。
