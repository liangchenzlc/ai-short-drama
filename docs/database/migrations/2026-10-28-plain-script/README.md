# 剧本纯文本结构

当前剧本编辑器只编辑纯文本。此迁移删除 `project_scripts.editor_document`、其 CHECK，以及 `project_script_revisions.before_document` / `after_document`；剧本正文、历史前后正文、并发版本、引用和行均不修改。

## 执行

新库直接使用[完整 SQL](../../schema/schema.mysql8.sql)。已有库先备份并在隔离库预演，确认不再需要旧富文本格式，再停写、排空进行中的正文改写任务，执行 [001-plain-script.sql](001-plain-script.sql)，并协调前端、API 和全部 Worker 同版启动。旧格式数据无法从删除后的列恢复；其他环境须自行核对旧列内容并决定如何保存。

MySQL DDL 隐式提交，两条 ALTER 不能整体回滚。执行前检查目标库、列和 `ck_scripts_editor_document` 均存在，执行后核对两表的列与约束、行数、正文和版本。中断时先核对实际结构，不要盲目重跑整个脚本。

## 本机执行记录

2026-10-28 在本机 `short_drama` 停写后执行，两表迁移前后均为 0 行；活动生成、Agent、渲染和记忆任务均为 0。执行后独立回读确认三个旧列及 CHECK 不存在，ORM 列检查通过。API、调度器和原有六类 Worker 已重启，API 健康检查通过。隔离 MySQL 中另验证迁移保留非空正文、前后快照与版本；其他库须独立检查和执行。
