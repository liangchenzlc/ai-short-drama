# Agent 会话、统一创作与上下文

本页补充分集 Agent 的输入契约。所有路径相对 `/api/v1`；请求沿用账号认证和 CSRF 规则。会话、消息、运行、附件与生成历史仅本人可见，项目成员不能读取他人的候选或对话。已采用的作品通过原工作流共享。运行要求 Agent schema 就绪、`AGENT_ENABLED=true` 和独立 Agent Worker。

现有会话、消息、运行、批准与采用入口见 [API 总约定](README.md)。本页以 [Agent 路由](../../backend/src/short_drama/api/v1/agent.py)、[上下文 schema](../../backend/src/short_drama/schemas/agent_context.py) 和 [消息 schema](../../backend/src/short_drama/schemas/agent_runtime.py) 为准。

## 对象会话范围

创建和解析会话使用稳定对象 ID，不使用人物名称或镜头编号作为身份：

```json
{
  "project_id": "100",
  "episode_id": "200",
  "stage": "assets",
  "subject_type": "asset",
  "subject_id": "300",
  "task_type": "creation",
  "title": "林晚的创作"
}
```

| 流程 | 对象 | task_type |
| --- | --- | --- |
| source | episode（本集 ID） | writing |
| assets | episode（本集 ID） | extraction / batch |
| assets | asset（本集关联素材 ID） | creation / image |
| storyboard | episode（本集 ID） | planning / batch |
| storyboard | shot（本集未删除镜头 ID） | creation / image / video |

`POST /agent/conversations/resolve` 在事务内返回此范围最近的未归档会话；没有才创建。显式新建使用 `POST /agent/conversations`，新建必须提供完整范围。范围创建后不可修改，输出含 `scope_version=1`。`scope_version=0` 的未分类旧记录保持只读，不按标题猜测归属；仍可停止历史运行。

`GET /agent/conversations` 传 project_id、episode_id 及 stage、subject_type、subject_id，可用 task_type 限定任务、query 搜索标题、include_archived 包含归档。列表与 total 使用同一范围过滤。省略范围只列旧未分类会话。

对象工作区读取详情、消息、附件、运行快照、事件流及操作 Run 时，通过查询参数携带上述四个 scope 字段；发送消息在 body 携带 `expected_scope`。范围不匹配返回 `agent_scope_mismatch`（409）；删除或脱离本集的对象不能继续提交创作。候选 API 支持相同查询参数，并通过候选关联的 Tool/Run/Conversation 验证归属；不匹配候选返回 404。本人及项目访问校验始终生效。

## 统一消息与运行恢复

新客户端发送正文、model_config_id、附件及 Skill，不再选择讨论或生成。`mode` 默认 `auto`，Agent 按本条用户要求选择只读工具、`prepare_task`、`create_candidate` 或 `propose_plan`。单项任务必须在当前对象范围内；图片/视频在来源、模型、参数唯一确定时可直接执行。多步骤和批量制作保留具体计划审核，所有结果仍是本人候选，用户另行采用。

兼容旧请求：显式 `mode=discuss` 仍禁止制作；`mode=generate` 与 task 按原明确授权处理，不能扩大旧请求权限。附件、Skill 和历史助手回复不能增加制作权限。

活动执行期间允许追加消息，持久化排队并串行处理。等待计划审核时补充消息是独立控制回合：只读问答保留原计划，修订替换旧计划并使旧批准失效。停止、审核、采用后继续分别操作，追加消息不代表采用或批准。

连续排队的补充消息始终跟随当前待审核计划；前一条补充修订取消原计划后，后一条不能绕过新计划直接制作，也不能保留两份同时待审核的制作授权。拒绝计划会把该 Run 收窄为只读回复，不能改用单项工具执行被拒绝的制作；用户发送新的明确创作消息后可建立新的 Run。

`GET /agent/conversations/{id}/state` 返回 `conversation_id/cursor/resume_cursor/active_run/queued_runs`。active_run 优先真实执行或媒体等待，再是待审核运行；queued_runs 按队列顺序返回。Run 新增 `queue_position/waiting_reason`。首次先读取 state，再读消息分页，最后从 resume_cursor 订阅 SSE；无活动运行时该值等于当前 cursor，有活动运行时从最早相关事件之前恢复，保留在途回复 delta。断线恢复使用已消费的事件 cursor，不能跳到最新位置丢失文字。客户端不能以最新创建的 Run 覆盖前序执行状态。201 只表示已受理，生成成功以实际候选和任务归档为准。未知受理结果不自动换键重新发送。

最终回复及失败反馈消息的 `artifacts` 仅包含当前 Run、本人、当前项目与分集的候选引用，格式为 `{"artifact_id":"十进制ID","kind":"候选类型"}`，不包含原始工具参数、供应商响应或其他 Run 的候选。已落库候选在最后一段模型回复失败后仍可核对，不重发制作。消息引用提供核对入口，不代表自动采用。纯问答成功表示回复完成；明确单项任务或已批准计划存在未执行步骤时，不能仅凭文字回复标记制作成功，分别返回 `agent_task_not_executed` 或 `agent_plan_not_completed`。

停止时已有但尚未出现在本次回复消息中的候选，会在取消事务内补一条私有消息引用；已受理媒体的延迟结果归档后沿用相同去重规则。去重在持有会话锁后读取当前消息，避免旧数据库快照造成重复；只补当前 Run 的候选，不把历史回复引用误认为本次已展示。Run 保持停止终态，不再调模型、继续计划或自动采用。

## 私有对话附件

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/agent/conversations/{conversation_id}/attachments/uploads` | multipart 单文件上传，字段 `file`，必带 `Idempotency-Key` |
| POST | `/agent/conversations/{conversation_id}/attachments/references` | 引用本人个人库或当前项目可访问的素材/媒体，必带 `Idempotency-Key` |
| GET | `/agent/conversations/{conversation_id}/attachments` | 标准分页；`offset>=0`、`1<=limit<=100`，默认 limit=50 |
| DELETE | `/agent/conversations/{conversation_id}/attachments/{attachment_id}` | 移除附件，返回 204；已受理运行的冻结输入保持有效 |

引用请求为 `{"source_type":"asset|media","source_id":"十进制 ID"}`。`asset` 使用 `asset_id`，冻结素材名称、描述、提示词、版本及当前图片；`media` 使用 `media_id`。添加附件不会把个人文件复制进项目库。

附件上传和引用的幂等键长度为 1–64，只允许 ASCII 字母、数字、`_`、`-`、`:`。同键同内容返回原附件；同键异参或复用已移除附件的键返回 409。上传返回 201，引用返回 201。

读取默认返回全部未移除附件。`pending=true` 仅返回从未绑定消息的待发送附件，供刷新后恢复输入；`pending=false` 返回已经发送过的附件。已发送附件可以再次显式选入新消息；列表不会自动重新选入。

附件输出包括：

```json
{
  "id": "123",
  "kind": "text",
  "name": "notes.md",
  "mime_type": "text/plain",
  "byte_size": 120,
  "media_id": null,
  "url": null,
  "text_preview": "最多 500 字的预览",
  "metadata": {},
  "checksum_sha256": "SHA-256",
  "pending": true
}
```

`url` 为短期展示链接，重新读取时刷新；附件、消息引用和运行 checkpoint 持久化稳定媒体 ID、存储定位和校验值，不能把该 URL 当作身份。文本预览不会替代交给模型的完整文本。

| 输入 | 上传限制 | 模型输入方式 |
| --- | --- | --- |
| TXT / Markdown | 1 MiB；UTF-8 或 GB18030；非空且不能包含 NUL | 发送完整正文；所选文本资料合计最多 128 KiB |
| PNG / JPEG / WebP | 20 MiB；实际解码检查，最多 4000 万像素 | 正规化为最长边 1280 像素的 JPEG，以二进制内联输入 |
| MP3 / WAV / M4A | 20 MiB，最长 120 秒，真实解码检查 | 转为单声道 16 kHz MP3，以二进制内联输入 |
| MP4 / WebM | 50 MiB，最长 120 秒，实际媒体探测 | 最多 8 张均匀采样画面；有音轨时默认连同音频输入 |

音视频处理依赖 FFmpeg/ffprobe。处理后所选媒体合计最多 4 MiB。视频采样只代表这些画面，不能保证理解所有动作细节；用户明确选择 `visual_only` 时，输入会声明本次未理解视频声音。

## 发送包含附件与 Skill 的消息

原消息请求新增以下可选字段，正文仍须非空：

```json
{
  "content": "结合参考画面调整本集人物的动作描述",
  "expected_scope": {"stage":"assets","subject_type":"asset","subject_id":"300","task_type":"creation"},
  "model_config_id": "456",
  "attachment_ids": ["123"],
  "skills": [{"id": "script.v1", "content_version": "1"}],
  "video_audio": "include"
}
```

最多选择 16 个附件和 8 个 Skill，不允许重复 ID。`video_audio` 仅允许 `include`（默认）或 `visual_only`。服务端在受理前验证本人、当前会话、文件安全及有效状态、Skill 内容版本；失败不会建立运行。模型任务兼容性在实际运行中处理并写入中文反馈，不作为未验证模型的发送门槛。

受理后，附件正文、文件校验值、稳定存储定位和 Skill 全文版本被冻结。随后编辑、停用、删除 Skill 或移除附件不会改变已受理运行。消息幂等重放仍返回原受理结果，不能重新调用模型。

消息 `references` 展示附件的类型、名称、媒体 ID、预览与元数据，以及 Skill 的 ID、名称、内容版本、是否内置。加载的 Skill 只能指导当前创作，不能扩大工具、模型调用或作品采用的授权；系统不执行上传文件中的脚本。

首次决策把附件转为 SDK 二进制内容，经原 SafeTransport 发送。在发送前保存完整输入与精确请求；已收到完整响应后的本地回放使用归档的二进制输入和响应，不依赖再次下载媒体，也不会发起远端请求。受理状态不确定时沿用原恢复规则，不能自动重发。SDK URL 输入始终被拒绝。

## 内置与个人 Skill 管理

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/agent/skills` | 标准分页，先列内置 Skill，再列本人未删除 Skill；停用项仍可管理 |
| POST | `/agent/skills/uploads` | multipart `file`；单个最多 64 KiB 的 UTF-8 `.md`，返回 201 |
| GET | `/agent/skills/{skill_id}` | 查看内置 ID 或本人自定义十进制 ID |
| PATCH | `/agent/skills/{skill_id}` | 管理本人 Skill：必带 `row_version`，可更新 `name`、`instructions`、`enabled` |
| DELETE | `/agent/skills/{skill_id}` | JSON body 必带 `row_version`；软删除并停用，返回 204 |

内置 ID：`novel.v1`、`script.v1`、`extract.v1`、`storyboard.v1`、`asset_patch.v1`、`shot_patch.v1`、`image.v1`、`video.v1`，只能查看和加载。

输出为 `id/name/filename/builtin/content_version/row_version/enabled/instructions/checksum_sha256`。内置 `row_version` 和 `filename` 为 null；所有 Skill 的 `content_version` 及自定义 Skill 的 `row_version` 输出十进制字符串。管理修改增加 `row_version`；只有正文变化增加 `content_version`。同版本内容校验值稳定，加载旧内容版本返回 409，加载停用 Skill 返回 422。

```json
{"row_version":"1","instructions":"# 对白要求\n控制单句长度。","enabled":true}
```

正文非空、无 NUL，UTF-8 编码后最多 64 KiB；所选 Skill 全文合计最多 128 KiB。客户端提交 409 后须让用户核对新内容，不能自动覆盖。

## 模型选择与运行反馈

选择只检查本人配置、启用状态和类型，不要求 verified，也不自动调用供应商探测。工具和多模态能力未知时尝试用户的实际请求；系统不能编码的协议组合在运行中本地反馈。模型不匹配、认证失败、限流和受理不明分别处理，不静默丢弃附件或切换模型。文字回复不能代替真实候选执行证据。

`GET /agent/models` 的每项新增 `input_capabilities`：

```json
{
  "text": true,
  "image": true,
  "audio": false,
  "video": "sampled_frames",
  "evidence": "declared"
}
```

`video` 为 `sampled_frames` 或 `unsupported`；`evidence` 兼容 `declared/model_family/text_only`，增加 `runtime` 表示等待实际请求证据。未知 Chat 配置不再按模型名称判为仅文本。以下旧声明和 `/verify` 接口保留兼容，当前创作界面不提供手动验证或声明入口：

```json
{"row_version":1,"image":true,"audio":false}
```

模型行版本输出十进制字符串，输入兼容正整数与十进制字符串；前端始终保留字符串。声明绑定配置版本、地址、模型标识和凭据身份；更新声明会增加模型版本，并保留未变更的已验证工具协议证据。配置发生变化后，旧声明失效。声明本身不调用模型，也不代表真实供应商验证。

当前 SDK 适配中，Chat 协议允许图片和 MP3/WAV 音频内联；Responses 协议允许图片，不能编码音频输入；视频由图片采样与可选音轨组合输入。协议或供应商明确拒绝所选组合时，运行在对话内给出安全中文反馈。用户决定更换模型、减少附件或对视频明确选择仅画面，系统不自动降级。

## 验证范围

维护测试包含：真实随机 MySQL 的私有 CRUD、幂等与冻结版本；本地 HTTP 替身的 SDK 图片/音频载荷、工具续调与零网络回放；本地 FFmpeg 的视频采样及音轨输入。它们未验收真实供应商的多模态理解质量，也未代替真实 MinIO 集成验证。付费模型兼容性须对具体配置单独验收。
