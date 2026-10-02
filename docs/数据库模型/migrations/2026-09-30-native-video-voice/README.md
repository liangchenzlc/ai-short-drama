# 原生视频与角色音色增量迁移

依赖既有生成任务、声音制作及分镜视频迁移。新增 `project_sound_modes`、`character_voices`、`shot_dialogues`，不更新或删除历史业务数据。旧项目没有模式记录时仍为 `legacy`。

发布前保持 `NATIVE_VIDEO_ENABLED=false`，暂停相关写入并备份。安装 backend 依赖后，在 backend 目录运行：

```powershell
.venv/Scripts/python.exe scripts/native_voice_ddl.py
.venv/Scripts/python.exe scripts/native_voice_ddl.py --apply
```

第一条只打印 DDL；第二条对当前配置数据库创建缺失表，可重复运行。SQL 版本见 [001_native_video_voice.sql](001_native_video_voice.sql)。`checkfirst` 不修正已经存在但定义错误的表；应核对列类型、主键、全部命名外键、RESTRICT 和 CHECK 约束。

部署同版 API 和全部 Worker 后，启用 `AUDIO_PRODUCTION_ENABLED`、`NATIVE_VIDEO_ENABLED`。首个项目检查模式、角色样音版本、对白保存和失败恢复。新增表已进入完整 schema；隔离 MySQL 迁移测试验证重复执行。

回退时保留新增表与媒体记录，不删除角色样音。暂停生成、导出和批次派发后再回退应用；已有 native 项目不可用旧版后期配音逻辑继续导出。旧成片文件和下载不受本迁移影响。

本轮仅在隔离测试库验证，未对业务数据库执行本迁移。
