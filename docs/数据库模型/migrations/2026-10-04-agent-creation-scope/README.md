# Agent 创作会话对象范围

此增量用于已经存在 Agent 九表、尚未保存流程与对象范围的旧库。新库使用[完整建表 SQL](../../schema.mysql8.sql)，无需执行本增量。应用不会自动 ALTER 表。

在 `agent_conversations` 新增 `stage`、`subject_type`、`subject_id`、`task_type` 和 `scope_version`，以及 `idx_agent_conversations_scope` 和 `ck_agent_conversations_scope`。旧会话保留四个范围字段 NULL、`scope_version=0`；不按标题、人物姓名或模型输出猜测归属，不复制消息、不重写作品。

同时将 `agent_runs.uk_agent_runs_active_conversation` 唯一索引替换为 `idx_agent_runs_active` 普通索引，保留 `active_conversation_id` 生成列。旧唯一索引把排队消息也算活动运行，导致第二条排队消息写入冲突；新流程允许多个已持久化的排队运行，制作执行仍由项目/会话锁与运行准入串行控制。脚本先新增普通索引，再核对并移除旧唯一索引；不删除运行、不更改状态，重入时已完成步骤跳过。

新会话由服务端确定 `scope_version=1`；客户端不能覆盖版本。创建后的范围不可编辑。合法组合如下，`episode` 的 `subject_id` 必须等于本集 ID，素材必须关联本集且属于当前项目，镜头必须属于本集且未删除。

| stage | subject_type | task_type |
| --- | --- | --- |
| source | episode | writing |
| assets | episode | extraction / batch |
| assets | asset | creation / image |
| storyboard | episode | planning / batch |
| storyboard | shot | creation / image / video |

历史列表按本人、项目、分集、流程和对象精确筛选，任务可选筛选；未提供范围的兼容列表只返回未分类旧会话。默认会话解析在项目锁内查找最近未归档的同范围会话，没有记录时创建；明确“新建对话”仍可创建第二段记录。对象删除或脱离本集不会改写历史归属，继续发送与制作会被拒绝。

HTTP 新建与解析入口必须提供完整范围；旧会话仅保留本人读取，不能改名、发送消息、增删附件、批准计划或继续创作，仍可停止旧运行。内部旧服务契约保留兼容用于历史任务验证，不作为新工作区入口。

在 `backend/` 工作目录执行：

```powershell
uv run python scripts/agent_scope_migration.py --precheck
uv run python scripts/agent_scope_migration.py --apply
uv run python scripts/agent_scope_migration.py --precheck
```

预检和执行日志仅记录目标数据库、结构差异与实际执行的 DDL。确认目标库并暂停相关写入后执行。脚本逐项校验字段类型、NULL、默认值、索引和 CHECK 表达式及启用状态；已有兼容项跳过，缺失项新增。已有不兼容项会拒绝自动修改，应先审查差异。MySQL DDL 隐式提交，失败后可按现有结构重入，不删除业务记录或重建业务表。

[001-conversation-scope.sql](001-conversation-scope.sql) 用于审查或在确认缺失项后手动执行；不要盲目重复运行全部 SQL。安全重入以 Python 脚本为准。

```powershell
uv run python scripts/agent_scope_migration.py --export
uv run python scripts/agent_scope_migration.py --check-export
uv run python scripts/export_schema.py --check
```

这些导出命令不执行 DDL。新字段计入既有 Agent 严格 readiness，缺失时启用 Agent 的服务拒绝启动。迁移后还须完成同版本其他增量，最终以 `inspect_agent_schema` 的完整检查为准。
