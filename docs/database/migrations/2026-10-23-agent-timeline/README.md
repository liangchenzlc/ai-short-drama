# agent 时间线：item 顺序与消息类型

**日期：** 2026-10-23
**类型：** 加列 + 回填
**影响：** 三张表加列，历史消息与操作按时间顺序回填 `item_seq`，不删除业务数据。

AI 创作助手的时间线需要在同一条序列里交错展示助手叙述与任务步骤。消息原先只有会话内 `seq`，操作只有 `created_at` 与雪花 ID，两者无法稳定比较，因此新增会话级单调序号。

| 表 | 变更 | 用途 |
| --- | --- | --- |
| `agent_conversations` | `next_item_seq` | 会话级发号器，消息与操作共用 |
| `agent_messages` | `item_seq`、`kind` | 排序键；`kind` 区分 `reply` / `narration` / `notice` |
| `agent_operations` | `item_seq` | 与消息共用序列，保证「说一句 → 做一步 → 再说一句」的顺序 |

## 执行

按[数据库说明](../../../database/README.md)的迁移方式，用具备 DDL 权限的账号连接目标库后整体执行 `up.sql`。语句顺序固定为：加列 → 回填消息 → 回填操作 → 初始化发号器；回填使用窗口函数按 `(created_at, id)` 在每个会话内统一编号，历史会话因此与新任务渲染一致。执行后用下面的查询核验：

```sql
SELECT (SELECT COUNT(*) FROM agent_messages WHERE item_seq IS NULL) AS messages_missing,
       (SELECT COUNT(*) FROM agent_operations WHERE item_seq IS NULL) AS operations_missing,
       (SELECT COUNT(*) FROM agent_conversations WHERE next_item_seq < 1) AS bad_counters;
```

三项均应为 `0`。

## 回滚

```sql
ALTER TABLE `agent_operations` DROP INDEX `idx_agent_operations_item`, DROP COLUMN `item_seq`;
ALTER TABLE `agent_messages` DROP CHECK `ck_agent_messages_kind`, DROP INDEX `idx_agent_messages_item`, DROP COLUMN `kind`, DROP COLUMN `item_seq`;
ALTER TABLE `agent_conversations` DROP COLUMN `next_item_seq`;
```

回滚只丢弃展示顺序，不影响业务字段。
