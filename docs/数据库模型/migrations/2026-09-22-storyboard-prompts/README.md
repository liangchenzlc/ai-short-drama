# 分镜建议时长与原文依据迁移

新库使用[完整建表脚本](../../schema.mysql8.sql)，不叠加执行本目录。旧库应先完成[生产工作流迁移](../2026-09-21-production-workflow/README.md)。项目要求 MySQL 8.0.21+，应用启动不自动迁移。

| 新增对象 | 当前定义 |
| --- | --- |
| `shot_scripts.duration_ms` | `INT UNSIGNED NOT NULL DEFAULT 3000`，建议镜头时长，单位毫秒 |
| `shot_scripts.source_excerpt` | `MEDIUMTEXT NOT NULL DEFAULT ('')`，生成镜头对应的连续剧本原文依据 |
| `ck_shot_scripts_duration_ms` | 强制执行 `CHECK (duration_ms BETWEEN 1000 AND 10000)` |

历史镜头统一使用默认建议时长 3000，原文依据留空；迁移不推测历史生成输入，不重写分镜正文、排序、归档、版本、素材关联或已采用媒体。建议时长与视频请求时长、文件实际时长分别保存。

## 执行顺序

1. 备份 `shot_scripts` 结构与数据，记录备份位置及时间。暂停 API、Worker 和其他镜头写入，选择目标库，使用 utf8mb4、UTC 和严格 SQL 模式。
2. 在同一个 MySQL 客户端连接中依次执行 [000_precheck.sql](000_precheck.sql)、[001_add_shot_duration_source.sql](001_add_shot_duration_source.sql)、[002_verify.sql](002_verify.sql)。`000` 记录临时行数基线；它只查询主版本号，运维仍须确认完整版本满足 8.0.21+。若表不存在或版本不满足要求，停止执行。
3. 若字段或同名 CHECK 已存在，执行 `SHOW CREATE TABLE shot_scripts`，核对类型、非空性、默认值、CHECK 表达式与 `ENFORCED=YES`；同名对象不会被迁移自动修正。最终校验会检查类型、主要默认值、启用状态、合法数据和行数，但不能替代完整结构比对。
4. `002` 成功返回 `storyboard prompt migration verified` 后，与[总 SQL](../../schema.mysql8.sql)核对完整定义，部署新版应用并恢复写入。

如果连接断开，临时基线丢失，必须与原备份行数核对后重新执行 `000`，不能把新基线当作未丢数据的独立证据。DDL 隐式提交，不支持整个目录事务回滚。

## 重入与恢复

`001` 只补缺失字段和 CHECK，重复执行不覆盖已有时长、原文依据或其他业务数据。中断后逐项确认已存在对象定义，再重新执行；已有非法时长会导致增加 CHECK 或核验失败，应先调查数据来源。

没有自动回滚脚本。发布失败时保持停写，保留备份和现场并向前修复；已写入新的时长或原文依据后，删除这些列会丢失业务数据。

隔离库测试覆盖升级后的结构与总 SQL 一致、旧数据保留、默认值、重复执行不覆盖新值，以及越界时长被数据库拒绝。运行方式见[迁移入口](../README.md)。
