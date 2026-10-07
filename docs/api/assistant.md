# 项目 AI 创作助手 API

标准分集和无限画布共用 `/api/v1/assistant`、本人项目对话及原有 Agent Worker 运行机制。助手只提供回答、分析、建议和回复中的文字示例，不执行生成、提取、计划、候选创建、作品修改或采用。没有可执行工具。用户仍可使用原编辑器和生成设置，并自行使用助手的回复。

## 会话与权限

会话以当前账号与项目隔离，可有多段对话。切换同项目的分集、阶段或画布不会自动更换对话；切项目或账号会更换历史范围。项目成员不能读取别人的消息、附件、Skill、运行或模型。退出、过期、撤权和项目归档终止继续访问。写入继续校验同源、Cookie 与 CSRF。

| 方法 | 路径（相对于 `/assistant`） | 请求或说明 |
| --- | --- | --- |
| GET | `/status` | `enabled/schema_ready`；不会调用模型 |
| GET / POST | `/conversations` | GET 按 `project_id` 分页；POST `{project_id,title?}`，必带 `Idempotency-Key` |
| POST | `/conversations/resolve` | `{project_id,title?}`；返回最近未归档本人项目对话，缺少时创建 |
| GET / PATCH | `/conversations/{id}` | 本人详情；PATCH `{row_version,title?、archived?}`，活动运行须先停止才能归档 |
| GET | `/legacy-conversations` | 按 `project_id` 分页本人旧对话，默认包含已归档记录；不合并、不改写旧范围 |
| GET / POST | `/conversations/{id}/messages` | GET 分页；POST 下文消息输入，必带 `Idempotency-Key` |
| GET | `/conversations/{id}/runs`、`/runs/{id}` | 私人运行历史或详情 |
| GET | `/conversations/{id}/state` | 当前运行、持久排队、`cursor/resume_cursor` |
| GET | `/conversations/{id}/events` | SSE，沿用事件名 `agent`、`EventRead` 和 `access-ended`，支持 `cursor/Last-Event-ID` |
| POST | `/runs/{id}/stop` | 停止本人运行；不会取消或修改另一条对话 |
| GET | `/conversations/{id}/attachments` | 附件分页，可筛选 `pending` |
| POST | `/conversations/{id}/attachments/uploads` | multipart `file`，必带幂等键 |
| POST | `/conversations/{id}/attachments/references` | `{source_type:'media'|'asset',source_id}`，必带幂等键 |
| DELETE | `/conversations/{id}/attachments/{attachment_id}` | 从之后的输入选择中移除；已经受理的快照不改写 |

分页仍为 `{items,total,offset,limit}`。详情使用 `ConversationRead`：新项目对话 `scope_version=2`，`episode_id/stage/subject_type/subject_id/task_type` 均为 null。消息、运行、状态、事件沿用既有 Agent DTO；内部 `mode='discuss'` 不是用户可选择的创作模式。所有实体 ID 和版本保持十进制字符串，事件 cursor/seq 和 scope_version 为普通数值。

## 每条消息的输入与来源

```json
{
  "content": "分析这一段的节奏并提出建议",
  "model_config_id": "123",
  "context": {
    "kind": "episode",
    "id": "456",
    "revision": "8",
    "include_document": true,
    "stage": "source",
    "selected": []
  },
  "attachment_ids": [],
  "skills": [{"id": "builtin:continuity", "content_version": "1"}],
  "video_audio": "include"
}
```

`model_config_id` 可省略；沿用本人模型偏好或已启用默认文本配置。`context` 可省略或为 null，表示不自动读取作品。自动作品可以移除；明确的手工引用仍通过 `context.selected` 保留，此时传 `include_document=false`，只冻结明确对象与来源标识，不读取整集或整图。

- 分集 `kind=episode`，`id` 为稳定分集 ID，`revision` 为 `content_version`；`stage` 可为 `source/assets/storyboard/assembly`，只表示当前阅读位置。可额外传 `storyboard_revision` 校验分镜集合。`selected` 支持素材或镜头 `{kind:'asset'|'shot',id,revision?}`。
- 画布 `kind=canvas`，`id` 为 `source_key`，`revision` 为服务器画布行版本。`selected` 支持 `{kind:'node',id,revision?}`，不接受分集 stage 或 storyboard_revision。节点 ID 保留原字符串，不转为数字。明确引用图片、视频、音频节点时，服务端从节点的主媒体稳定身份冻结本人可见且属于当前项目的 `MediaFile`，合入本轮多模态输入；空生成节点只带文字。普通自动整图上下文只读取文本与元数据，不全量附带媒体。
- `selected` 最多 16 项、不重复。服务端逐项验证真实项目/分集/画布归属，失效手工引用整轮拒绝，不静默跳过。服务端只读取可见的已保存作品，不接受客户端正文、图文档或来源快照。
- 发送前完成相关编辑器保存与画布远端回执；保存失败、恢复待核对或 409 时保留输入并停止发送。新受理时验证来源版本并将快照保存在私人 `AgentRun.checkpoint.context_snapshot`；排队或页面切换不会改变该轮资料。
- 自动上下文有界：单段文本最多 24 KiB、总文本最多 96 KiB，素材和镜头各最多 100 项、画布节点最多 200 项。截取位置返回在 `message.references[type=source].truncated`；前端必须明确显示已截取，不能声称完整读取。可移除自动作品后以具体引用或附件补充。

消息不接受 `mode/task/expected_scope` 或任何生成参数，未声明字段返回 422。内容最多 32000 字；明确节点媒体与附件合计最多 16 项，Skill 最多 8 项。引用媒体缺失、越权、身份或类型不一致、无有效 SHA256、超大小/时长限制时整轮拒绝；缺失或已改变对象不会静默降为文字分析。

## 附件、Skill 与模型

复用[既有多模态附件和个人 Skill](agent.md)。TXT/Markdown 最多 1 MiB，PNG/JPEG/WebP 最多 20 MiB，音频最多 20 MiB，视频最多 50 MiB；音视频不超过 120 秒且须有实际探测时长。处理后的媒体总量不超过 4 MiB。媒体保持稳定 MinIO 定位值、SHA256 与本人权限，签名 URL 只用于显示。画布图片、视频、音频 resource ID 对应 `MediaFile.id`；明确 @node 主媒体在服务端冻结，Worker 从可信存储读取字节并核验 SHA256 后内联图片/采样视频帧/可选音频，不要求供应商下载展示 URL。文件缺失或 hash 不匹配导致运行失败，不能冒称已完成视觉分析。冻结媒体身份在 `message.references[type=source].media` 返回，存储定位值与私有校验细节不出现在该引用中。

个人 Skill 的上传、查看、修改、启用和删除继续使用 `/agent/skills`；加载的正文仅指导回复，不获得执行工具。新消息按本人、启用状态和 `content_version` 冻结所选 Skill，内容变化返回 409，受理后的快照保持不变。内置或上传 Skill 的指令均不能扩大纯对话边界。

模型配置入口仍为宿主 `/ai_config`，模型列表、输入能力和个人管理复用 `/agent/models` 及原配置 API，不要求工具调用能力验证。当前运行 Gateway 支持 OpenAI `chat-completion/openai-response`，要求 `api_format=openai`；其他协议返回 `unsupported_assistant_protocol`，不能按地址静默猜测或回退。图片能力依模型真实支持；音频输入当前只支持 Chat Completions 协议。视频沿用采样画面和显式声音选择，依赖 FFmpeg/ffprobe；本地合同或测试替身不代表真实供应商验收。

## 排队、恢复与兼容

同一对话串行运行，新消息持久排队。`state.resume_cursor` 与 SSE 事件可恢复断线状态，不能用重新发送代替恢复。消息创建和附件上传使用 1–64 字符幂等键；受理不明时沿用原请求体和原键核对。幂等重放先于重新读取作品版本，已受理请求不会因后来编辑而创建另一轮。供应商受理不明的模型段不自动重发，明确再次询问才使用新键。

运行目的只由服务端在 `checkpoint.purpose='assistant_chat'` 持久化，不新增目的字段。V2 的工具清单和工具预算为空/零；发送准备、队列 claim、结果结算、工具执行及可信 ORM 写检查均阻止创作工具与候选。旧 `/agent` 的发送、附件写入、修改、审核、继续和停止入口拒绝 V2；旧接口不是纯对话的备用写入口。

旧 V0/V1 会话保持原分集与对象范围，只读历史通过旧 GET 接口读取；已有运行、审核、候选和明确采用继续走原权限与版本契约。新助手不能发送到旧会话，不合并历史或自动执行旧任务。升级前按[项目助手数据库迁移](../数据库模型/migrations/2026-10-06-project-assistant/README.md)显式更新结构并部署同版 API/Worker；应用不会自动迁移。
