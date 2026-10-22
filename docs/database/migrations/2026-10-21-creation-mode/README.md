# 2026-10-21 创作模式单层项目切换

本版移除 `episodes`、三层素材关联、成员和邀请，业务对象直接归属项目；删除项目梗概／画幅／风格，保留画布生命周期使用的 `projects.archived_at`。完整新库结构见 [schema.mysql8.sql](../../../database/schema/schema.mysql8.sql)，离线操作与恢复见[创作模式维护说明](../../../instruction/创作模式维护说明.md)。本次使用允许清空业务测试数据的专用重建流程，不能用于保留旧作品的升级。

执行工具为 `backend/scripts/creation_mode_reset.py`。工具在停止写入后导出完整数据和对象，在随机隔离库恢复旧库并重建新结构，验证保护数据摘要及全部外键后才允许原库切换。每条实际执行 DDL 保存在备份目录的 `applied-ddl.sql`；完整 SQL 从当前 Domain 导出，不以应用启动自动迁移。

## 实际执行

2026-10-21 已对当前使用的 `short_drama` 执行离线重建。源码与配置快照位于 `.runtime/creation-mode/20261021-115354/`；原库及 64 个引用对象完整备份位于 `.runtime/creation-mode/database-20261021/`，没有缺失对象。完整数据摘要为 `eea4a2ffa7c3bc4d3fcda706e637efdb817747ea336007dfc29d6c506772a035`。

在随机隔离库完成同点恢复、重建和外键检查后，已在原库实际执行 DROP／CREATE、索引和延后外键 DDL，共 101 张表；执行语句保存在备份目录 `applied-ddl.sql`。原始切换 SQL 摘要为 `e157c3471719748f4a7890f5415df1fc9f1fa99e3fd75b003d2e35258111fd34`，原始 `applied.json` 保持不变。

随后实际执行 14 条注释 DDL，将旧分集术语改为项目；保留生成列定义、类型、默认值和约束。语句与执行校验分别位于 `.runtime/creation-mode/comment-ddl.sql`、`comment-ddl-applied.json`。最终 canonical SQL SHA-256 为 `ac73603bc52d70cc179ad5ca55fb94cba3493606d452e9d3154b67f1fa90d7a6`，与 Domain 导出检查一致。

原账号、模型配置和会话分别保留 1、3、6 条，持久保护数据摘要一致；登录限流计数因冒烟正常更新，不纳入切换后的静态摘要比较。注释 DDL 前后全部业务数据摘要一致，全部外键有效。真实运行验证使用两个独立临时账号，结束后仅删除这些账号及其明确外键后代和媒体对象；清理后项目、媒体和异步任务均为零，没有更改原账号密码或会话。

API、scheduler 及 text／image／video／audio／render／agent Worker 使用统一的新 `GENERATION_QUEUE_NAMESPACE`，各角色继续使用不同 Snowflake worker ID；原命名空间保留，不重发旧任务。验收后再次停机，仅从备份清单删除当前已无引用的 64 个旧对象，未清空 bucket。备份仍可用于恢复。

项目未采用内容迁移策略；原作品、历史任务、草稿、对话和画布内容已清空。34 条历史 unknown 调用记录包含在同点备份中，不自动重发。原个人画风为零；按用户复制依赖媒体的闭包另有自动测试覆盖。

## 验证与限制

- 全量 fast：3,699 项通过；随后新增的邀请退役、渲染任务代理及导入视频跨请求读取回归已分别通过定向验证。测试清单检查通过，登记 502 个测试文件。
- 前端两包生产构建、Canvas source check、Python Ruff 与格式检查、canonical SQL 导出检查均通过。创作核心桌面／390px、新建直接进入、原位改名和 409 保稿浏览器 6 项通过。
- 真实 MySQL 素材流程当前 60 项已覆盖；成片与所有者权限 11 项通过。画布回收、目录、清理与执行保留共 45 项通过（首轮 43 项，修正退役分集夹具后另 2 项通过）。其他分层回归记录保存在 `.runtime/creation-mode/`。
- 当前业务库的真实 HTTP 冒烟覆盖登录、CSRF、改名冲突、写作保存、画布保存、跨账号 404、MinIO 上传，以及 RabbitMQ 调度和渲染 Worker 的真实 FFmpeg 导出。4:3 源视频以 `auto` 成功输出 960×720；临时验证数据已精准清理。
- 旧画布回收站浏览器 2 项未通过：登录超时，以及旧脚本访问已有无效 `/canvas` 列表路由后定位超时。同点源码快照证明该列表路由在本次修改前已不存在；证据见 `.runtime/creation-mode/recycle-preexisting-route-evidence.json`。保留失败记录，未增加额外回收站 UI，也未降低断言。
- 未执行付费模型供应商、全部画布浏览器场景或完整原版一比一运行验收；测试替身通过不代表真实供应商验收。额外性能 profile 未开启，读取脚本单样本冒烟不作为性能结论。
