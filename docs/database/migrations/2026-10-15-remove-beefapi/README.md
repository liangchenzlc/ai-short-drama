# 移除 BeefAPI 官方连接（2026-10-15）

## 背景

BeefAPI 官方连接功能（设备授权、钱包、官方模型目录同步与后台恢复轮询）已整体移除；本迁移删除对应存储表。

## 执行内容

- `001-drop-canvas-beefapi-connection.sql`：`DROP TABLE IF EXISTS canvas_beefapi_connections;`

## 本机执行记录（2026-10-15）

- 目标库：本机 Docker `short_drama`。
- 执行前：`canvas_beefapi_connections` 1 行（状态 `expired`，无有效凭据）；已备份至 `frontend/canvas/.runtime/beefapi-removal/canvas_beefapi_connections.sql`。
- 执行后核验：`SHOW TABLES LIKE 'canvas_beefapi%'` 无结果；全库表数 102 → 101。
- `canvas_workspace_user_states` 当时为空表，无 `editor_tool_states` 偏好键残留需要清理。
- `export_schema.py` 已按当前 ORM 重新导出规范 SQL（101 张表），`--check` 通过。
- 其余环境仍需独立核对。
