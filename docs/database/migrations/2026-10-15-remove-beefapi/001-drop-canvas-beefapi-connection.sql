-- 2026-10-15 移除 BeefAPI 官方连接：删除本人设备授权表。
-- 功能已整体移除（连接/授权/钱包/目录同步与画布恢复轮询），表内仅含历史授权状态。
-- 执行前备份：frontend/canvas/.runtime/beefapi-removal/canvas_beefapi_connections.sql（1 行，状态 expired，无有效凭据）。
DROP TABLE IF EXISTS canvas_beefapi_connections;
