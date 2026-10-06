# 统一宿主模型运行配置

旧库升级新增 `ai_model_configs.runtime_profile JSON NULL`、`runtime_credentials_cipher MEDIUMTEXT NULL` 和 `canvas_channel_models.runtime_migrated_at DATETIME(3) NULL`，不新增表，完整建库仍为 93 张表。ORM、[完整 SQL](../../schema.mysql8.sql) 与[增量 SQL](001-host-model-runtime.sql) 同步；新库使用完整 SQL。

模型管理统一到宿主 `/ai_config`。公开协议/能力配置存 `runtime_profile`，API Key 继续只存原 `apikey` 加密信封，Secret Key 和自定义 headers 存第二个加密信封。`capability_cache` 为派生数据，不是配置真值。没有高级配置的旧标准模型保持原 adapter resolver 行为。

## 旧库执行

在 `backend/` 执行以下命令，使用现有 `.env` 中的连接和加密主密钥；不得更换主密钥或输出凭据。`uv` 不可用时可使用现有 `.venv/Scripts/python.exe`。

```powershell
uv run python scripts/host_model_runtime_migration.py --precheck
uv run python scripts/host_model_runtime_migration.py --apply
uv run python scripts/host_model_runtime_migration.py --precheck
uv run python scripts/host_model_runtime_migration.py --check-export
uv run python scripts/export_schema.py --check
```

部署前暂停旧版 API、scheduler 与相关模型写入进程，保留可恢复备份。三列新增 DDL 在 MySQL 中隐式提交；字段类型、nullable 或默认值不符时预检拒绝，不能自动覆盖已有列。中断后核对状态重入，只执行缺失列。运行同版 API/Worker 后才恢复模型管理写入，不能让旧 settings 客户端继续回写目录；本轮标准任务也支持冻结自定义 headers，旧 Worker 不能解析新的 `model_auth_*` 信封。

`--apply` 先执行缺失 DDL，再在单一事务中锁定旧目录、绑定和模型，校验所有待回填数据后写入：

- 保留原 `model_config_id`、所有者、模型编码、API Key、默认、启停/删除状态及旧 `channel::model` 绑定；不重建模型或改写节点、偏好、历史生成记录。
- 将旧 profile 的协议、能力、默认参数与逻辑规格转为宿主运行配置，将旧 headers/Secret Key 加密回填。
- API Key 以宿主模型行当前值为准。与旧 catalog 不一致时报告稳定 ID，不输出密钥，也不以旧密钥覆盖当前值。
- 首次回填推进模型 `row_version` 并清理派生缓存，避免旧结果按旧版本回写缓存，并在旧绑定记录 `runtime_migrated_at`。已回填绑定重入跳过，即使用户后来显式清空高级配置也不会恢复旧目录；不覆盖后续编辑、不重新加密、不再次推进版本。
- 已退场且缺少 profile 的软删除配置可跳过；活跃绑定缺失、能力类型不一致、凭据解密失败、非法数据或版本耗尽会拒绝，全部回填事务回滚。已执行 DDL 保留以便向前修复。
- 旧 catalog 作为兼容与企业目录同步数据保留，不再提供普通模型管理写入口。BeefAPI 的授权凭据继续由本人企业连接服务管理，HTTP 不能将普通模型伪装为托管模型。

SQL 只处理列，无法安全解密/加密旧数据。升级已有画布目录时必须执行 Python 回填，不可仅执行 SQL 后宣称数据升级完成。`--precheck` 检查真实结构；首次应用的回填计数与稳定 ID 差异应保存到部署记录中。

## 核验和恢复

核验三列类型、nullable、模型总数、ID、归属、默认、迁移标记和旧选择；配置列表与画布只能读取脱敏字段。验证更改地址、编码、密钥、headers、能力后的新任务，以及版本冲突和旧任务冻结配置。已有排队任务在首次提交前仍检查当前配置的启停/删除许可。

部署前发现异常可以修复配置并重跑；升级后不要删除新增列、重建模型 ID、恢复旧渠道写入口或以旧 catalog 覆盖新配置。历史任务使用原冻结信封，不能批量替换其中的密钥。

对应真实 MySQL 回归为 `backend/tests/integration/test_host_model_runtime.py`，包含 DDL 重入/类型不符、完整事务回滚、ID/默认/密钥保留、旧别名、凭据脱敏与清除、CAS 和偏好不能回写模型。测试只在随机临时库执行。

实际本机执行状态与本轮验证结果记录于[实施记录](../../../plans/2026-10-05-beeftv-implementation-log.md)，本说明不将任何部署环境默认视为已升级。
