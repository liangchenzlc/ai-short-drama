# Claude Code Harness 对照与 AI 创作助手改进分析

日期：2026-11-04。对象：`learn-claude-code`（s01–s17 主线 + `docs/`、`agents/` 旧 12 章）与本项目 AI 创作助手（`backend/src/short_drama/agent/**`、`service/assistant/**`、`ai/prompts/assistant/**`）。基线：`main`。本轮只做静态阅读与定点核对，未启动服务、未调用真实模型与媒体供应商、未跑测试。

## 0. 阅读范围与判定口径

| 类别 | 实际阅读 |
| --- | --- |
| 教程主干 | `README-zh.md` 全文；`s01`–`s09`、`s15`、`s17` 的中文 README；`s01`/`s02`/`s07` 的 `code.py` 全文 |
| 教程旁支 | `s05`、`s17` 的机制说明；`docs/`（旧 12 章）与 `agents/s_full.py` 仅确认版本关系，未逐行读 |
| 本项目 | `docs/instruction/AI创作助手Harness运行机制.md`、`docs/plans/2026-10/2026-10-21-AI创作助手系统设计总览.md`、`…AgentSkill管理与按需调用设计.md`、`docs/reviews/2026-11-04-AI创作助手模块全面审查.md` |
| 本项目代码 | `agent/runtime/{agent,executor,middleware,prompt}.py`、`agent/tools/registry.py`、`agent/skill/progressive.py`、`agent/context/{budget,prompt,graph}.py`、`ai/prompts/registry.py`、`tools/sandbox/execute_description.py` |

判定用词固定为四档，避免把“不一样”直接说成“差”：

- **对齐**：机制等价，只是实现载体不同。
- **更强**：本项目在权限、持久化、审计、版本等工程约束上更严。
- **更弱**：缺该机制，或明显简化到影响能力。
- **形态不同**：约束前提不同，不能直接比较优劣。

## 1. Claude Code 的架构定位

### 1.1 一条公理，五个职责

教程开篇先否掉一个常见误解：agency（感知—推理—行动）来自模型训练，不来自外层编排。由此推出全书唯一的架构主张——**模型是驾驶者，harness 是载具**。harness 的职责被切成五块：

```
Harness = Tools + Knowledge + Observation + Action Interfaces + Permissions
```

这条公理带来一个很强的设计约束：**凡是“替模型做判断”的代码都是负债**。所以全书没有意图分类器、没有节点图路由、没有“如果用户说 X 就走 Y”的分支；所有分叉都发生在模型的一次 `tool_use` 里。s01–s17 每章只做一件事——给载具加一个部件，且都不碰主循环的判断权。

### 1.2 机制地图

17 章不是 17 个功能，而是六层递进：

| 层 | 章节 | 解决的问题 | 一句话机制 |
| --- | --- | --- | --- |
| 能动手 | s01–s04 | 循环、工具、权限、扩展点 | 一个 `while True` + 工具表 + 三道闸门 + 四个 hook |
| 做复杂任务 | s05–s08 | 计划、隔离、知识、上下文 | todo、fresh messages、目录常驻正文按需、四级压缩 |
| 跨会话 | s09 | 记忆 | 索引 + 选择 + 提取 + 整理 |
| 长期运行 | s10–s12 | 任务、后台、定时 | 落盘任务图、线程 + 通知、cron |
| 协作 | s13 | 多 agent | 持久队友 + 原子认领 + 任务绑定 worktree |
| 扩展与收口 | s14–s17 | MCP、集成、编排、目标 | 工具池组装、单循环集成、journal 续跑、独立判断器 |

值得注意的是 **s15 不引入新机制**，只回答“前面这些部件从哪个位置插进同一个循环”。这一章的表格（组件 → 循环位置）其实是全书最有工程价值的一页：它把“机制”还原成“时序位置”。

### 1.3 三个核心模块如何咬合

```
用户输入
  └─ messages[] 追加 user
       └─ LLM(system=固定指令+技能目录, tools=全部工具)
            ├─ 无 tool_use → 返回（s04 起先过 Stop hook）
            └─ 有 tool_use → 逐个查 TOOL_HANDLERS
                 ├─ PreToolUse：权限/日志（可拦截）
                 ├─ handler(**input) 执行
                 ├─ PostToolUse：副作用/告警
                 └─ tool_result 追加回 messages → 下一轮
```

三者的分工可以概括成一句话：**loop 决定“还能不能继续”，tool_use 决定“能碰什么”，skill 决定“知道怎么做”**。三者共享同一个消息列表，所以任何新增能力都必须以“一条消息”的形态回到循环里——这正是它们能组合的原因，也是它们的天花板（没有跨轮持久状态）。

## 2. 核心模块解剖

### 2.1 agent_loop：把“中间层”自动化

**职责**：只做四件事——发请求、追加 assistant 回复、判断有没有 `tool_use`、把结果作为新消息追加。三十多行，没有重试、没有持久化、没有并发控制。

**契约**（`s01_agent_loop/code.py`）：

| 环节 | 契约 | 设计意图 |
| --- | --- | --- |
| 输入 | `messages` 列表由调用方持有 | 循环无状态，历史归宿主 |
| 继续条件 | 响应里存在 `block.type == "tool_use"` | 唯一的控制信号来自模型输出结构，不来自文本 |
| 结果配对 | 每个 `tool_use` 必有一条同 `id` 的 `tool_result` | 协议合法性由结构保证，不靠提示词 |
| 退出 | 无 `tool_use` 即 `return` | 不判断“是否真的完成” |

**设计意图**：把“人肉复制粘贴命令输出”这个中间层自动化，仅此而已。教程刻意不在这里加任何业务判断——因为一旦循环开始理解任务，它就要开始替模型决策，而这正是 README 批评的“提示词水管工”。

**明确的能力边界**（也是它的诚实之处）：进程内内存历史、崩溃即丢、无并发、无预算、无多租户。s08 之后才补上下文，s10 之后才补持久化，且都不改循环形状。

### 2.2 tool_use：把“意图”翻译成“结构化动作”

**职责**：用 `TOOL_HANDLERS` 字典把工具名映射到处理函数，循环里只改一行：

```python
# s01
output = run_bash(block.input["command"])
# s02
output = TOOL_HANDLERS[block.name](**block.input)
```

**为什么 bash 已经能做还要加专用工具**——这是全章最有价值的一段论证，它给出的不是“方便”，而是三个 harness 层面的收益：

1. **减少翻译损耗**：模型想“读这个文件”，不必拼 `cat path/to/file`，少一层语法转义。
2. **让策略可精确**：harness 看到的是 `(read_file, {path})`，而不是一整个不透明字符串；s03 才能按工具和路径判定，s04 才能统一记录。
3. **让扩展成本恒定**：加工具 = `TOOLS` 加一条 + `TOOL_HANDLERS` 加一行，循环不变。

**边界处理**：`safe_path()` 把文件工具限制在工作区内（bash 不受限，留给 s03）；`glob` 最多返回 200 条并显式提示收窄；`read_file` 超限时追加“… 还有 N 行”。这些细节共同表达一个原则——**工具必须自己把“结果不完整”说清楚**，而不是让模型猜。

**多工具调用**：按 `response.content` 原始顺序逐个执行。教程没有做并行，理由是教学清晰；但也因此留下一个真实约束：同一批工具调用之间不能有隐含依赖假设。

### 2.3 skill：知识按需加载，而不是前置塞满

**问题设定**很具体：三份规范全塞 system prompt，改 React 组件时 SQL 规范和 API 文档也在烧 token，还挤压了代码与工具结果的空间。

**解法是两级加载**：

| 内容 | 进入位置 | 时机 | 成本 |
| --- | --- | --- | --- |
| 名称 + 描述（目录） | system prompt | 启动时一次 | 每轮固定，极小 |
| 完整 `SKILL.md` | `tool_result` | 模型调 `load_skill(name)` | 只在需要时付 |

实现上 `SkillLoader.scan()` 遍历 `skills/*/SKILL.md`，解析 YAML frontmatter 取 `name`/`description`，`catalog()` 只输出 `- name: description` 列表，`load(name)` 从内存注册表取正文——**`name` 只用于查表，不当路径用**，避免路径穿越。

**设计意图**：让模型“知道有什么可用”，然后把“要不要用、用哪个”留给模型。这与 RAG 的区别在于——检索是模型主动发起的一次工具调用，而不是 harness 推测相关性后注入；好处是可解释、可复现，坏处是多一次往返。

**一处容易被忽略的细节**：`load_skill` 本身就是一个普通工具，因此它自动继承 s03 的权限与 s04 的 hook，不需要为“加载知识”单独开一条链路。这是“工具即接口”的复利。

### 2.4 其余机制的一句话职责

| 章节 | 机制 | 一句话 |
| --- | --- | --- |
| s03 | Permission | 三道闸门（硬拒绝 → 规则 → 用户审批），判断发生在执行前 |
| s04 | Hooks | 四个生命周期点，扩展挂在循环外，返回值即控制流 |
| s05 | TodoWrite | 只增加规划能力，不增加执行能力；三轮未更新则提醒 |
| s06 | Subagent | 全新 `messages[]`，只回最终文本，中间过程不进父上下文 |
| s08 | Compact | 四级递进：转存 → 裁剪 → 替换 → 摘要，只有最后一级调模型 |
| s09 | Memory | 索引与正文分离，一次轻量选择调用 + 关键词兜底 |
| s17 | Goal Loop | 无 `tool_use` 只代表“这一轮想停”，完成与否交给独立判断器 |

## 3. 本项目 AI 创作助手现状

### 3.1 执行骨架

本项目没有手写循环，循环由 LangChain `create_agent` + LangGraph 图承担，`AgentRuntime.execute` 只负责“一次投递跑完整张图”：

```
claim（冻结工具目录/预算/模型快照/租约）
  → build_chat_model（父 Run 账本回调）
  → create_agent（system_prompt + tools + 6 个 middleware + MySQL checkpointer）
  → graph.astream(durability="sync") → 逐事件发布
  → 有 interrupt → wait；否则取最后一条 AIMessage → finish
  → finally：销毁沙箱、释放租约
```

中间件顺序（`agent/runtime/agent.py`）：`ExecutionMiddleware → ContextMiddleware → ModelRetry → LLMToolSelector → SkillTools → ModelCallLimit → SkillSummarization`。最后一个由 `bound_model_middleware()` 用 `isinstance` 从中间挪到末尾，理由是“摘要要看到当轮真实包络”。

### 3.2 工具层

45 个 `ToolSpec` 注册在 `agent/tools/registry.py`。每个工具由三个文件构成：`<tool>.py`（实现 + SPEC）、`<tool>_description.py`（结构化描述：purpose / when_to_use / avoid / prerequisites / effect / returns / recovery / example）、registry 里一行 import。描述在 claim 时冻结进 `config_snapshot.assistant_settings.tool_descriptions`，已领取的 Run 沿旧描述执行。

授权是三层：

| 层 | 位置 | 作用 |
| --- | --- | --- |
| 目录裁剪 | `available_specs()` | 按 mode、`required_capability`、媒体/工作台/记忆开关过滤 |
| Run 冻结 | claim | `permitted_tool_names` + 目录正文写入快照 |
| 每次调用 | `ExecutionMiddleware.awrap_model_call` | 每轮把不在 `permitted_tool_names` 的工具从请求里剔除 |

业务工具还各自在 Service 层校验 scope、版本、主体权限。**授权的事实源在服务端，不在提示词**，这一点比教程强。

### 3.3 Skill 层

四层工具 + 双预算：

| 工具 | 行为 | 预算 |
| --- | --- | --- |
| `search_skills` | 分页检索，返回元数据 | 6000 tokens/页，描述 800 字符 |
| `load_skill` | 校验版本/模式/所需工具后固定版本，返回正文 | 同时最多 8 个活动方法，正文合计 8000 tokens |
| `read_skill_resource` | 只读已激活版本 manifest 内文件 | 按段读，返回 checksum 与范围 |
| `release_skill` | 释放自动方法（手选方法拒绝释放） | — |

目录本身不写在 system prompt，而是每轮由 `agent/context/graph.py` 作为一条 `task-context` 用户消息注入：默认 `limit=12`、`INDEX_TOKENS=2000`、描述截断 180 字符，超出部分靠 `search_skills` 翻页。手选方法记 `source=manual`，自动方法可被 `load_skill` 挤占释放，版本冻结后不许换版。

### 3.4 上下文与收口

- **前缀缓存**：`agent/context/prompt.py` 把稳定策略放 `instructions`，把每轮变化的任务状态做成 JSON 用户消息，并用 digest 判重——状态未变就不重发，保住前缀缓存。
- **预算**：`CONTEXT_TOKENS=65536`，`input_limit = 窗口 - 输出 - 4096(工具) - 2048(安全)`；超限直接抛 `agent_context_budget_exhausted`，不静默截断。
- **压缩**：LangChain `SummarizationMiddleware` + 自研 `BoundedSummary`（按 helper 窗口分批、前次摘要进下一批）；活动 Skill 正文组与 pinned 来源整体保留。
- **收口**：`graph.astream` 结束 → 有 interrupt 则 wait；否则要求最后一条是 `AIMessage` 且无 `tool_calls` 且正文非空，否则 `empty_agent_response`。**没有独立的完成校验，也没有 continuation**（`assistant-runtime-v17` 明确移除了 reviewer 与续执行）。

## 4. 逐模块对照

### 4.1 总表

| 机制 | Claude Code | 本项目 | 判定 |
| --- | --- | --- | --- |
| 主循环 | 手写 `while True`，30 行 | LangGraph 图 + 6 中间件 + executor | 形态不同 |
| 工具分发 | 扁平 `TOOLS` + `TOOL_HANDLERS` | 45 个 ToolSpec + 冻结描述 + 中间件过滤 | 更强，但边际成本更高 |
| 权限 | 三道闸门，执行前判定 | claim 冻结 + 每轮剔除 + Service 复核 | 更强 |
| Hooks | 4 个事件，外部注册，返回值即控制流 | LangChain 中间件，无注册表、无 Stop/UserPromptSubmit | 更弱 |
| 计划 | `todo_write` + 三轮提醒 | 无计划工具，只有 `plan_revision` 记账 | 更弱 |
| 子 Agent | fresh messages，只回最终文本，30 轮上限 | SubAgentMiddleware + 双向私有 state + 业务回执 | 更强 |
| Skill | 目录进 system prompt，正文按需 | 索引分页 + 冻结版本 + 双预算 + 释放 | 更强，但发现路径更长 |
| 压缩 | 四级递进，先卸载后摘要 | 预算硬拒 + 摘要中间件 + pinned 组 | 形态不同，缺“卸载”这一级 |
| 记忆 | `.memory` 索引 + 选择器调用 | MySQL L1–L5 + 7 个记忆工具 + 后台维护 | 更强 |
| 任务/账本 | 磁盘任务图 + 依赖 + 认领 | Task/Run/Operation/Turn 事务账本 | 更强（但不是同一件事） |
| 后台命令 | `run_in_background` + 通知注入 | 无（仅媒体生成异步等待） | 更弱 |
| 定时 | cron 调度器 | 无 | 形态不同（产品不需要） |
| 团队 | 持久队友 + 原子认领 + worktree | 仅一层子 Agent | 形态不同（产品不需要） |
| MCP | 工具池动态组装 + 宿主白名单 | 无 | 更弱（有意） |
| 目标闭环 | 独立判断器 + `defer` | 靠提示 + 预算 | 更弱 |

### 4.2 三个核心模块的深层差异

**（1）循环的所有权不同，导致“可解释性”成本不同**

CC 的循环是产品的一部分：读 30 行就知道继续/退出的全部条件。本项目的循环属于框架，产品只拥有“中间件 + executor”两层包裹。后果是：想知道“为什么会停”，要同时读 `create_agent` 的图构造、6 个中间件的 `awrap_*`、`executor.deliver()` 的终态分支。CR-02（`finish` 返回值被忽略）正是这种“退出路径分散”的典型症状——三条退出路径（succeeded / wait / failed）里有一条没有消费结果。

**（2）工具的“接口成本”被转移到了注册环节**

CC 加一个工具 2 行；本项目加一个工具至少 3 个文件 + 1 行 registry + 可能的 `trace_kind`/`trace_visibility` 映射。这不是退步，而是把成本从“运行时”挪到了“注册时”：换来的是描述可冻结、可按 Run 回放、可做结构校验。但代价是**任何新增都要跨 3 个文件**，且 `registry.py` 是单点冲突源（45 条 import + 45 条列表项）。CR-28（技能正文与工具组配对逻辑两份实现）说明这种分散已经开始产生重复。

**（3）Skill 的“发现”被拆成了两次调用**

CC 把目录放进 system prompt，模型第一轮就知道有什么；本项目把目录放进动态消息的前 12 条，第 13 条起必须调 `search_skills`。设计文档其实预见了这个问题（"小目录可注入有预算的元数据；大目录使用分类摘要、分页"），但实现只做了分页，没有做“常驻摘要 + 分页补充”的两段式。结果是：技能库超过 12 个后，**自动选择只能看到第一页**，而模型也不知道自己没看到的部分存在。

## 5. 架构差异的五个轴

| 轴 | Claude Code | 本项目 | 结论 |
| --- | --- | --- | --- |
| 失败模型 | 进程内，崩了重开 | 事务 + 租约 + 围栏，可恢复 | 本项目必须如此，不可简化 |
| 扩展方式 | 注册表（hook / handler） | 继承与组合（middleware） | 本项目缺少“不改类也能加规则”的入口 |
| 上下文策略 | 字符阈值，逐级降级 | token 预算，超限即拒 | 前者更鲁棒，后者更可预测 |
| 知识加载 | 目录常驻 + 正文按需 | 索引分页 + 正文按需 | 本项目多一次往返，少一份常驻成本 |
| 收口 | 独立判断器（可 `defer`） | 模型自判 + 预算上限 | 本项目省了调用，但少了兜底 |

一句话总结：**CC 优化的是“模型在宽松环境里的自主性”，本项目优化的是“副作用在严格环境里的可信度”。** 前者可以承受“跑错一次再重来”，后者不能承受一次错误写入。这决定了大部分差异是**刻意的**，不能照搬。

## 6. 可借鉴的改进点

按“收益/成本”排序。每项都给出可度量的验收指标，避免写成愿望清单。

### P0-1 显式收口契约：Stop gate + termination reason

| 项 | 内容 |
| --- | --- |
| 现状 | 退出条件散在 `executor.deliver()`：有 interrupt → wait；否则要求最后一条 `AIMessage` 无 `tool_calls` 且正文非空，否则抛 `empty_agent_response`。没有“为什么停”的记录，`finish` 返回值未被消费（CR-02）。 |
| CC 对照 | s04 的 `Stop` hook 允许“返回非 None 就继续”；s17 进一步把“没有 tool_use”降级为“这一轮想停”，完成与否另判。 |
| 改造 | 在唯一退出点加一个**确定性**闸门（不调模型）：检查 ① 无 `running` 状态的 Operation；② 无未结算的写批次；③ 无待消费输入；④ 上次写入有对应回执读取（可选）。任一不满足 → 注入一条固定格式的 system 消息并 `continue`，最多 2 次；否则 `finish`，并把 `{reason, at_turn, blocked_by}` 写入 `run.checkpoint.exit`，透出到 `RunRead`。同时消费 `finish` 返回值，`False` 时走 `accepted_unknown` 或明确失败。 |
| 收益 | 零额外模型调用，换来三件事：不会带着悬挂写入宣称完成；“为什么停”从日志考古变成一次查询；CR-02 顺手关闭。 |
| 成本/风险 | 闸门条件必须全部可从现有账本读取，不能引入新表；2 次上限防止死循环。 |
| 验收 | 构造“工具已开始未结算”与“finish 返回 False”两类用例，断言不发布成功终态且 `checkpoint.exit.reason` 正确。 |
| 优先级理由 | 与现有终态语义完全兼容，不改提示词、不改图结构，是唯一能同时改善正确性与可观测性的低风险改动。 |

### P0-2 大工具结果自动卸载：把“超限即失败”变成“可恢复读取”

| 项 | 内容 |
| --- | --- |
| 现状 | 单次请求按 `input_limit` 硬校验，超限抛 `agent_context_budget_exhausted`。工具结果只预留 4096 tokens；一次读取长剧本、长分镜或大 JSON 就可能直接判死整个 Run。Operation 里其实已经存了完整结果，只是没回到上下文。 |
| CC 对照 | s08 第一步 `tool_result_budget`：单条 > 30000 字符就整份落盘，上下文只留路径 + 2000 字符预览；后续可用 `read_file` 分段取回。 |
| 改造 | 在工具结果进入消息列表前做一次“体积检查”：超过阈值（建议 8k tokens）时，保留 `{operation_id, preview(前 N 字), truncated, next_offset}`，正文留在 Operation。模型用现有 `read_operation_receipt` / `read_task_results` 按段回读——不新增存储，不新增工具。 |
| 收益 | 单条大结果导致的 `agent_context_budget_exhausted` 预期归零；同等窗口下可处理的单份材料长度提升到原来的 3–4 倍；长剧本场景不再要求用户手动拆分。 |
| 成本/风险 | 预览长度与阈值需要按模型窗口自适应（已有 `context_profile`）；必须保证卸载后仍能精确回读，否则模型会“看见路径却读不到内容”。 |
| 验收 | 用一份 20 万字符的剧本文本跑一次读取，断言：请求不超限、`truncated=true`、按 `next_offset` 连续回读可拼回原文 checksum。 |

### P0-3 工具选择器失败降级，而不是判死 Run

| 项 | 内容 |
| --- | --- |
| 现状 | `LLMToolSelectorMiddleware(max_tools=16)` 每轮先用一次模型调用挑工具；选错名 → `ModelRetry` 重试 2 次 → `on_failure="error"` → Run 以 `invalid_agent_tool_selection` 失败。`FIXED_TOOLS` 的 11 个工具靠 `always_include` 兜底。 |
| CC 对照 | 不做工具筛选：`tools=TOOLS` 全量传入，把“选哪个”完全交给模型。s15 只做工具池组装（内置 + 已连 MCP），不做相关性裁剪。 |
| 改造 | 两条规则：① 可用工具数 ≤ 阈值（建议 18）时跳过选择器，直接全量传入；② 选择器最终失败时**降级为全量池**并记一条 warn 事件，而不是失败。 |
| 收益 | 小池场景每轮省一次模型调用（延迟与费用同时下降）；消除一整类失败终态；工具选择从“必经关卡”变回“优化手段”。 |
| 成本/风险 | 大池场景 token 会上升，需要用现有 `tool_schema_bytes` 指标对比；降级要有次数上限，避免持续用坏选择器。 |
| 验收 | 统计 `invalid_agent_tool_selection` 归零；对比开关前后单轮 `tool_schema_bytes` 与平均时延。 |

### P1-1 常驻能力地图：目录摘要进稳定前缀

| 项 | 内容 |
| --- | --- |
| 现状 | Skill 目录在动态 `task-context` 消息里，默认只给 12 条、描述截断 180 字符；第 13 条起只能靠 `search_skills`。模型不知道“还有没看到的”。 |
| CC 对照 | 目录（`- name: description`）常驻 system prompt，正文按需；目录的职责是“让模型知道有什么”，不是“给全量细节”。 |
| 改造 | 在稳定前缀里加一段**极小**的能力地图：`skill_id + 中文名 + ≤40 字用途`，按稳定键排序，硬上限 800 tokens；超出部分在末尾写明“另有 N 项，用 search_skills 检索”。全量目录仍保留在动态消息里，供手选/自动选择使用。库变更时用版本号进入前缀，避免每次编辑都击穿缓存。 |
| 收益 | 常见任务少一次 `search_skills` 往返；自动选择不再只看前 12 条；前缀只在库变更时失效，缓存命中率不降。 |
| 成本/风险 | 前缀多 800 tokens 常驻；需要一条“目录版本”参与 digest，否则每次启停技能都会重算前缀。 |
| 验收 | 对比“首轮是否发生 search_skills”的比例；对比前缀缓存命中率与单轮输入 tokens。 |

### P1-2 生命周期 hook 注册表：让顺序可声明、让规则可外挂

| 项 | 内容 |
| --- | --- |
| 现状 | 扩展点只有中间件类。加一条审计/护栏要改 `ExecutionMiddleware` 或新增类并在 executor 里接线；顺序靠列表位置，甚至用 `isinstance` 把 `SkillSummarizationMiddleware` 挪到末尾（`bound_model_middleware`）。缺 `Stop` 与 `UserPromptSubmit` 两个生命周期点。 |
| CC 对照 | 四个事件、外部 `register_hook()`、返回值即控制流（Pre 返回非 None 拦截，Stop 返回非 None 继续）。循环只调 `trigger_hooks()`，不知道具体规则。 |
| 改造 | 在 `agent/runtime/` 增一个薄 `lifecycle.py`：固定事件 `before_model / after_model / before_tool / after_tool / on_stop`，支持注册、显式 order、`abort(message)` 返回值；中间件改为“委托给注册表”，保留 LangChain 接口不变。先迁两条：权限复核（收敛 CR-23 的两份实现）、工具审计。 |
| 收益 | 顺序从隐式变显式且可单测；权限判定收敛到单点；新增审计/护栏不再触碰 executor；`on_stop` 正好承载 P0-1。 |
| 成本/风险 | 中间件与 hook 双轨并行期容易搞混“谁先谁后”，需要一张顺序表 + 一条断言测试。 |
| 验收 | 单测断言事件顺序；新增一个 hook 不改 executor 即可生效。 |

### P1-3 计划可见化：给长任务一个“不会漂”的锚

| 项 | 内容 |
| --- | --- |
| 现状 | 没有计划工具。长任务（搭 6 个分镜 → 逐个生成 → 采用）靠提示词和 `plan_revision` 记账，模型一旦被工具结果带偏，没有结构化回拉的锚点。前端只能看轨迹，看不到“还剩几步”。 |
| CC 对照 | s05：`todo_write` 只增加规划能力，不增加执行能力；三轮未更新就在工具结果里追加 `<reminder>`。 |
| 改造 | 加 `runtime__update_plan(steps[])`，写入 scope state（复用 `plan_revision`，不新建表），渲染进 ThoughtChain；连续 N 轮工具调用未更新时，在工具结果末尾追加一条固定提醒。计划是**投影**，不是事实源——完成状态仍以 Operation 回执为准。 |
| 收益 | 长任务漏项减少；用户获得真实进度而非仅“正在执行”；中断恢复后有明确续点。 |
| 成本/风险 | 最大风险是变成第二套事实源。约束：计划只描述“打算做什么”，不得用于判定“已经做了什么”。 |
| 验收 | 构造 10 步任务，断言中断后恢复能读出原计划且不重复已完成步骤。 |

### P2-1 后台命令 + 通知注入

| 项 | 内容 |
| --- | --- |
| 现状 | `execute` 是同步的（`timeout` 参数），长命令全程占用循环与租约；媒体生成本身已异步（`wait_generation`），但 ffmpeg、依赖安装、批量转码这类操作没有后台通道。 |
| CC 对照 | s11：标记 `run_in_background` 后先返回占位结果，完成后以 `<task_notification>` 注入下一轮；命令在独立进程组，退出时清理。 |
| 改造 | `execute(background=true)` → 返回占位结果 + 后台句柄；复用现有事件通道把完成/失败作为一条消息注入。沙箱已有取消与进程组清理，主要工作在“通知如何幂等回到会话”。 |
| 收益 | 长命令不再占租约、不再触发超时；模型可在等待期间继续读资料或推进其他分支。 |
| 成本/风险 | 通知乱序/重复投递必须有幂等键；Run 结束时仍有后台任务要有明确收尾策略。 |
| 验收 | 启动一个 10 分钟命令，断言主循环在 1 分钟内返回占位、完成通知恰好注入一次。 |

### P2-2 工具注册表生成化 + 结构测试

| 项 | 内容 |
| --- | --- |
| 现状 | 45 条 import + 45 条列表项集中在一个文件；`trace_kind`/`trace_visibility` 用 `replace` 事后补；新增工具要跨 3 个文件。 |
| CC 对照 | 工具 = schema + handler，同文件 10 行；加工具 = 2 行。 |
| 改造 | 保留“描述可冻结”的语义，但把注册改为按约定发现（每个 `tools/<group>/<name>.py` 暴露 `SPEC`），registry 只做汇总与去重；`trace_kind`/`trace_visibility` 移进 SPEC。补一条结构测试：每个 SPEC 必须有 description/effect/args_schema、名称唯一、`required_capability` 合法。 |
| 收益 | 消除单点冲突；漏注册在测试期暴露而不是运行期；工具契约一处可见。 |
| 成本/风险 | 纯重构，需一次全量回归；收益偏长期，故排 P2。 |
| 验收 | 结构测试通过；`registry.py` 行数下降且不再逐条 import。 |

## 7. 不建议借鉴

这四条不是“做不到”，而是“照搬会倒退”：

| 机制 | 为什么不借鉴 |
| --- | --- |
| 30 行无状态循环 | 本项目的价值在事务性副作用与多租户隔离；砍掉持久化等于放弃产品前提。CC 自己也在 s10 补了落盘任务。 |
| 字符串 deny list | `"rm -rf /" in command` 只能防住教学演示。本项目已有 claim 冻结 + 每轮剔除 + Service 复核，回到字符串匹配是降级。 |
| Skill 正文常驻 system prompt | 会击穿前缀缓存。本项目用“稳定 instructions + 动态 digest 消息”已经解决，方向正确，只需补常驻摘要（P1-1），不需要把正文搬回 system。 |
| LLM reviewer / continuation | 项目已明确移除（`assistant-runtime-v17`），理由是成本与不可靠。CC s17 的独立判断器是同一类方案；真正值得借鉴的是它的**出口设计**（`defer`、连续阻止上限、失败不伪装成功），而不是“再调一次模型”。确定性闸门（P0-1）足以覆盖 80% 的场景。 |
| worktree 任务隔离 | 本项目已有沙箱 scope + 独立工作区，且业务数据在 MySQL/MinIO，git 分支隔离解决的不是同一个问题。 |
| MCP 全量接入 | 权限面会从 45 个已知工具扩到不可枚举的外部工具。若要接，先学 s15 的两条：`mcp__server__tool` 命名空间 + 宿主维护只读白名单，绝不把 server 自述当授权。 |

## 8. 落地顺序与验证

| 批次 | 内容 | 依赖 | 验证 |
| --- | --- | --- | --- |
| 第一批（1–2 天量级） | P0-3 选择器降级 → P0-1 收口契约 | 无 | 单测覆盖两类异常终态；`invalid_agent_tool_selection` 归零 |
| 第二批 | P0-2 大结果卸载 | 需要 `read_operation_receipt` 支持按段读 | 20 万字符回读校验和一致；预算错误归零 |
| 第三批 | P1-1 能力地图 → P1-2 hook 注册表 | P1-1 需要目录版本号；P1-2 需要顺序表 | 首轮 search_skills 比例下降；hook 顺序单测 |
| 第四批 | P1-3 计划投影 → P2-1 后台命令 | 前端 ThoughtChain 需加计划节点 | 中断恢复读回原计划；后台通知幂等 |
| 随时 | P2-2 注册表生成化 | 独立重构，不阻塞其他 | 结构测试；回归全绿 |

与既有审查的衔接：P0-1 顺带关闭 CR-02；P1-2 顺带收敛 CR-23；P0-2 与 CR-05/CR-12（读路径分页）方向一致，但解决的是不同层的问题，不能互相替代。

## 9. 结论

1. **CC 的真正贡献不是代码，而是边界感**：它把“模型判断”和“harness 执行”切得很干净，因此每个机制都能单独讲清、单独替换。本项目在能力上远超教程，但在“边界是否一眼可见”上不如它——退出路径、规则顺序、目录可见性都散在多个文件里。
2. **大部分差异是刻意的，不该抹平**：持久化、围栏、授权、版本冻结都是产品前提换来的复杂度，回到 30 行循环是倒退。
3. **真正可迁移的是四个模式**：确定性收口（P0-1）、先卸载再摘要（P0-2）、失败降级而非判死（P0-3）、目录常驻正文按需（P1-1）。这四项都不增加模型调用，只改“失败时怎么办”和“模型能看见什么”。
4. **最该警惕的不是缺功能，而是缺入口**：本项目加一条规则的代价是改中间件类并调整列表顺序，这会让审计、护栏、观测这类“本该随手加”的东西持续欠账。P1-2 的 hook 注册表是杠杆最大的一项结构改动。

## 10. 验证范围与限制

- 本轮为静态对照分析：读完上述路径的文档与源码，未启动服务、未连数据库、未调用真实模型、未跑测试。
- 教程侧只精读了 s01/s02/s07 的完整代码，其余章节依据中文 README；旧 12 章（`docs/`、`agents/`）未逐行读，结论只针对根目录 s01–s17 主线。
- 文中“预期收益”均为基于现有实现路径的推断，未做线上度量；落地时应按第 8 节的验收指标实测后再判断是否保留。
- 未评估：真实长会话（万级事件）下的卸载阈值、多 Worker 并发下 Stop gate 的竞态、MCP 接入后的权限模型。这些需要独立验证。
