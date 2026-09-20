# 模型生成任务与资产库方案

日期：2026-09-20  
版本：第四版补充（业务场景衔接与分镜图片确认采用）。  
状态：用户已完成建表并授权实施，分镜图片由用户选中并确认采用后更新。归档独立预算与投递暂停恢复入口已纳入实现；运行步骤和验证边界见[运行说明](../../模型生成运行说明.md)。

当前完整建表定义：[schema.mysql8.sql](../../数据库模型/schema.mysql8.sql)，包含模型生成任务、调用记录、媒体资产及其他业务表。

## 1. 本版修改结论

| 原设计 | 本版 |
|---|---|
| 任意业务的通用任务框架 | 只共用文本、图片、视频的生成任务流程 |
| async_tasks 三十多个字段、多种细分状态 | 19 个字段、6 种业务状态 |
| 独立 task_outbox | 当前动作投递信息直接保存在 task |
| 生成主记录 + 调用尝试两表 | 合并成一次调用一行的 ai_generation_records |
| 通用输出表 + 资产条目表 | 文本放调用记录，媒体结果直接成为 media_assets |
| 已发布但等待超时后周期重投 | 删除；published 不进入周期发布扫描 |

新增表从六张减少到三张。不是把所有字段换成一个不受约束的大 JSON：关键任务状态、版本、投递状态和锁仍是普通列；JSON 只保存确实随协议变化的输入/响应快照。

## 2. 最终结构

~~~text
POST 文本 / 图片 / 视频生成
          ↓
API → GenerationService → DAO
          ↓
MySQL: async_tasks → ai_generation_records → media_assets → 现有 media_files
          ↓
Publisher → RabbitMQ → Celery Worker → 模型适配器
                                            ↓
                                     文本结果 / MinIO 文件
~~~

RabbitMQ 负责消息传递，Celery Worker 执行 Python，MySQL 保存事实，MinIO 保存媒体。保持 Python + uv、FastAPI、SQLAlchemy/PyMySQL、api → service → dao。没有 Redis 依赖。

任务创建时同事务写 task 和第一条 prepared 调用记录，task 自身为 pending 投递状态。提交后返回 202，Publisher 扫描任务表。没有独立 Outbox 表，也没有通用 Handler 注册/任意 payload/动态任务类型。

三个简单的生成处理流程按 service_type 分发，复用配置选择、HTTP、消息发布、租约、保存、取消和查询代码。模型协议仍通过独立适配器扩展。

三个生成入口保持不变。带业务来源的请求由 Service 中明确的场景方法校验对象、准备输入快照，再直接调用共用生成服务；服务间不通过 HTTP 调用自己的接口。模型适配器只处理供应商协议，不操作分镜业务表。初期不引入动态业务 Handler 注册、客户端回调地址或任意表名写入。

## 3. 三张表的职责

| 表 | 字段数 | 保存内容 |
|---|---:|---|
| async_tasks | 19 | 模型类型、业务状态、幂等、当前动作投递、锁、取消意图、错误和时间 |
| ai_generation_records | 17 | 每次调用准备/提交的模型/请求快照、受理 ID、响应、用量、文本正文 |
| media_assets | 9 | 每个已保存图片/视频的文件关联、调用来源、序号和名称 |

复用 media_files 保存存储位置和文件属性；ai_model_configs 仅加内部 capability_cache。详细字段、索引、DDL 与事务规则只维护在独立表设计文档。

对外 generation_id 直接是 task.id。record_id 是某次调用；asset_id 是一个媒体结果。删除旧的“生成主记录 ID + task_id”和“output_id + library_item_id”双重身份。

受理时的第一条调用记录先存快照，started_at=null 表示还没有调用模型。后续实际生成提交各留一条记录，不覆盖旧错误。视频查询和保存重试更新当前记录，不计成新生成。用户重新生成创建新任务，retry_of_id 指回原任务。

任务历史列表按 task 和 call_no=1 联查；详情可查看所有调用。资产指向真实产生该文件的调用，不要求任务整体成功才可浏览。

## 4. 任务状态精简

| status | 用户含义 |
|---|---|
| queued | 排队中 |
| running | 处理中，包含等待模型/查询/保存 |
| succeeded | 所需结果完整保存 |
| failed | 未完整完成或明确失败 |
| cancelled | 已确认取消 |

不再单独定义 waiting、retry_wait、partially_succeeded、needs_attention。执行动作只有 submit/poll/save；前端通过动作显示“生成中/等待模型结果/保存中”，不推算百分比。

部分成功：task=failed，error.code=partial_result，返回 partial=true 和已保存产物，显示“未全部完成，已有 N 个结果”。文本截断保留正文、finish_reason=length，task=failed、error.code=text_truncated。资产不会因为任务 failed 而隐藏或删除。

按用户后续确认，任务状态删除 unknown，统一归为 failed，前端展示“失败”。提交后连接断开不能认定模型没收到；内部调用记录保留受理证据，can_retry 不能仅依据任务 failed 决定。生成等待超时后停止自动查询和生成。

取消意图只用 cancel_requested。供应商是否支持、是否确认写入本次调用 response_data.cancel_result；只有已确认取消才变更业务状态。默认配置改变不影响已受理任务的快照；配置停用后停止尚未提交的新调用，已提交的结果继续查询和保留。

## 5. 消息投递：明确禁止确认后周期重投

上一版使用“超过投递宽限期就重发”的策略不再采用。排队慢不等于消息丢失；它可能让数据库不断追加重复消息，并占用正常任务的消费能力。

### 5.1 当前动作的四种内部消息状态

~~~text
pending → publishing → published → idle
           │
           └─ 未确认/发布失败：有限次退避补偿
~~~

- pending：达到 next_run_at 后需要发布。
- publishing：持有发布短锁，等待 confirm。
- published：broker 已确认且没有不可路由返回，排队时间再长也不自动改回 pending。
- idle：本轮不再主动发布，可表示 Worker 已接管、动作已结束或发布暂停；结合业务状态与锁判断，不能单靠此值声称消息已消费。

这是内部投递状态，不向用户增加四种任务状态。每个任务同时只有一个待执行动作，满足把投递信息合到任务表的前提。

### 5.2 Publisher 和 Worker 的竞态

Publisher 只扫描 pending，不扫描所有 queued/running。抢占时设置 publishing、发布 lock_token/locked_until，publish_count 加一，释放事务后发 persistent 消息 {task_id,message_version}。启用 publisher confirms 和不可路由检查，只有 ACK 且无 basic.return 才条件写 published。

消费者可能先于 confirm 的数据库写回接管消息。Worker 按当前 message_version 把消息置 idle、换执行令牌；Publisher 迟到写回必须匹配原 publishing、版本和令牌，匹配失败就结束，不能覆盖成 pending/published。发布锁恢复器使用相同条件，避免把已经消费的任务重新投递。

同版本未确认发送最多三次，指数退避；耗尽后为 failed/message_delivery_unknown，记录结束时间，message_status=idle、next_run_at=null，清除锁但保留原动作。失败任务拒绝迟到消息接管；用户明确恢复时核对 prepared/poll/save 证据并递增版本，旧消息失效，不重新提交受理不明的模型请求。

confirm 已返回但数据库写回前崩溃可能造成有限重复，这属于未确认记录的补偿窗口，无法通过普通数据库事务完全消除。版本和执行锁负责去重；不声称端到端 exactly-once。

### 5.3 各类恢复必须有对应证据

| 证据/情况 | 动作 |
|---|---|
| pending 到期 | 正常发布 |
| publishing 锁过期、仍未被接管 | 当前版本有界确认补偿 |
| published 排队较久 | 只告警并检查消费者/积压，不重投 |
| idle 执行锁过期 | 检查调用状态，条件安排一个新版本安全恢复动作 |
| 已有 provider_task_id | 恢复查询 |
| prepared 且旧执行锁已失效 | 可以恢复提交 |
| sent/unknown，没有受理证据 | failed，保留调用证据，禁止盲目再次 POST |
| 队列确实被删除/清空 | 运维按明确任务 ID 单次修复，先核对调用状态；不常驻重扫 published |

恢复器安排一次新动作后会推进 message_version、清除旧锁，所以旧扫描条件不再成立。不能每轮扫描都再创建恢复消息。

Worker 在保存动作结果/下一次调度后 ACK。进程断开时未确认消息由 RabbitMQ 重投，过期版本、终态或已有有效执行锁的重复消息不重复调用。旧 Worker 所有数据库写回都必须携带当前执行令牌和版本。

### 5.4 视频轮询是新动作，不是旧消息补投

视频受理后持久保存 provider_task_id；同事务设置 next_action=poll、next_run_at、message_version+1、message_status=pending、publish_count=0，清除当前锁，然后 ACK 当前消息。

每轮查询只调度一个新动作；如果仍未完成，完成当前查询后再安排下一轮。不会把同一个 published 查询消息按时间重复发送。保存失败同理安排 save 动作，不再次生成。

队列使用 short_drama.tasks 持久 direct exchange，tasks.ai.text/image/video 持久队列。独立死信队列不自动重新生成。初期单节点不宣称高可用；不依赖延迟插件，队列确认超时要高于单次动作最大时间。

## 6. 三个创建接口与公共查询

公共前缀 /api/v1，三个创建接口均要求 Idempotency-Key。路径决定类型，请求体不含 service_type；三个独立 Schema/TypeScript 类型，后端共用 create_generation(kind,payload,key)。

| 接口 | 输入 |
|---|---|
| POST /ai/generations/text | messages、可选 temperature/max_output_tokens |
| POST /ai/generations/image | prompt、reference_media_ids、aspect/resolution/count |
| POST /ai/generations/video | prompt、可选首尾帧媒体 ID、aspect/resolution/duration_ms |

文本请求示例：

~~~json
{
  "config_id": "2071234567890123456",
  "input": {"messages": [{"role": "user", "content": "请将以下小说改编成剧本……"}]},
  "parameters": {"max_output_tokens": 4096}
}
~~~

图片请求示例：

~~~json
{
  "config_id": "2071234567890123457",
  "input": {"prompt": "雨夜街道，一名穿黑色风衣的青年", "reference_media_ids": []},
  "parameters": {"aspect": "16:9", "resolution": "2K", "count": 1}
}
~~~

视频请求示例：

~~~json
{
  "config_id": "2071234567890123458",
  "input": {"prompt": "镜头缓慢推进", "first_frame_media_id": "2071234567890123459"},
  "parameters": {"aspect": "16:9", "resolution": "1080p", "duration_ms": 6000}
}
~~~

config_id 可省略，创建时固定类型默认项。参数和媒体类型按模型能力校验，不支持就明确拒绝，不静默忽略。source 可选，真实源对象经 Service 校验后，把类型/ID/可推导项目分集放入请求快照。本地演示 ID 不能冒充服务端来源。

分镜生图仍调用 POST /ai/generations/image，source 使用按 scene 区分的明确 Schema，例如：

~~~json
{
  "config_id": "2071234567890123457",
  "source": {
    "scene": "shot_image",
    "shot_id": "2071234567890123470",
    "layout": "single"
  },
  "input": {"prompt": "雨夜街道，一名穿黑色风衣的青年", "reference_media_ids": []},
  "parameters": {"aspect": "16:9", "resolution": "2K", "count": 4}
}
~~~

shot_image 只允许图片入口使用；未知或类型不匹配的场景直接拒绝。Service 校验真实分镜及参考素材，读取相关业务内容，服务端推导 project_id/episode_id，不接受客户端伪造归属。实际提示词、参考素材、采用所需 layout 与请求参数一并固定到 request_data，Worker 不在执行时重新读取已修改的脚本来替换输入。原请求的 source 参与幂等 hash；省略 source 时为独立生成。layout 表示单个产物的布局，count 表示产物数量，两者不能混用。

请求 JSON 初始上限 1 MiB、messages 最大 100、图片 count 默认 1/应用上限 4，仍受模型具体约束。非法字段拒绝，不允许请求覆盖服务地址、密钥、队列。

首次创建 task 和 prepared 调用记录提交成功后返回 202：

~~~json
{
  "generation_id": "2071234567890123460",
  "service_type": "image",
  "status": "queued"
}
~~~

同键同原请求返回 200 和原任务当前摘要，同键异体/跨类型误用返回 409。hash 包含接口类型及 create/retry 操作，先查幂等键再选择当前默认配置。

| 管理接口 | 用途 |
|---|---|
| GET /ai/generations | 按类型/状态/模型配置/时间等筛选，服务端分页 |
| GET /ai/generations/{id} | 任务、配置/输入摘要、文本结果、媒体资产、错误 |
| GET /ai/generations/{id}/records | 按 call_no 查看调用过程，不再另设 attempts 资源 |
| POST /ai/generations/{id}/cancel | 请求取消 |
| POST /ai/generations/{id}/retry | 以新幂等键创建关联旧任务的新生成 |
| POST /ai/generations/{id}/resume | 仅恢复有明确证据的投递或结果保存，不新建生成 |
| GET /ai-model-configs/{id}/capabilities | 只读内部已识别的业务能力 |

列表返回 {items,total,offset,limit}，limit 最大 100，created_at DESC,id DESC，不在列表加载长正文。详情示例：

~~~json
{
  "generation_id": "2071234567890123460",
  "service_type": "image",
  "status": "succeeded",
  "result": {
    "text": null,
    "assets": [
      {
        "asset_id": "2071234567890123461",
        "record_id": "2071234567890123462",
        "media_id": "2071234567890123463",
        "media_type": "image",
        "name": "图片-2071234567890123460-1"
      }
    ],
    "partial": false
  },
  "error": null,
  "can_cancel": false,
  "can_retry": false
}
~~~

文本 result.text 是 {record_id,content,finish_reason}，媒体 assets 为空。不能继续返回旧的 generation_output_id/library_item_id；资产 ID 就是结果 ID。

cancel 对未提交任务直接确认并使旧消息版本失效；running 时仅记意图，提交前锁内再次检查，已经发送后不能承诺无费用。上游不支持取消则继续追踪，不能假装取消成功。

retry 仅用于可确认安全重试的 failed/cancelled，新建 task 和调用记录，原记录/资产不变。配置已改变或停用提示重新选择；最新调用记录 sent/unknown 或消息投递不明的 failed 禁止快捷重试。部分结果失败时重新生成整次请求，界面说明可能收费；完整成功后再生成使用对应类型创建接口。

请求错误沿用 {error:{code,message,fields?}}；异步错误保存在任务/调用的规范 error 中，不直接透传密钥、输入或上游原始 body。

## 7. 协议和生成执行

适配器仍覆盖：
- openai_chat：DeepSeek、豆包/方舟兼容文本、百炼兼容文本、GPT 与中转站。
- openai_responses：提供 Responses 的 GPT 文本和中转。
- openai_images：GPT-Image-2 等 Images API。
- ark_images、ark_video：豆包生图、Seedance 等方舟媒体服务。
- dashscope_media：百炼原生生图/生视频。

模型是配置字符串，不按每个型号创建流程；特定参数依官方协议确认，表述为设计范围而非已完成实际联调。参考 GPT 官方文档：
- https://platform.openai.com/docs/models/gpt-5.6
- https://developers.openai.com/api/docs/models/gpt-image-2

自动识别按已验证缓存 → 官方域名/明确路径/内置中转规则 → 安全只读能力描述 → 有依据的候选。模型列表不能证明生成兼容；保存配置不发起收费生成。视频没有全行业通用默认协议。

首次实际提交一个候选。仅明确未受理且接口不匹配时可切换一次，新增调用记录；其它生成失败不自动重发 POST。超时/5xx 不能作为未受理证据。未知私有协议显示未适配，用户不选协议。

cache 按地址/模型/类型/凭据身份指纹、解析器版本失效；缓存更新不增加用户编辑 row_version，当前调用开始后固定适配器。请求快照写入实际 resolved_parameters，不能悄悄修改或丢弃参数。

在任务行锁和执行令牌检查下把记录 prepared → sent、写 started_at，然后提交 HTTP。结果明确后更新同一记录；sent 后失联先核对。任务第一次执行设置 started_at，以第一条 config_snapshot 中固定的预算计算超时，不需要独立 deadline 字段。

初始生成预算可配置：建连 10 秒，文本 3600 秒、图片 300 秒、视频 30 分钟；文本队列 RabbitMQ 消费确认策略配置为 70 分钟，留出持久化余量。正常查询间隔可配置为 3–60 秒，初始 5 秒，查询错误退避为 15 秒。超时任务统一 failed 并停止自动动作，但受理不明时禁用重新生成。新预算仅作用于新任务，历史快照不改。

获得媒体结果后单独开启默认 24 小时归档窗口，起点存 response_data.archive_started_at；不消耗原生成等待预算。每个新的下载/保存动作前校验该窗口。保存超时保留结果清单与已存资产，resume 在有来源或持久定位证据时可重新开启仅保存窗口。message_delivery_unknown 同样通过 resume 在行锁下核对 prepared/poll/save 证据后安排一个新版本，不扫描重投 published，也不把受理不明的 sent 请求当成 prepared。

模型请求保持已有地址/DNS 实际连接校验。媒体下载逐跳验证，不给 CDN 发送模型鉴权头。SDK/HTTP 客户端也不能无条件重试生成 POST。

## 8. 媒体结果直接成为资产

media_assets 只存成功转存的图片和视频，无单独的输出状态/重复库条目。未完成产物的有限 manifest 保存在调用 response_data 中；每项预分配 asset_id/output_index，用于崩溃恢复与固定文件名。

MinIO 对象键为 generations/{task_id}/{record_id}/{asset_id}-{sha256}.{ext}。先保存 manifest，再转存对象，之后事务内创建 media_files/media_assets 并更新任务结果。内容哈希隔离失效 Worker 的不同字节写入，避免仅靠数据库令牌却覆盖已引用文件。相同结果内容重试保持相同对象键；已有对象核对内容和归属再收尾，不重复调用模型。重试入库必须匹配唯一(record_id,output_index)和 media_id，不覆盖用户改名。失效写入可能留下无引用对象，首期不自动删除。

文本正文直接保存 text_content；上游返回的真实 usage/finish_reason 在 response_data，未知不估算。响应 DTO 显式提取字段，不将内部加密下载凭证或完整 response_data 暴露给前端。

图片/视频每个成功文件自动入库，未采用也长期保留；任务失败/取消或模型配置变化不清除已归档结果。多图部分成功照常可用。文件只保存一份，重复采用仅更新业务 media_id。

同步响应在持久化前崩溃而供应商无法取回，可能丢失结果；如实记录，不自动再生成。base64 不写 JSON：先计算归档定位及元数据并提交 manifest，再在当前 Worker 上传，最后调度 save，不能向其他 Worker 传本地临时文件路径。正文响应上限初始 8 MiB；单图 50 MiB、单视频 1 GiB。

现有 assets 是角色/场景/道具，不与 media_assets 混为一表。原镜头被替换仍可写 media_recycle_bin，但不是从资产库删除。没有全局 is_adopted；同一文件可服务多个目标。首期不提供永久删除或未采用自动清理，MinIO 生成前缀不设置自动过期。

### 8.1 资产 API 与采用

保留 /api/v1/media-library/items 的列表、详情、PATCH 重命名和 POST /{id}/apply；这里的 {id} 是 media_assets.id。列表按图片/视频、名称、生成来源、时间筛选；来源追溯 record → task，真实属性来自 media_files。不提供客户端直接创建资产条目的接口。

重命名用 name 和 row_version；采用请求：

~~~json
{
  "target": {"type": "shot_image", "id": "2071234567890123470"},
  "expected_media_id": null
}
~~~

target 为 shot_image/shot_video/asset_image，id 为分镜或素材的真实服务端 ID。锁目标比较 expected_media_id，变化返回 409，已经是同一文件则幂等返回。类型必须匹配，源参数从调用快照读取，缺失的业务采用参数需要明确补充，不捏造尺寸/时长，不自动裁剪/转码。

采用不需要原模型仍启用或保留密钥。复用已有分镜确认/回收事务；共享素材采用前明确共享影响，单项目独立修改先使用现有 copy_for_library。当前本地演示对象不能伪装成服务端采用目标。

### 8.2 分镜图片：生成候选与确认采用分开

用户已确认：生成成功后不自动采用，只有用户选中并确认后才更新 shot_images。该表的 media_id 必填、shot_id 唯一、state=confirmed，继续只表示每个分镜当前采用的图片，不承载生成中状态或多张候选。

1. 受理：创建 task 与 prepared 调用记录，request_data.source 保存经校验的场景和分镜来源；此时不创建空的 shot_images，也不清除旧图。
2. 生成：每张成功保存的图片写 media_files/media_assets。结果完整保存即可 task=succeeded，不等待用户采用；部分成功的已保存候选同样可见。
3. 查看：分镜页按 source_scene=shot_image、source_id=分镜 ID 查询历史任务和资产；服务端分别映射到 request_data.source.scene/shot_id，客户端不能指定任意 JSON 路径。任务列表按首条调用记录过滤，资产列表按实际产出调用记录过滤，避免多次调用导致重复任务。source 表示生成来源，不限制资产以后用于其他目标。
4. 采用：用户通过 POST /media-library/items/{asset_id}/apply 确认。MediaAssetService 解析可信资产及调用快照，复用分镜业务 Service，在同一事务内锁目标、比较 expected_media_id，写入 media_id、model_id、prompt、layout、aspect、resolution，并处理旧图回收历史。缺失的布局等业务参数必须明确提供并校验。
5. 冲突或失败：目标已变化返回 409；目标已删除返回不存在，不重建分镜。只重试采用，不重新生成、不修改原任务的生成结果、不删除资产。同一文件已被采用时幂等返回。

分镜页面的“生成成功”和“当前已采用”分别展示。来源对象后来被修改或删除，不撤销已经保存的资产和来源快照；再次采用仍按目标的当前存在性和约束校验。

## 9. 前端仍保留两类页面

侧栏顺序：项目管理 → 素材库 → 任务管理 → 资产库 → AI 配置。任务管理紧接素材库分组。

任务管理 /tasks/text、/tasks/image、/tasks/video 三个 Tabs，查询同一历史 API，类型/状态/模型/时间筛选和分页。详情展示输入、调用记录、正文/资产与错误；部分结果失败仍展示已有产物。前端只展示五种任务状态，failed 为“失败”，重试和恢复按钮由服务端能力标志决定。

资产库 /media-library/image、/media-library/video 两个 Tabs，预览/重命名/查看来源/选择采用。视频列表不自动播放，没有封面使用占位。资产列表不以任务 succeeded 为过滤条件。

真实分镜页增加候选图片区域：展示本分镜历次生成的可用资产、当前采用图片，以及明确的“确认采用”操作。轮询到生成完成只刷新候选，不自动调用 apply；刷新页面后通过服务端来源过滤恢复候选，不能只依赖浏览器内存。

沿用石墨/琥珀风格、现有侧栏和 Ant Design 尺度，窄屏优先摘要/状态/主要操作；键盘 Tabs、可见焦点、抽屉关闭返回焦点，状态使用文字。所有媒体访问 URL 按需签发，不批量为全部历史签名。

Axios 仍有三个独立创建方法 generateText/Image/Video，对应三个生成路径；查询方法合用。创建重发复用幂等键，新的重新生成才换键。ID 为字符串，密钥不进浏览器存储。

当前页活跃任务约每 2–5 秒查询，终态停止；网络失败不把最后已知状态改为失败。切换 Tab/筛选取消过时请求，刷新按 URL 恢复。前端轮询只 GET 状态，不触发后台消息投递。

## 10. 目录与部署

后端保留三类创建路由、统一历史管理路由和资产路由。生成部分分为任务受理、GenerationExecutionService 和 MediaAssetService；业务场景准备由 Service 明确方法承担，采用复用已有 ShotMediaService 事务。现有 GenerationService 已负责剧本/分镜结果批次入库，新增任务受理服务使用 AIGenerationService，避免覆盖原职责。DAO 对应三张新表，并复用现有业务 DAO。tasks/ 保留 celery_app、publisher、worker、recovery 四个模块，不再有 OutboxDAO/动态 registry/任意 Handler 接口。ai/adapters 继续隔离厂商协议。

HTTPX 为运行依赖，Celery 使用 AMQP；云 MySQL/MinIO 复用，RabbitMQ 与 Worker 通过配置接入。Worker 建议 Linux 容器，三类队列初始并发 4/2/2，按配额调整；每个进程使用不同雪花节点号，Session 不跨线程共享，外部请求不持数据库事务。

监控队列积压/消费者连接/未确认消息/死信、pending 与过期 publishing、执行锁过期、受理不明失败、转存失败。published 排队久触发告警，不触发任务级自动重发。

密钥仅调用记录加密保存；终态任务默认 7 天清理，活跃任务和 message_delivery_unknown 的可恢复失败保留所需凭据。下载凭证转存后清理。当前单用户边界不等于多租户认证；未来账号系统需扩展身份与幂等作用域。

只对本版三表与缓存列实施增量迁移，旧版尚未落地，不执行删表合表脚本。不安装本地 MySQL、不改数据库账号权限。历史/资产默认保留，任务清理不能级联删除媒体。

## 11. 验收与实施

新增重点验证：
1. published 任务排队一小时、反复运行 Publisher/Recovery，仍没有新增 publish。
2. publishing 确认丢失只在当前版本有界重发，次数耗尽暂停。
3. 消费者先接管、Publisher 迟到写回不能覆盖 idle 或重新排队。
4. 执行锁超时只产生一个安全恢复版本；sent/unknown 不重发模型 POST。
5. 每轮视频 poll 的版本只推进一次，重复消息不重复安排下一轮。
6. 同键创建只产生一个 task；每次真正提交有独立 record，查询不伪装成新生成。
7. 文本保存/截断、媒体自动入库、部分失败保留资产、入库重试不重复、目标采用冲突。
8. 两个页面、三个创建接口与 Axios 方法、模型协议能力及无隐藏收费探测。
9. 分镜一次生成四张图：成功产物全部入库，生成及前端轮询均不改变 shot_images；用户确认后仅所选图片成为当前采用图。
10. source 场景类型、真实分镜、来源筛选、输入快照及刷新后候选恢复；部分成功候选仍可采用。
11. 采用并发冲突、重复确认、目标删除与采用事务失败：原任务和资产不变，不新增模型调用，旧图回收与当前图替换同事务完成。

完整 MySQL/消息故障测试在独立临时数据库和 RabbitMQ 测试 vhost/队列运行，不能对业务库/队列做破坏性测试。真实文本、图片、视频各至少一次闭环；无资格型号只标记模拟契约验证，不称已连通。

实施顺序：三表/Schema/DAO → 任务内投递状态与 RabbitMQ/Worker → 三个生成接口和适配器 → 媒体资产与采用 → 前端任务/资产页 → 独立环境故障测试与联调。厂商真实调用与本地协议 fixture 验证区分记录，不将基础设施连通称为所有模型已联调。
