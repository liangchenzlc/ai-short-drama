# 源码重构与退役创作数据清理

当前 ORM 为 119 张表。小说、大纲和小说转剧本来源记录已经退役，对应三张表删除。

本次环境已授权清空测试创作数据，不保留旧接口、归档或迁移兼容层。先停止 API、scheduler 和所有 Worker，在 `backend/` 执行：

```powershell
.venv/Scripts/python.exe scripts/reset_retired_creation_data.py
.venv/Scripts/python.exe scripts/reset_retired_creation_data.py --execute
.venv/Scripts/python.exe scripts/export_schema.py --check
```

默认命令只输出待清理表的行数。执行命令在一个事务内清理作品、素材、分镜、生成、助手、草稿与画布业务记录，校验账号认证、模型与偏好、Skill 全部版本与设置、画布模型和风格配置的指纹保持一致，再删除退役表。风格配置引用的媒体一并保留；Skill 资源直接引用 MinIO，本脚本不删除任何对象。

DDL 可重跑；如果业务行事务已提交而 DDL 中断，重跑执行命令完成表清理。不用于含真实生产业务数据的数据库。
