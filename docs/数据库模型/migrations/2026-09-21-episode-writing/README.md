# 分集小说与剧本持久化迁移

新库直接执行 [完整建表脚本](../../schema.mysql8.sql)。存量库必须先完成本迁移，再重启包含分集写作接口的新后端、任务 worker 和其他写入进程；应用启动不会自动迁移。

新增字段：

| 字段 / 约束 | 定义 |
| --- | --- |
| `episodes.editing_script_id` | `BIGINT UNSIGNED NULL DEFAULT NULL`，当前编辑的剧本 |
| `episodes.content_version` | `BIGINT UNSIGNED NOT NULL DEFAULT 1`，小说、剧本及编辑选择共用版本 |
| `ck_episodes_content_version` | 已强制执行的 `CHECK (content_version > 0)` |

`editing_script_id` 不设外键，避免 episodes 与 episode_scripts 循环依赖。所有应用写入先锁分集行，在同一事务中校验剧本归属本集；删除当前剧本必须清空指针。已有剧本确认唯一约束保持有效。

## 执行顺序

1. 暂停全部写入入口，包括旧版 API、生成任务与 worker。显式选择目标数据库，使用 MySQL 8.0.21+、utf8mb4、UTC 和严格 SQL 模式。
2. 在本地保留 `SHOW CREATE TABLE episodes`、`episode_novels`、`episode_scripts` 及相关数据备份，执行只读 `000_precheck.sql`，记录三张表行数和待初始化选择。不将凭据写入输出。
3. 初次迁移应没有这两个字段及版本 CHECK。若已有部分或全部字段，必须检查名称、unsigned 类型、可空性、默认值及 CHECK 的表达式和 `ENFORCED=YES` 与上表完全一致；不一致时停止，不能依赖脚本自动修复。已有字段时还应执行 `002_verify.sql` 检查无跨集或悬空指针；未回填造成的空指针可在下一步恢复。
4. 在同一个数据库连接中执行 `001_add_episode_writing.sql`。DDL 隐式提交，不能用事务整体撤销；脚本仅补缺失字段和约束，不删除列、表、剧本或正文。随后事务初始化编辑指针：本集已确认剧本优先，否则按 `position` 最小、`id` 最小选择；无剧本保持 NULL。正文、剧本确认状态、审计字段和历史行全部保留。
5. 执行只读 `002_verify.sql`。检查定义正确、三张表行数不变、各异常查询为空，并与预览逐集比较首次初始化结果，再启动新版运行时。

脚本支持失败后的重复执行：已有字段及 CHECK 被跳过，只回填 `editing_script_id IS NULL AND content_version = 1` 的未修改分集；已有非空指针和大于 1 的版本均不覆盖、不重置。若中断于部分 DDL 之间，重新执行前只检查已经存在的字段，两个字段齐备后才能执行完整 `002_verify.sql`。重复执行仍须暂停写入，不能将迁移作为日常修复或定时任务。版本大于 1 的空指针可能来自合法删除，不应重新选择历史剧本。

本次没有自动回滚脚本。发布失败时保持写入暂停，修复后继续部署；不要直接恢复不参与版本更新的旧写入进程。MySQL 集成测试仅在 fixture 创建的随机独立测试库运行，覆盖初次并发保存、确认后编辑、空操作以及旧结构迁移和重复执行，不触碰业务库。
