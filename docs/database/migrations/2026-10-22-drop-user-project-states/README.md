# drop user_project_states

**日期：** 2026-10-22  
**类型：** 删表  
**影响：** 仅删除废弃表，无业务代码使用，无外键被其他表引用。当前 `short_drama` 已执行（删除前 0 行），执行后完整结构为 116 张表。

`user_project_states` 记录用户最近打开的项目，ORM 类 `UserProjectState` 已从 `domain/collaboration.py` 移除，db/access.py 和 db/readiness.py 中的引用已同步清理。

## 执行

```sql
-- up.sql
DROP TABLE IF EXISTS `user_project_states`;
```

## 回滚

表无外键约束，回滚需重建：

```sql
CREATE TABLE `user_project_states` (
  `id` bigint unsigned NOT NULL,
  `user_id` bigint unsigned NOT NULL,
  `project_id` bigint unsigned NOT NULL,
  `last_opened_at` datetime(6) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_user_project_state` (`user_id`,`project_id`),
  KEY `fk_ups_project` (`project_id`),
  CONSTRAINT `fk_ups_user` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`),
  CONSTRAINT `fk_ups_project` FOREIGN KEY (`project_id`) REFERENCES `projects` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci ROW_FORMAT=DYNAMIC;
```
