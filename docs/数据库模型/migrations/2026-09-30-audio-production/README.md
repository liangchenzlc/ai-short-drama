# 配音、字幕和配乐

前提：既有成片与时间轴迁移已完成。扩展 `ai_model_configs`、`async_tasks`、`media_assets` 的类型检查和 `media_files` 的 MIME/时长检查，使其接受 audio；新建 `episode_sounds`、`project_voice_defaults`、`sound_media_references`。

先备份，暂停新任务并在 `backend` 运行 `python scripts/sound_ddl.py --apply`。每个约束替换是 MySQL 8 原子 ALTER；新表使用存在检查，因此可重跑。迁移仅扩展允许值、创建新表，不删除旧数据。`001_audio_production.sql` 是对应的审阅/一次性执行版本。

部署匹配的 API、调度器、配音/render worker，创建私有音频桶并配置 FFmpeg、中文字幕字体，再启用 `AUDIO_PRODUCTION_ENABLED=true`。声音引用表保留采用及上传媒体，代理关联有真实外键；快照冻结原音频定位和校验值、混音设置及字体校验值。

回退时关闭开关、保留新表和音频；音频记录存在时不能缩窄旧约束。新声音快照不能交给旧 render worker。重复迁移和新旧数据兼容由独立 MySQL 测试覆盖。详见[制作功能说明](../../../production-features.md)。
