# 画布风格与兼容播放升级

日期：2026-10-16。适用已完成无限画布基础存储及 016 本地处置迁移的旧库。
新增完整画布风格存储，允许异步 H.264/AAC 兼容播放；标准模式文字画风保持原行为。

## 执行方式

在停写窗口核对目标库、现有定义并准备可恢复备份。MySQL DDL 隐式提交，不能整体回滚。
命令在 `backend/` 执行，Settings 从当前目录读取已有 `.env`，不要覆盖配置。

```powershell
uv run python scripts/canvas_alignment_migration.py
uv run python scripts/canvas_alignment_migration.py --apply
uv run python scripts/canvas_alignment_migration.py
uv run python scripts/export_schema.py --check
```

默认只检查并列出待执行语句。`--apply` 显式执行；已存在的三表核对列类型/可空性、索引、
CHECK 和外键，不以名称存在代替结构一致。只接受精确旧 CHECK，未知结构停止并保留现场。
完成后再次运行应返回 `statements: []`。部署同版 API、scheduler 与 render Worker 后恢复写入。

[001-styles-playback.sql](001-styles-playback.sql) 是审阅用完整增量，包含 CREATE 和 ALTER，
直接运行不幂等；已有部分结构请使用上述脚本按缺项执行。新库使用
[schema.mysql8.sql](../../../database/schema/schema.mysql8.sql)，不用叠加本增量。
`--write-sql` 仅按 ORM 更新增量文件，不连接数据库，不等于完成迁移。

## 实际执行记录

2026-10-16 在本机业务库 **`short_drama`** 已实际执行以下结构更新：

1. `ALTER TABLE canvas_local_jobs DROP CHECK ck_canvas_local_type, ADD CONSTRAINT ...`，
   允许 `timeline_transcription`、`depth_capture`、`resource_playback`。
2. `ALTER TABLE canvas_local_jobs DROP CHECK ck_canvas_local_output, ADD CONSTRAINT ...`，
   深度/兼容播放成功时要求输出媒体，保留现有处置回执例外。
3. `CREATE TABLE canvas_style_profiles`、`canvas_style_media_references`、
   `canvas_style_preferences` 及 `CREATE INDEX idx_canvas_personal_styles`。

准确表达式与表定义见增量 SQL。回读脚本返回
`{"database":"short_drama","applied":false,"statements":[]}`。
实际信息模式表数和规范 SQL 均为 **104**（移除 BeefAPI 后 101，再增加三表）。
没有回填个人风格、重写标准文字画风或转码旧原件；风格由用户选择导入或保存，兼容副本按需准备。

## 数据与运行

个人风格原件按账号隔离，来源快照按项目共享，引用媒体通过 RESTRICT 外键保护。
CAS 版本对外为十进制字符串，个人软删除不改变作品快照。来源快照只允许同项目已发布媒体。
系统预设收藏与最近使用按账号保存。本机旧风格须由用户明确勾选幂等导入。

兼容播放复用本地任务表、FFmpeg/ffprobe、MinIO 和 render Worker，原文件不替换。
写对象前记录持久 staging 定位，调度器清理取消、租约中断或崩溃遗留的未登记对象；
已有 MediaFile 登记的输出不会误删。云端转写只存配置引用和提交标记，不保存解密凭据，
未知受理不恢复重发。

已在独立临时 MySQL 的随机 `_test` 库执行风格、兼容播放、转写准入与迁移测试，MinIO
使用隔离测试桶；真实 HEVC 转 H.264/AAC、Range/HEAD、取消清理及来源权限有自动验证。
这些结果不代表真实语音供应商、深度模型、AMQP 全链路或完整一比一验收。
逐项结果见[实施方案](../../../plans/2026-10/2026-10-16-画布BeefTV对齐实施.md)。
