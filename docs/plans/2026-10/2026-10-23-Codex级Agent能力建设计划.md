# Codex 级 Agent 能力建设：底层剖析与分阶段实施计划

**状态：** 进行中（P0–P3 已实施）
**日期：** 2026-10-23
**目标：** 让 AI 创作助手具备 Codex 那种「关键步骤可见、过程可打断、产物可回滚」的运行时手感，同时把内部实现细节从用户视野里收干净。
**非目标：** 复刻 Coding Agent 的 shell 沙箱与文件补丁。我们不写本地文件，写的是剧本、分镜、画布和媒体。

---

## 0. 结论先行

当前体验的三个具体问题，都能定位到代码：

| 现象 | 根因 | 位置 |
| --- | --- | --- |
| 一句话回复上面顶着一大坨工具链，方法和回执全露出来 | 轨迹按「任务」整块渲染，不是按时间线 | `AssistantConversationBody.tsx:327` 把 `AssistantTaskTrace` 插在任务第一条消息后 |
| 看不到「说到一半去做事、做完接着说」 | 工具段的模型文本没有被持久化 | `agent/coordinator/runtime.py:724` 只有 `output_kind == "text"` 才建消息 |
| 用户能点开原始 JSON 回执 | 组件内直连接口 | `AssistantTaskTrace.tsx` 的「查看回执」调用 `assistantApi.operation` |

三处都不是模型问题，是**展示契约**问题。修完这三处，观感就接近 Codex。

---

## 1. 产品定位：用户该看见什么

### 1.1 三类可见性

| 级别 | 用户感知 | 展示形态 | 典型工具 |
| --- | --- | --- | --- |
| `silent` | 完全不可见 | 不渲染，不占位 | 加载/检索创作方法、记忆读写、读取操作回执、读取生成策略 |
| `step` | 知道「做了一步」，不关心细节 | 时间线里一行摘要，默认折叠成组 | 读取主体、读作品内容、查生成状态、读图片组 |
| `artifact` | 关心结果 | 产物卡片（可跳转），不展开参数 | 保存剧本补丁、保存画布补丁、提交媒体生成、设置主图 |

判定原则：**用户是否需要据此做决定**。方法加载不影响决策 → 隐藏；读了三张分镜影响信任感 → 一行摘要；生成了 4 张候选 → 必须看见。

### 1.2 工具映射（唯一事实源放后端）

| 工具 | 级别 | 用户看到的文案 |
| --- | --- | --- |
| `search_skills` | silent | — |
| `load_skill` / `read_skill_resource` | step | 读取创作方法（连续同名合并为 ×N） |
| `search_memories` / `read_memories` / `memory_catalog` / `remember_memory` / `update_memory` / `forget_memory` / `restore_memory` / `read_conversation_history` | silent | — |
| `read_operation_receipt` / `get_generation_policy` | silent | — |
| `describe_creation_target` / `read_selected_subjects` | step | 核对了创作对象 |
| `read_task_results` / `read_image_group` / `read_image_content` | step | 读取了作品内容 |
| `get_generation_status` | step | 查询生成进度 |
| `apply_standard_patch` | artifact | 已保存到正文 / 分镜 |
| `apply_canvas_patch` | artifact | 已更新画布 |
| `submit_generation_batch` | artifact | 生成 N 张 / N 条视频 |
| `set_image_group_primary` | artifact | 已设为主图 |

这张表必须写在 `agent/tools/contracts.py` 并由事件下发 `visibility` 字段，前端不许再维护一份硬编码名字表（现在 `AssistantTaskTrace.tsx` 的 `labels`、`internalTools` 就是第二份事实源，已经在漂移）。

### 1.3 明确从界面移除

- 「查看回执」按钮与回执 JSON 弹窗：删除入口。审计数据留在库里，需要排查时走运维接口或开发构建。
- 「使用方法：剧本创作·版本 2 / references/method.md·从字符 0 读取」这类方法装载明细：不再出现。
- 「计划 1」这类内部计数标签：改成有语义的「待确认 1 项」，或直接并入审批卡片。

---

## 2. 目标交互：交错时间线

### 2.1 目标形态

```
用户：按《剧本创作》方法，写第 1 集
助手：好，我先看一下当前作品的三幕结构。
  ▸ 已完成 2 步 · 核对了创作对象、读取了 3 个镜头
助手：结构上第 2 幕偏短，我直接补一版。
  ▸ 已保存到正文（附查看链接）
助手：另外补了两张分镜图，需要你选一张。
  ▸ 生成 2 张候选（附候选卡）
```

对应 Codex 的 item 序列：`agent_message → tool → agent_message → tool → tool → tool → agent_message`。**一个 Run 里可以有多个助手消息**，而不是只在最后吐一段。

### 2.2 分组与折叠规则

1. 连续的 `silent` 工具不产生任何节点，只累加到后续步骤组的计数里（也可以选择完全无痕，见第 9 节待确认）。
2. 连续的 `step` 工具合并为一个步骤组：运行中显示「正在核对创作对象…」，完成后折叠为「已完成 3 步」，点击展开为 3 行。
3. `artifact` 永远独立成卡，不并入步骤组，因为它承载结果。
4. 助手叙述（narration）永远独立成气泡，且会中断步骤组：新的叙述之后，后续工具另起一组。
5. 组内每行只有：状态图标 + 一句摘要 + 可选结果链接；**没有参数、没有返回值、没有 JSON**。

### 2.3 流式与状态

| 阶段 | 步骤组表现 | 叙述气泡表现 |
| --- | --- | --- |
| 模型生成中 | 不出现（尚无工具） | 逐字流式追加 |
| 工具执行 | 展开，当前行左侧转圈，已完成行打勾 | 保持上一条不动 |
| 工具全部完成 | 自动折叠成「已完成 N 步」 | 等下一段 narration |
| 运行结束 | 全部折叠 | 最后一条常驻 |
| 失败/中断 | 组头变红，失败行保留错误摘要 | 保留已生成内容 |

折叠状态只存在于前端，不落库；刷新后默认「已完成的折叠、进行中的展开」。

### 2.4 与 Codex 的对应关系

| Codex | 本方案 | 说明 |
| --- | --- | --- |
| `item/started` / `item/*/delta` / `item/completed` | `tool.started` / `tool.delta` / `tool.finished` | 生命周期对齐，字段裁剪到业务所需 |
| `Turn` | 一个 Run 内的 `turn_no` | 已有 `agent_turns`，无需新概念 |
| `Thread` | Conversation | 已持久化，可续接 |
| 审批 item | `waiting_review` 卡片 | 已有，界面需要收敛到时间线内 |
| diff item | 产物卡 + 版本对比 | 排到 P4 |

---

## 3. 技术选型：为什么自制组件

现状：`@ant-design/x@1.6.1`，没有 `Think`；`ThoughtChain` 是唯一的折叠链组件。

`Think` 从 2.0.0 才有，且 **2.0.0 起 peer 就是 `antd ^6.0.0`**（2.9.0 要 `^6.1.1`）。本项目是 `antd ^5.29.3`，为一个小卡片升级 antd 6，等于全站组件回归，收益与风险完全不成比例。

| 方案 | 成本 | 风险 | 结论 |
| --- | --- | --- | --- |
| A. 升级 antd 6 + X 2.9 用官方 `Think` | 高（全站 UI 回归） | 高 | 否 |
| B. 直接 `ThoughtChain` 换皮 | 低 | 语义不对：它是「思考链」，我们是「叙述+动作时间线」 | 否 |
| C. 自制 `ThinkBlock` + `AgentTimeline` | 中（约 150 行组件 + 80 行样式） | 低，无新依赖 | **采纳** |

补充：官方 `Think` 的实现本身只有约 4KB JS + 2KB 样式（折叠头 + 状态图标 + 内容区），语义可以照搬，不需要移植依赖。自制还能直接吃到项目已有的暗色主题变量。

**组件拆分**

- `features/ai-assistant/AgentTimeline.tsx`：接收已排序的 items，负责把连续 step 合并成组、把 narration 与 artifact 分开渲染。
- `features/ai-assistant/ThinkBlock.tsx`：步骤组容器。头部 32px（图标 14 + 13px 文案 + 右侧 chevron），展开区每行 24px，13px 次要色文本。
- `features/ai-assistant/AgentArtifactCard.tsx`：产物卡，沿用现有 result chip 的跳转逻辑。
- 复用 `AssistantReply` 渲染 narration，`Bubble.List` 继续做外层容器，只改 items 组装逻辑。

---

## 4. 现状与差距

| 维度 | Codex | 现状 | 差距 |
| --- | --- | --- | --- |
| 时间线 | Item 按序交错 | 每个任务一个整块轨迹，插在首条消息后 | 需要 item 顺序模型 |
| 叙述 | 助手可在任意位置说话 | 只在段落输出为纯文本时建消息 | 工具段的文本被丢弃 |
| 工具可见性 | 由 UI 决定展示粒度 | 全部展示，且暴露原始回执 | 需要 visibility 策略 |
| 审批 | 阻塞式、就地插入 | 独立面板 | 排 P2 |
| 中断 | 保留已生成 | `stop` 直接取消整轮 | 排 P2 |
| 上下文 | 三相位压缩 + 缓存前缀 | 任务级压缩，无前缀治理 | 排 P3 |
| 产物 | diff + 部分采纳 | 整份替换 | 排 P4 |

Codex 的内部机制（Op/EventMsg 双通道、`needs_follow_up = model_needs_follow_up || has_pending_input`、`preempt` 抢占、分相位压缩）已在上一版剖析中核实，此处不重复。

---

## 5. 阶段计划

| 阶段 | 目标 | 工作量 | 与 Codex 的核心差距 |
| --- | --- | --- | --- |
| P0 时间线与可见性 | 只展示关键步骤，去掉回执 | 10–12 人日 | 缺 `Item` 顺序与生命周期 |
| P1 交错叙述 | 边做边说 | 5–6 人日 | 缺 turn 中途的 `agent_message` |
| P2 软停与抢话 | 随时纠偏不丢稿 | 6–8 人日 | 缺 `pending_input` 与抢占 |
| P3 上下文与成本 | 长会话不劣化 | 4–5 人日 | 缺分相位压缩与缓存前缀治理 |
| P4 diff 与部分采纳 | 改一段不用重生成 | 10–12 人日 | 缺 diff 跟踪与 base 版本 |
| P5 多入口 | 桌面/IDE 复用 | 可选 | 缺稳定协议层 |

### P0 时间线与可见性（10–12 人日）

**做什么**

1. 数据层：会话新增 `next_item_seq`；`agent_messages` 增 `item_seq`、`kind`（`reply`/`narration`/`notice`）；`agent_tool_calls` 增 `item_seq`。
2. 写入层：发消息、建工具调用时领取 `item_seq`；工具调用在 `prepare` 阶段就分配，保证「先发生先排序」。
3. 事件层：`tool.started` / `tool.finished` 携带 `item_seq`、`visibility`、`summary`、`refs`；`message.created` 携带 `item_seq`、`kind`。
4. 接口层：`runtime_state` 快照返回合并后的 `items`（或由前端合并 messages + tool_calls，二选一，建议后端合并）。
5. 前端：新增 `AgentTimeline` / `ThinkBlock` / `AgentArtifactCard`，重写 `AssistantConversationBody` 的 items 组装；删除 `AssistantTaskTrace` 的回执弹窗与 `labels` 硬编码。
6. 文案：后端下发 `summary`（中文），前端不再维护工具名映射。

**为什么先做**：它是「不要一股脑展示」的唯一落点，也是后续 P1/P2/P4 的展示底座。没有 item 顺序，交错叙述无从谈起。

**与 Codex 的差距**：等价于补齐 `Item` 原语与 `item/*` 事件。Codex 的 Item 有三段生命周期（started/delta/completed），我们现在只有终点事件，且前端拿不到统一序号；跨进程协议不搬，只做会话内有序流。

**验收**

- 一次含 6 个工具的 Run，界面只出现 ≤2 个步骤组 + 产物卡，不出现方法装载与回执入口。
- 步骤组完成后默认折叠；展开后每行一句摘要，无参数、无 JSON。
- 刷新页面后顺序与实时一致；历史数据（无 `item_seq`）按 `turn_no` 兜底排序。
- 后端 fast 测试通过；前端 `npm run typecheck` + 对应 node 测试通过。

### P1 交错叙述（5–6 人日）

**做什么**

1. `agent/gateway/protocol.py`：`AgentSegmentResult` 增 `narration`，从终止响应的 TextPart 提取（已做脱敏），仅在输出为 `DeferredToolRequests` 时填充。
2. `agent/coordinator/runtime.py`：段落落库时，若 `narration` 非空，先建 `kind='narration'` 的助手消息，再建工具调用。
3. 事件：新增 `narration.created`（或复用 `message.created` 带 `kind`）。
4. 前端：narration 渲染为正常气泡，与前后的步骤组自然衔接；为空则不产生空气泡。

**为什么**：这是「agent 回复 + 工具 + agent 回复」的全部来源。没有它，界面只能把工具堆在最前面。

**与 Codex 的差距**：Codex 的 `agent_message` 是独立 item，可以在 turn 中途出现，这是它「边做边说」的基础。我们在工具段把模型说的话丢掉了，只能在最后补一段总结。

**验收**

- 构造一个「说一句 → 调工具 → 再说一句 → 再调工具」的 Run，界面呈现 4 段交错，而不是 1 段总结。
- narration 为空时不出现空行；历史 Run 无 narration 不影响展示。

### P2 软停与抢话（已实施）

**做什么**

1. `stop` 拆成 `interrupt`（停当前段，保留已生成消息与候选）与 `cancel`（现状语义，整轮作废）。
2. 心跳处检查 `cancel_requested`，中止 HTTP 时保留已落 `delta`，补 `run.interrupted` 事件。
3. 通过「同 Run 内 pending input」实现插话：新输入在段间作为下一条 user 消息进 prompt，而不是另起 Run 排队。
4. 前端：停止按钮文案区分「停止本轮」与「取消任务」；中断后保留的步骤组标记为「已中断」。

**为什么**：现在改一句话要整轮重来，这是最伤的一处。Codex 的 `has_pending_input` 让插话成为 turn 继续的原因之一。

**与 Codex 的差距**：Codex 用 `needs_follow_up = model_needs_follow_up || has_pending_input` 把工具循环和人工插话放进同一个回合，靠 `preempt + CancellationToken` 在流式过程中中断。我们的插话只能另起 Run 排队，`stop` 直接 `finish_locked(cancelled)` 终结整轮。

**验收**：生成中点停止，已输出文本保留可继续；插话后新指令在下一段生效；`usage.external_requests` 不因中断增加。

### P3 上下文与成本（已实施）

**做什么**：动态内容（用户约束、技能全文、上下文快照）从 `instructions` 移到 user prompt，保证系统提示与工具清单逐轮字节稳定；`agent/task/compaction.py` 增加采样前与回合后触发点；记录 provider 的 cached tokens。

**为什么**：20 轮以后变慢变贵，根因是前缀不稳定导致缓存全失效（Codex 踩过同样的坑）。

**与 Codex 的差距**：Codex 分 PreTurn / MidTurn / PostTurn 三个压缩相位，并有后台候选压缩；更关键的是它把「系统提示与工具顺序逐轮字节一致」当成缓存命中的硬前提。我们只有任务级压缩，也没有缓存命中治理。

**验收**：第 10 轮 `cached_tokens > 0`；单轮耗时与 token 不随轮次线性增长。

### P4 产物 diff 与部分采纳（10–12 人日）

**做什么**：`ApplyStandardPatch` / `AssistantCanvasPatch` 补 `base_version` 与反向 patch；候选增加段落级 diff；前端支持逐段勾选；冲突返回 409 并可重放；撤销复用现有撤销链。

**为什么**：改一段就要重生成整份，是创作场景最贵的浪费。

**与 Codex 的差距**：Codex 用 `SharedTurnDiffTracker` 跟踪本轮所有改动，`apply_patch` 以 diff 为单位落地，因而能「只接受第 2 处」。我们的候选是整份快照，没有 base 版本、没有反向补丁，撤销只能按批次整回退。

**验收**：同一候选可只采纳第 2 段；撤销后版本号与内容一致；冲突路径有测试。

### P5 多入口与外部工具（可选，10 人日+）

先做事件契约抽象（SSE 载荷收敛成稳定 JSON），为桌面/IDE 复用；确有外部能力需求时再引入 MCP 客户端，先解决权限边界与超时。

**为什么**：现在只有网页一个入口，事件载荷还带着实现细节；一旦要做桌面端或插件，就得重写一套对接。

**与 Codex 的差距**：Codex 用 App Server 的 JSON-RPC 把 TUI、IDE、桌面统一在同一套 Item/Turn/Thread 协议上，服务端还能主动发起审批请求（双向）。我们是 REST + SSE 单向推送，协议不是稳定契约。

---

## 6. 变更清单

**数据库迁移** `docs/database/migrations/2026-10-23-agent-timeline/`

| 变更 | 说明 |
| --- | --- |
| `agent_conversations.next_item_seq` | 会话级单调序号，默认 1 |
| `agent_messages.item_seq` / `kind` | `kind` 取 `reply`/`narration`/`notice`，默认 `reply` |
| `agent_tool_calls.item_seq` | 与消息共用序列，保证交错顺序 |
| 回填 | 历史消息按 `seq`，工具按 `(created_at, id)`，仅保证不崩不重 |

**后端**

- `agent/tools/contracts.py`：新增 `VISIBILITY` 与 `summary` 文案表。
- `agent/core/events.py`：新增事件类型与字段白名单。
- `agent/coordinator/runtime.py`：narration 落库；`tool.started` 前置。
- `agent/gateway/protocol.py`：`narration` 提取。
- `api/v1/assistant.py`：`runtime_state` 返回 `items`。
- `docs/api/assistant.md`：事件契约与顺序保证。

**前端**

- 新增 `AgentTimeline.tsx`、`ThinkBlock.tsx`、`AgentArtifactCard.tsx`、`agent-timeline-state.ts`。
- 改 `AssistantConversationBody.tsx`（items 组装）、`AssistantTaskTrace.tsx`（只留产物与撤销）。
- 删 `ThoughtChain` 依赖使用；保留 `Bubble.List` 作外层。
- 样式进 `ai-assistant.css`，全部用现有主题变量，不引入新色板。

---

## 7. 验收口径

| 指标 | 现状 | P0 后 | P1 后 |
| --- | --- | --- | --- |
| 单次 Run 可见节点数（6 工具场景） | 6+ 条全展开 | ≤2 组 + 产物 | ≤2 组 + 2 叙述 + 产物 |
| 原始 JSON 可被用户打开 | 是 | 否 | 否 |
| 方法装载可见 | 是 | 否 | 否 |
| 交错叙述 | 无 | 无 | 有 |
| 事件端到端延迟 | 未度量 | p95 < 800ms | 同左 |

不把「步骤更少」当目标——目标是**可解释**，不是**少显示**。该看见的产物一步都不能少。

---

## 8. 风险与回滚

| 风险 | 处理 |
| --- | --- |
| 历史会话顺序错乱 | `item_seq` 为空时前端按 `turn_no` 兜底，不阻塞 |
| narration 与工具摘要重复表达 | narration 超过阈值才展示；同一 turn 内不重复同一句话 |
| 步骤组吞掉关键信息 | `artifact` 永不被合并；失败行永远单独可见 |
| 事件变多压 SSE | 服务端对 `tool.delta` 合并节流，已有 delta 合并机制可复用 |
| 迁移影响在线库 | 三列均可空 + 默认值，可先发代码后跑回填 |

---

## 9. 待你确认（不影响开工）

1. **silent 工具是否留一句汇总**：例如「已参考 2 项创作方法」。默认**完全不留**，更干净；若希望保留信任感，可加一行灰字。
2. **步骤组默认折叠**：默认「运行中展开、完成后折叠」。若你希望全程展开，改一个 prop 即可。
3. **产物卡形态**：P0 先做「一行摘要 + 查看链接」，P4 再做内嵌预览。
4. **历史会话**：是否需要对最近 N 条历史任务重算 `item_seq` 以获得一致观感，还是仅新任务生效（默认仅新任务）。

---

## 10. 参考

- `openai/codex`：`codex-rs/protocol/src/protocol.rs`（Op/EventMsg）、`codex-rs/core/src/session/turn.rs`（`needs_follow_up`、`preempt`、分相位压缩）、`codex-rs/core/src/tools/registry.rs`（工具 namespace/exposure）。
- 本仓库：`backend/src/short_drama/agent/`、`frontend/src/features/ai-assistant/`。

---

## 11. 实施记录（2026-10-23，P0 + P1）

**已落地**

- 迁移 `2026-10-23-agent-timeline`：`agent_conversations.next_item_seq`、`agent_messages.item_seq`/`kind`、`agent_operations.item_seq`；历史会话按 `(created_at, id)` 在每个会话内统一回填，`short_drama` 已执行，空值 0、发号器全部 ≥1。
- 可见性唯一事实源：`agent/tools/presentation.py`，三档 `silent`/`step`/`artifact` 与中文 `label`，前端不再维护工具名映射。
- 顺序：消息与操作共用 `agent/core/items.py::allocate_item_seq`；快照与 `step.*` 事件都带 `item_seq`。
- 叙述：`AgentSegmentResult.narration` 从终止响应的 `TextPart` 提取并脱敏；运行时先落 `kind=narration` 的助手消息，再建工具调用。
- 前端：`agent-timeline.ts`（纯函数合并与分组）、`ThinkBlock`（运行中展开、完成后折叠）、`AgentArtifactCard`、`AssistantOperationResults`；`AssistantConversationBody` 改用 `timelineBubbles`；删除 `AssistantTaskTrace` 与「查看回执」入口。

**与计划的差异**

- `item_seq` 落在 `agent_operations` 而非 `agent_tool_calls`：界面展示单元是操作（已聚合结果与撤销回执），tool call 只是执行细节，两者一一对应，只有操作需要参与排序。

**验证**

- 后端 `pytest tests/unit/standard/{agent,assistant}` 399 项通过；改动文件 ruff 通过。
- `export_schema.py --check` = 116 表一致；`check-docs` 通过；API 启动自检未被 readiness 拒绝。
- 前端 `tsc --noEmit` 通过；`vite build` 成功（环境禁止批量清空 `dist/assets`，改用独立 outDir 验证，产物已清理）。

**未验证**

- 真实模型下的 narration 产出：需要一次真实多步创作，观察是否出现「回复 → 步骤 → 回复」。
- 浏览器端历史会话观感：需要启动前端人工核对折叠与顺序。

### P2 实施记录（同日）

**软停**

- `AgentRunService.stop` 与 `apply_controls_locked` 不再由 API 立即终结正在运行的 Run：改为置 `cancel_requested`，由 Worker 在流式增量处感知并中止请求。
- `_DeltaBuffer` 累计已流出文本；`save_delta` 返回 `ok/lost/cancelled`，感知到取消后 `save_interrupted` 先把文本落成 `kind=narration` 消息、补 `run.interrupted` 事件，再 `finish_locked(cancelled)`。
- 排队、待审、等媒体等没有在途段的 Run 仍在 API 内立即结束，避免悬挂。

**补充要求（插话）**

- 前端在任务运行中允许输入并发送，走既有 `control redirect`：同一任务、同一条消息流，输入在下一段生效，不再新建排队 Run。
- `control()` 返回是否受理，只有受理成功才清空草稿；停止按钮保留，发送按钮仅在草稿非空时出现。

**边界（需要在验收时说明）**

- 已经产生输出的流式段：取消在 0.2 秒增量周期内生效。
- 模型尚未输出首个 token：最多等一个心跳周期（约 5 秒）。
- 工具执行中停止：仍等当前工具结束，沿用既有约束，避免半途副作用。

**验证**：`tests/unit/standard/{agent,assistant}` 399 项通过；`tsc --noEmit` 与 `vite build` 通过；改动文件 ruff 通过。

### P3 实施记录（同日）

**稳定前缀**

- 新增 `agent/context/prompt.py`：`build_segment_prompt` 把「稳定策略文本」留在 `instructions`，把本轮状态（授权、技能、历史、回执、待审计划）序列化为一段用户消息。
- 三条路径统一改造：`legacy/tools.py`（项目/分集创作）、`legacy/chat.py`（纯对话）、`coordinator/assistant.py`（项目助手）。续段只在状态摘要变化时重发该消息，摘要写入 `run.checkpoint.prompt_digest`。
- 结果：系统提示逐轮字节一致，缓存前缀不再被动态状态打断。

**缓存计量**

- `initial_usage` 增加 `cached_tokens`；网关从 `prompt_tokens_details.cached_tokens`（chat）与 `input_tokens_details.cached_tokens`（responses）提取；每轮结算一次并累加到 `run.usage`。
- 未上报时不写入该字段，避免把「未报告」当成 0 命中。

**压缩触发**

- 采样前：项目助手 `prepare_decision` 调用 `schedule_task_compaction_locked`，历史超过软阈值时提前排任务。
- 回合后：文本成功结束时再调用一次；函数自身按软阈值与幂等键去重，重复调用不会重建任务。

**验证**：`tests/unit/standard/{agent,assistant}` 403 项通过（新增 4 项前缀用例）；ruff 通过。

**真实验收**：已用 `deepseek-v4.1-flash` 完成网关层验收，第二次相同前缀命中 `cached_tokens=1792`，工具调用/续段/叙述/截断/错误映射均通过，详见[验收记录](../../reviews/2026-10-23-DeepSeek真实模型验收.md)。应用层全链路与媒体仍未覆盖。

#### P0 补充：方法读取改为可折叠步骤（人工验收反馈）

验收时发现「读取方法」整组不可见。原因是 `load_skill` / `read_skill_resource` 按最初「silent 不留汇总」的决策归为 `silent`。按验收意见调整为：

- `search_skills` 仍为 `silent`（检索过程无信息量）；
- `load_skill` / `read_skill_resource` 改为 `step`，文案「读取创作方法」；
- 步骤组内连续同名步骤合并为「读取创作方法 ×N」，避免一次读取多个资源时刷屏；
- 历史操作无需回填，`visibility` 在读取时计算，重启 API 即生效；新运行的实时事件需重启 Agent Worker。

#### 输入区整改（人工验收反馈，第二轮）

- **回复操作**：删除助手气泡下方的「复制」按钮，只保留确有修订历史时的「修订记录」。
- **方法选择**：不再出现在输入框顶部的标签行。选中后以 `/方法名` 写回正文，并由高亮层标出；删掉这段文字即卸载该方法。`skills` 状态由正文派生（`assistant-skill-token.ts`），Skill 面板增删也会同步正文，避免出现「已加载但看不见」的方法。
- **下拉列表**：`/` 列表只展示 Skill 标准的 `name` 与 `description`，不再输出「内置/个人 · vN · ID」这类内部元数据；完整面板仅在任务版本落后时提示 `v` 差异。
- **附件展示**：上传后的附件由「文件卡 + 缩略图」改为 56×56 缩略图磁贴，名称走 title，移除按钮悬浮出现；`@` 列表为图片素材补上 28×28 缩略图，选中后 `@名称` 与 `/方法名` 共用同一高亮层。

**实现约束**：高亮层是 textarea 身后的镜像 `div`，文字仍由原生输入框渲染（输入法、选区、撤销不受影响），镜像的字体、行高、内边距与滚动条宽度按输入框实测复制，两层换行保持一致；不使用 `contentEditable`，避免与 Sender 的受控值互相覆盖。

**测试**：新增 `frontend/tests/assistant/assistant-skill-token.test.mjs`（5 项）；`frontend/tests/assistant` 41 项通过；`tsc --noEmit` 与 `vite build` 通过；`test-map.json` 补登记 3 个测试文件、7 个新增前端源文件与已删除的 `AssistantTaskTrace.tsx`。

#### 输入区整改（人工验收反馈，第三轮）

**附件按消息隔离**

- 删除「任务附件草稿」概念：`AssistantAttachments` 组件、`attachmentDraft` 状态与持久化字段全部移除，因此不会再出现“任务附件已变化 / 任务已变化，原附件选择已保留”两条提示，也不会再卡住发送。
- 发送时由 `assistant-attachment-scope.ts` 读取任务输入水位并传 `attachment_selection.retain_ids=[]`：本条消息的 `attachment_ids` 成为该任务当前唯一附件集合，旧图不会被逐轮重复注入模型上下文。新任务不传该字段。
- 发送后输入区无残留：输入框附件随消息发送即清空，任务附件不再常驻显示。

**素材统一与 @ 识别**

- 本地上传、资产库引用最终都是 `AgentAttachment`；`AgentContextComposer` 通过 `ComposerAttachmentsContext` 把它们交给输入区，`@` 列表把“当前消息附件”排在最前（缩略图 + 名称，标记“附件”），选中只写 `@名称` 并高亮，不重复建附件。
- 模型提示中本来就带 `附件 {名称}：` / `资料 {名称}：` 前缀，`@名称` 可被准确对应。
- 不做“添加参考”分类分组。

**展示**

- 输入区附件改为 30px 药丸：22px 缩略图（图片真实预览）+ 文件名（省略）+ 悬浮移除。
- 消息气泡只留缩略图：图片 56×56 可点预览，文本 3 行摘要，音视频只给图标，不再展开播放器与文件名列表。
- 发送受阻原因（附件恢复失败、待核对）现在在输入区上方直接显示，不再只出现在保存状态面板。

**词条交互**

- 高亮用 `box-shadow` 模拟内边距（真实 padding 会让镜像层错位）。
- Backspace/Delete 落在 `/名称`、`@名称` 内或边界时一次删掉整个词条，并吞掉一个分隔空格；纯函数在 `prompt-tokens.ts`。

**验证**：`tests/assistant` 44 项（新增 prompt-tokens 6 项）；`--changed --runner node` 373 项通过；`tsc --noEmit`、`vite build`、`check-docs`、`check-quality` 通过。`test-map.json` 补登记新测试、3 个新增源文件与已删除的 `AssistantAttachments.tsx`。
