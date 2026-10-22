# Agent 无 Embedding 记忆系统设计

状态：历史设计草案，已被修订版替代，尚未实施。日期：2026-10-21。

> 当前方案以 [Agent 无 Embedding 记忆系统设计（修订版）](2026-10-21-Agent记忆系统设计修订版.md) 为准。以下保留原始讨论内容，不作为当前实施依据。特别是统一 project-scoped、固定目录行数、新消息导致摘要失效、目录超限标记 superseded，以及未来自动引入向量检索等规则，均已在修订版调整。

本文只描述设计与结论。选型依据与否决理由见 [记忆系统存储与分层决策](2026-10-21-Agent记忆系统存储分层决策.md)。

## 1. 结论

- 五层记忆全部存储于 MySQL，不引入 PostgreSQL、pgvector 或任何 embedding 服务。
- 召回方式为「索引常驻 + 正文按需读取」，索引每轮注入提示词，正文由模型通过工具按需读取。
- 稳定内容（系统提示词、已确认约束、记忆索引）置于提示词前缀，以命中提示词缓存。
- 压缩采用后台预计算摘要 + 单一水位线，保留近期原文与完整任务状态。

## 2. 五层结构

| 层 | 内容 | 存储 | 读取时机 |
| --- | --- | --- | --- |
| L1 原始记录 | 完整消息、引用、工具结果摘要 | `agent_messages` | 不自动进提示词，按需回查 |
| L2 近期上下文 | 水位线之后的完整消息 | L1 查询，不落新表 | 每轮 |
| L3 任务状态 | 目标、约束、已确认决策、待办 | `fixed_requirements` + run checkpoint | 每轮必进 |
| L4 会话摘要 | 分段摘要与阶段结论 | 新表 `agent_conversation_summaries` | 覆盖区间内替代原文 |
| L5 长期记忆 | 偏好、纠正、作品决策、参考来源 | 新表 `agent_memory_items` | 索引每轮，正文按需 |

## 3. 提示词组装顺序

顺序决定缓存命中率，不可随意调整。

**稳定区（前缀，缓存命中区）**

1. 系统提示词
2. `fixed_requirements`：用户显式确认的约束
3. L5 记忆索引：`kind + title`，每行一条，上限 150 行
4. 作品静态设定摘要

**动态区**

5. L4 摘要：当前已压缩区间的结论
6. L2 近期消息：水位线之后的原文
7. 当前用户输入

稳定区内任何内容变动都会使缓存失效，因此记忆写入必须低频批处理，不得每轮写入。

## 4. 表设计

### 4.1 会话摘要表

表名 `agent_conversation_summaries`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | BIGINT UNSIGNED | 主键 |
| `conversation_id` | BIGINT UNSIGNED | 所属会话，外键 |
| `from_seq` | BIGINT UNSIGNED | 起始消息序（含） |
| `to_seq` | BIGINT UNSIGNED | 结束消息序（含） |
| `version` | INT UNSIGNED | 同一区间的摘要版本 |
| `content` | TEXT | 摘要正文 |
| `token_count` | INT UNSIGNED | 估算 token，用于预算 |
| `status` | VARCHAR(16) | `pending` / `ready` / `stale` |
| `model_config_id` | BIGINT UNSIGNED | 生成所用模型 |
| `created_at` / `updated_at` | DATETIME(6) | 时间戳 |

约束：唯一键 `(conversation_id, from_seq, to_seq, version)`；`status` 取值受 CHECK 约束。

`agent_conversations` 新增 `summary_covers_until_seq BIGINT UNSIGNED NOT NULL DEFAULT 0`。

### 4.2 长期记忆表

表名 `agent_memory_items`。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | BIGINT UNSIGNED | 主键 |
| `owner_user_id` | BIGINT UNSIGNED | 归属用户，外键 |
| `project_id` | BIGINT UNSIGNED | 所属作品，外键 |
| `kind` | VARCHAR(16) | `user` / `feedback` / `project` / `reference` |
| `title` | VARCHAR(80) | 索引行文本，一行一句，不超过 40 字 |
| `content` | TEXT | 正文，不超过 2000 字 |
| `source_message_id` | BIGINT UNSIGNED | 来源消息，可空（用户手写时为空） |
| `supersedes_id` | BIGINT UNSIGNED | 被本条取代的旧记忆，可空 |
| `forgotten_hash` | CHAR(64) | 遗忘墓碑指纹，可空 |
| `hit_count` | INT UNSIGNED | 被读取次数，默认 0 |
| `last_used_at` | DATETIME(6) | 最近被读取时间，可空 |
| `modified_at` | DATETIME(6) | 内容最近修改时间 |
| `status` | VARCHAR(16) | `active` / `superseded` / `forgotten` |

索引：

- `(owner_user_id, project_id, status, kind, last_used_at)`：索引生成与淘汰
- 唯一键 `(owner_user_id, forgotten_hash)`：墓碑查重

作用域为 project-scoped：同一作品的所有会话共享同一批记忆，会话只是写入来源，不是可见范围。

### 4.3 记忆分类标准

| kind | 记什么 | 不记什么 |
| --- | --- | --- |
| `user` | 用户角色、专长、长期写作偏好 | 单次任务的临时要求 |
| `feedback` | 用户纠正过的做法、明确否定的方向 | 用户未表态的领域 |
| `project` | 作品设定决策、创作方向、数据推不出的约定 | 可从业务表直接查到的事实 |
| `reference` | 外部参考、素材来源、资料位置 | 内容本身（只记位置） |

## 5. 写入流程

由 Celery 在 run 进入终态后触发，不阻塞对话。

1. 取本次 run 新增的消息。
2. 调用模型抽取候选记忆，结构化输出，Pydantic 校验。
3. 逐条过滤，命中任一条即丢弃：
   - 可从 `project_novels` / `shot_scripts` / `assets` 直接查询的事实
   - `fixed_requirements` 已覆盖的约束
   - `forgotten_hash` 命中的墓碑
4. 冲突检测：同 `kind` 且语义等价的 `title` 已存在时，新建记录并写 `supersedes_id`，旧记录置 `superseded`。
5. 写入 `status='active'`。
6. 索引行数超过 150 时触发淘汰。

淘汰顺序：`superseded` 优先，其后按 `hit_count` 升序、`last_used_at` 升序。淘汰不删除记录，只置 `status='superseded'`。

## 6. 读取流程

每轮组装提示词前执行。

1. 查询 `status='active'` 的记忆，按 `kind` 分组，按 `last_used_at` 倒序。
2. 生成索引行：`kind + title`，上限 150 行，超出按第 5 节淘汰顺序裁剪。
3. 索引注入稳定区。
4. 模型按需调用 `read_memory(id)` 读取正文，命中后 `hit_count + 1` 并更新 `last_used_at`。
5. 单次 run 内读取上限 5 条，超限返回明确提示而非静默拒绝。

`read_memory` 走现有 `tools.py` 的 `_execute` 分支，必须校验 `owner_user_id` 与 `project_id` 归属，归属不匹配按不存在处理。

## 7. 压缩机制

### 7.1 水位线

`summary_covers_until_seq` 是 L2 与 L4 的唯一分界。

- L2 起点固定为 `summary_covers_until_seq + 1`
- 摘要写入与水位线推进在同一事务内完成
- 不允许两条规则各自判断边界

### 7.2 生成流程

1. 触发条件：水位线之后的原文 token 超过预算，或消息数超过阈值。
2. 取区间：`summary_covers_until_seq + 1` 至 `next_message_seq - 保留窗口`。
3. 后台生成摘要，`status='pending'`，不阻塞对话。
4. 完成后置 `ready`，同事务推进水位线。
5. 生成期间到达新消息时，已 `ready` 但区间不完整的摘要置 `stale`，不得启用。

### 7.3 失败处理

摘要生成失败时水位线不推进，继续使用原文窗口。不得以旧摘要冒充新摘要，也不得因摘要缺失中断对话。

## 8. 配额

| 项 | 上限 | 超限行为 |
| --- | --- | --- |
| L5 索引行数 | 150 行 | 按淘汰顺序裁剪 |
| 单条记忆正文 | 2000 字 | 抽取时拒绝，要求拆分 |
| 单次 run 读取记忆 | 5 条 | 返回提示，不再读取 |
| L3 任务状态 | 600 token | 按优先级截断，保留已确认决策 |
| 单条摘要正文 | 800 token | 分段生成 |
| 记忆总条数 | 建议 500 条 | 超出时提示清理，仍可继续使用 |

## 9. 降级

| 故障 | 行为 |
| --- | --- |
| 摘要生成失败 | 使用原文窗口，水位线不推进 |
| 记忆查询失败 | 跳过索引注入，对话继续 |
| `read_memory` 超时 | 返回不可用，对话继续 |
| 抽取任务失败 | 记录失败原因，下轮重试，不重复抽取已成功项 |

任何记忆子系统的故障都不得阻断对话主流程。

## 10. 实施顺序

1. **L3 任务状态**：区分 `fixed_requirements`（用户确认）与 checkpoint（模型提议），在提示词中标注来源。
2. **L4 摘要**：新增摘要表、水位线字段、后台预计算任务、`stale` 判定。
3. **L5 记忆**：新增记忆表、索引注入、`read_memory` 工具、抽取与淘汰任务、墓碑。
4. **观测**：统计索引行数、记忆读取次数、用户纠正频率、摘要命中率。

第 3 步完成后按第 12 节条件判断是否需要引入向量检索，默认不引入。

## 11. 与现有代码的衔接

| 位置 | 改动 |
| --- | --- |
| `agent/runtime.py` 提示词装配 | 按第 3 节顺序插入索引与摘要，调整稳定区位置 |
| `agent/tools.py` `_execute` | 新增 `read_memory` 分支，校验归属 |
| `ai/prompts/` | 新增记忆抽取与摘要提示词 |
| `agent/state.py` | run 终态后投递抽取任务 |
| `tasks/` | 新增摘要预计算与记忆抽取两个任务 |
| `schemas/` | 新增记忆与摘要的 Pydantic 模型 |
| 前端 | 记忆列表、编辑、删除入口 |

表结构变更按项目约定补齐三处：ORM 模型、真实库 DDL、`docs/database/schema/schema.mysql8.sql`。

## 12. 引入向量检索的条件

仅在下列任一条件成立时评估，届时只新增一张向量表，`agent_memory_items` 仍为唯一事实源：

1. 单项目记忆超过 500 条，索引无法纳入上下文预算。
2. 用户表述与记忆用词系统性不匹配，标题匹配失效。
3. 出现跨项目检索需求。

未出现上述条件时，不引入 embedding。

## 13. 设计来源

### 13.1 借鉴部分

| 设计点 | 来源 | 本项目改动 |
| --- | --- | --- |
| 索引常驻 + 正文按需读取 | Claude Code `MEMORY.md`（前 200 行或 25 KB）+ 主题文件按需读 | 文件系统与文件工具 → 数据表与 `read_memory` 工具 |
| 四类记忆分类 | Claude Code（`user` / `feedback` / `project` / `reference`） | 分类标准原样沿用，取舍标准改写为业务可查即不记 |
| 跳过可推断内容 | Claude Code（不记架构、文件路径、调试修复） | 映射为"可从业务表查询的事实不记" |
| 索引硬上限 | Claude Code 200 行 / 25 KB、Codex `project_doc_max_bytes` 32 KiB | 150 行，超限淘汰合并而非截断 |
| 压缩后稳定内容重新注入 | Claude Code（压缩后根 `CLAUDE.md` 从磁盘重读） | 稳定区前置，缓存命中区 |
| 摘要与原文分离 | Codex（`/compact` 与 `resume` 为两个独立动作） | 由水位线串联 |

### 13.2 本项目自研部分

以下机制在 Codex 与 Claude Code 中没有对应物：

- 单一水位线 `summary_covers_until_seq`
- 摘要 `stale` 状态
- `supersedes_id` 版本链
- `forgotten_hash` 遗忘墓碑
- L3 任务状态双来源

自研原因：两家的记忆写入均由模型在会话过程中主动完成，写入时机与内容由模型当次判断；本项目采用会话结束后由 Celery 批量抽取。批量抽取会重复生成用户已删除的内容，因此必须有墓碑；异步预计算与对话推进并发，因此必须有 `stale` 与单一水位线。这两类问题在两家的同步写入模型下不存在。
