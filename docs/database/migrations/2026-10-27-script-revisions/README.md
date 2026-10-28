# 剧本变更记录增量

新增 `project_script_revisions`，保存 AI 替换、整篇导入、助手明确采用及恢复的前后正文／格式、来源、版本和操作者。普通自动保存不产生逐键历史。新库的[完整 SQL](../../schema/schema.mysql8.sql)已包含该表，当前共121表。

## 执行

适用已完成单层创作模式及[剧本工作台增量](../2026-10-27-writing-workspace/README.md)的旧库。先备份，在隔离库预演，暂停写入并部署同版 API、文本／Agent Worker 与前端。在 `backend/` 执行：

```powershell
uv run python scripts/script_revision_migration.py --apply
```

脚本只增加新表，已存在时核对列类型／可空、主键、唯一约束、父外键和版本 CHECK；结构不一致则拒绝继续。不会回填过去的操作，不会修改已有正文、剧本指针或业务版本。也可由管理员审阅并执行 [001_script_revisions.sql](001_script_revisions.sql)，随后用脚本重入核验。应用启动不会自动迁移。

恢复不是将并发版本回退：使用当前 `content_version` 核对后产生新版本，并再保留恢复前正文。回滚部署前须停写，保留新表及快照；不要直接删表丢失用户恢复记录。

## 本机执行证据

2026-10-27 已在本机 `short_drama` 实际执行 `--apply`，再次执行通过，表结构就绪；没有回填或修改业务正文。完整建表已重新导出为121表。隔离 MySQL 的新增表／重入用例与正文恢复用例见[整改验收](../../../reviews/2026-10-27-创作工作台与助手缺陷整改验收.md)。

本次 `agent_migration.py --precheck` 另返回 `ready`、`gaps=[]`。这是当前结构的只读证据，不代表本次重新执行了历史 Agent SQL，也不代表真实模型供应商验收。
