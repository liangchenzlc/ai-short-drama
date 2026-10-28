# 剧本创作工作台增量

新增 `project_story_outlines`（每项目唯一、MEDIUMTEXT、审计字段、项目外键），当前完整 schema 为 **120 张表**。不增加场次、草稿或用户版本表。已有小说、剧本、任务及候选历史保留。

## 执行

先停止新写作提交并排空旧 `novel_script` 任务，再暂停相关 API、scheduler 和文本 Worker，备份数据库。确认 `backend/.env` 指向目标库，在 `backend/` 执行：

```powershell
uv run python scripts/writing_workspace_migration.py --apply
uv run python scripts/export_schema.py --check
```

脚本先幂等建表，再在项目锁下规范标准模式、未归档、已有已发布当前剧本的确认状态：非空正文成为唯一确认稿；空正文取消可用状态。未采用 Agent 候选排除，不自动选取旧稿或创建发布证据。仅状态确实变化时推进项目 `content_version` 并更新时间，重跑不再写入。无当前稿、未发布稿及其他模式不回填。

[001_story_outline.sql](001_story_outline.sql)仅负责建表；只执行 SQL 不会完成状态规范。新库直接使用[全量 schema](../../schema/schema.mysql8.sql)，不叠加此建表语句。部署同版 API、文本 Worker 和前端后恢复提交；旧候选接口仍保留，AI 创作助手无须改造。

MySQL DDL 隐式提交；失败后核查现有表与数据再重入，不删除表。优先向前修复。回退旧代码前停止新写作任务，保留大纲表与新写入正文；若需恢复迁移前的业务状态，使用同点备份，不以删除新增表代替恢复。

## 本机执行与验证

2026-10-27 在 `short_drama` 实际运行 `--apply` 并导出全量 schema，大纲表已创建。初次脚本检查了 1 份已发布当前稿；后续脚本改为只统计实际变更，重入返回 0。`export_schema.py --check` 通过，共 120 表。隔离 MySQL 测试验证非空／空白历史稿状态规范、并发版本推进与重入零写入。真实队列联调使用 RabbitMQ、Celery 文本 Worker 与本地 HTTP 模型替身，不代表收费供应商效果验收。
