# 候选与生成历史私有迁移

本次迁移将任务、批次、Agent 产物、图片候选、声音候选及导出历史限制为本人可读。项目成员只能读取明确采用后的业务作品，不取得作者的私有生成输入、错误、调用记录或模型信息。

## 执行顺序

1. 停止 API、Scheduler 和所有 Worker，等待正在受理的生成/渲染任务完成或显式处理。
2. 备份业务数据库与 MinIO，执行 `001_private_candidates.sql`。新库使用完整 `schema.mysql8.sql`。
3. 在 `backend/` 运行 `uv run python scripts/backfill_private_candidates.py` 查看证据回填与隔离统计，核对后使用 `--apply` 执行。
4. 执行 [Agent 附件与 Skill 增量](../20261003_agent_context.sql)，核验完整 SQL 与 ORM 一致，部署当前代码，再启动 API 与各 Worker。

回填入口：

```powershell
uv run python scripts/backfill_private_candidates.py
uv run python scripts/backfill_private_candidates.py --apply
uv run python scripts/export_schema.py --check
```

默认预览执行相同的证据判断后回滚，只输出分类行数；`--apply` 在单一事务中提交。重复执行不会替换已有作者或首次发布标记。脚本使用独立系统 Session，拒绝受账号权限过滤的会话，检测到 queued/running 生成与渲染、运行中批次、sent/unknown 调用时退出。归档素材图片候选还须有对应 `media_manifest` 的 `saved` 与 `candidate_status=linked` 证据，手动关联已发布图片不能凭媒体作者猜测候选创建人。

`episode_scripts.published_at` 和 `media_files.published_at` 记录作品发布状态。旧数据具有当前采用指针、已确认剧本、Agent 采用回执、镜头回收记录或已保存业务引用时才标记发布；实际旧采用时间无法证明时使用本次回填时间，不声称恢复了原采用时间。未来明确采用与业务指针写入处于同一事务，替换当前作品不会撤回已发布作品。

`asset_image_candidates.created_by` 保存候选创建人。唯一约束改为 `(created_by, asset_id, media_id)`，不同账号可独立将同一已采用图片加入候选，候选记录仍互相不可见。无法证明创建人的旧候选保持空作者并隔离。

## 历史归属

只根据可信的发起人或唯一、范围一致的任务关联回填。小说候选依据小说生成记录与任务；Agent 剧本依据对应产物创建人；生成媒体依据生成/导出任务。自动关联的素材生图候选必须与原生成来源素材一致。来源矛盾、缺失或未知作者的记录保持隔离，不默认转交项目主，不删除文件或数据库记录。

已采用作品可共享，原生成任务始终私有。共享镜头和音色只暴露稳定媒体 ID 与业务版本；当前成片通过独立作品投影与 `/assembly/current/download` 下载，不需要读取作者的导出任务。

迁移不会修改 `.env`、加密主密钥或任何供应商状态，也不调用真实模型。旧签名 URL 仍受原有效期约束；新读取和重新签发立即遵守新权限。
