# 候选隐私数据库执行记录

本次修改在本机 MySQL 8.4.11 的 `short_drama` 数据库执行。执行由主协作代理统一完成，先停止相关进程、备份数据库，再应用以下 DDL：

- `2026-10-03-private-candidates/001_private_candidates.sql`：为剧本和媒体增加 `published_at`，为素材图片候选增加 `created_by`，调整每人候选唯一键与作者索引。
- `20261003_agent_context.sql`：创建私有 Agent 附件、账号 Markdown 技能两表及对应索引、外键和 CHECK。

`backend/scripts/backfill_private_candidates.py` 已先 dry-run 后 `--apply`。提交统计：素材图片候选作者 8 行，媒体作者 23 行，小说生成记录作者 1 行，分镜生成记录作者 21 行，剧本作者 1 行；已采用媒体发布标记 10 行，剧本发布标记 1 行。再次执行结果为 `counts={}`。8 个原有服务角色已恢复。

本记录不包含凭据或业务正文。备份与执行报告保存在本机 `.runtime`，不纳入版本控制。当前完整 SQL 从 ORM 导出为 52 张表。数据库执行与证据回填不代表真实供应商调用验收；供应商调用仍由隔离测试替身验证业务状态。
