# 剧本创作工作台接口

适用于 `workspace_mode=standard`。所有路径以 `/api/v1` 为前缀，`E` 表示 `/projects/{project_id}`。项目仅所有者可访问，ID 与并发版本均为十进制字符串。AI 创作助手沿用独立接口。

当前剧本页提供故事设定／剧本正文双标签和`script_rewrite`选区改写；标题不提供TXT导入导出，页面已移除“让助手起草剧本”和“最近变更”按钮，助手仍可从顶部进入并审阅采用剧本建议。旧小说／大纲编辑与`story_outline/script_generate`整篇生成无页面入口，历史数据和兼容API保留。故事设定不会自动成为这些AI场景的输入。

## 故事设定

`GET E/story-settings` → `{row_version,logline,synopsis,genre,character_bios}`。未保存时四字段为空、`row_version="0"`，读取不插入记录；项目不存在、已归档、模式不符或非本人项目返回404。

`PUT E/story-settings` 提交完整四字段及`row_version`，返回同结构。版本为十进制字符串，首次提交0创建版本1，后续每次保存推进一次；旧版本返回409 `story_settings_version_conflict`。设定与正文版本独立，不改变`content_version`或当前剧本，不受正文生成任务锁影响。

字段允许空值、保留首尾空白与换行：`logline`最多200字符、`synopsis`最多20000、`genre`最多100、`character_bios`最多20000。拒绝额外字段及空值null；未知字段和超限返回422。前端复用资源编辑会话串行自动保存、账号隔离恢复与显式冲突核对，网络失败保留输入；不从旧大纲回填。

## 当前文档

| 方法 | 路径 | 请求与响应 |
| --- | --- | --- |
| GET | `E/writing` | `project_id/content_version/novel/outline/editing_script/confirmed_script_id/active_writing_tasks` |
| PUT | `E/novel` | `{content,content_version}` → `{novel,content_version}` |
| PUT | `E/outline` | `{content,content_version}` → `{outline,content_version}` |
| PUT | `E/script` | `{content,editor_document?,content_version,script_id,change_source?:"edit"|"import"}` → `{script,content_version,confirmed_script_id}`；首次保存 `script_id=null` |

未创建文档返回 null；读取不创建空文档。小说、大纲正文返回 `id/content/updated_at`，剧本保留原有元信息。正文保留首尾空白与换行，允许空字符串，UTF-8 最大 1 MiB。三个文档共享 `content_version`，每次受理保存推进一次；过期写入返回 409 `writing_version_conflict`。

非空当前剧本保存后即发布并成为内部唯一确认稿，后续编辑仍立即生效。空正文取消当前可用状态，不回退旧剧本。界面不提供候选稿确认；并发版本与“最近变更”分开。AI 替换及整篇导入保留前后正文，不为每次按键建立历史。`editor_document` 为历史受限 Delta 兼容字段，新编辑器和 AI 以纯文本为准，纯文本保存清除旧格式。

`active_writing_tasks` 为数组：`{task_id,target,operation,status}`。`target` 为 `outline/script`，`operation` 为下文三个场景，`status` 为 `queued/running`。活动任务锁定对应目标；手动保存、内部写入、切换及确认均返回 409 `writing_target_busy`，其他文档仍可保存。素材提取与分镜生成在剧本有活动写作任务时拒绝准入。

旧 `E/scripts` 候选、`E/editing-script` 和 confirm 接口保留兼容用途，不在本工作台显示。未采用 Agent 候选仍须走原采用回执，不能通过普通保存或新生成绕过。旧 `novel_script` 任务继续产生候选。

## 异步创作

统一调用 `POST /ai/generations/text`，必带 `Idempotency-Key`，首次 202、同请求同键重放 200。可选 `config_id`、`instructions`（最多 4000 字符）、`parameters` 沿用通用文本能力；省略模型使用本人启用的默认文本配置。不引用私人生成草稿。

| source.scene | 来源必填字段 | 写入目标 |
| --- | --- | --- |
| `story_outline` | `project_id/content_version/input_kind`，来源为 `novel/idea` | 当前故事大纲 |
| `script_generate` | 同上，来源为 `outline/novel/idea` | 当前剧本，原位更新；无稿时创建 |
| `script_rewrite` | `project_id/content_version/script_id/start/end/selected_text`；`instructions` 必须非空 | 当前剧本的精确选区 |

`input_kind=idea` 时必须提供非空 `idea`，最多 4000 字符；已保存的来源不同时接受 idea。服务端在项目锁内读取已保存内容、校验版本并冻结来源及目标 ID/正文哈希；浏览器无需传整篇来源。大纲可跳过，直接从小说或想法生成剧本。

```json
{
  "source": {
    "scene": "script_rewrite",
    "project_id": "10",
    "content_version": "8",
    "script_id": "40",
    "start": 1,
    "end": 3,
    "selected_text": "台词"
  },
  "instructions": "让语气更克制",
  "parameters": {}
}
```

改写偏移是 **Unicode 码点的半开区间 `[start,end)`**。例如正文 `😀台词结束` 的选区为上例；浏览器 textarea 的 UTF-16 偏移必须先转换，不能截断 surrogate pair。服务端要求原区间逐字等于 `selected_text`，不按字符串搜索，重复台词也只替换所选位置。新请求提示词版本为 `script-rewrite-v2`，仅包含选文及前后各最多2000码点，不读取隐藏大纲；模型仅返回替换文字，服务器拼接原前缀与后缀，返回内容不作 trim。已经受理的任务仍沿用自身冻结快照；整篇目标锁与原子保存约束不变。

任务先持久化原始模型输出，再将目标文档、发布状态、并发版本、业务回执及任务成功同事务提交。成功详情的 `result.business` 为 `{kind,schema_version:1,document_id,content_version}`，kind 等于 source.scene。空输出、截断、超限、过期目标、取消或失去归属均不覆盖正文，原始回执仍供核对。

状态、取消、回执查询及恢复复用通用生成接口。离开页面不取消任务；刷新 writing 可重建目标锁。结果保存恢复复用原输出，执行前重新校验目标指纹与互斥。新写作场景不提供旧快照 retry，须从工作台重新捕获当前来源；已受理不明请求只用原体原键核对或重放。

| 错误码 | 含义 |
| --- | --- |
| `writing_version_conflict` | 409；并发版本已变化，保留输入后核对 |
| `writing_target_busy` | 409；目标已有活动任务 |
| `writing_target_changed` | 409；目标指针或正文已变化，禁止迟到覆盖 |
| `writing_selection_changed` | 409；选区文字不一致 |
| `writing_source_empty` | 422；来源尚未保存非空内容 |
| `writing_result_too_large` | 422；最终正文超过 1 MiB |
| `writing_retry_requires_source` | 409；需重新捕获来源后生成 |

迁移、部署顺序与实际执行边界见[剧本工作台增量](../database/migrations/2026-10-27-writing-workspace/README.md)。

## 最近变更与恢复

> **2026-10-28 界面调整：** “最近变更”按钮及专属历史弹窗已移除。以下快照与恢复接口继续作为兼容契约保留；下述恢复界面反馈为原实现记录。

| 方法 | 路径 | 请求与响应 |
| --- | --- | --- |
| GET | `E/script-revisions?offset=0&limit=20` | `{items:[id,source,before_version,after_version,created_at]}`，最新在前；最多100条/页，无正文 |
| GET | `E/script-revisions/{revision_id}` | 摘要字段及 `before_content/after_content`；恢复时服务端同时恢复内部保存的格式 |
| POST | `E/script-revisions/{revision_id}/restore` | `{content_version}` → 完整 writing；恢复该记录变更前整篇正文 |

所有读取和恢复校验当前项目所有权。来源为 `import/script_generate/script_rewrite/assistant/restore`，只在内容或格式实际变化时保存快照。正文更新、`project_script_revisions`、版本和操作回执同事务提交；普通 edit 不创建快照。恢复先完成本机保存，使用当前版本 CAS 并检查写作任务锁；恢复本身也保留恢复前正文。过期返回 `writing_version_conflict`，不会直接回滚后来输入。恢复提交成功而正文刷新失败时，界面提供重读正文并禁止重复恢复。

旧库部署须先执行[变更记录迁移](../database/migrations/2026-10-27-script-revisions/README.md)。助手读取及待审建议合同见[助手 API](assistant.md#当前剧本引用与待审修改)。
