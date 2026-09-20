# 模型生成任务、调用记录与媒体资产表设计

日期：2026-09-20  
版本：第四版补充（三张新表不变，明确分镜候选与确认采用的写表时机）。  
状态：用户已执行独立 SQL 文件完成建表并授权实施。分镜图片确认后采用规则已确认；旧版本未落地，不需要执行删表或数据合并。独立归档预算与受控 resume 已纳入服务实现，不新增表或字段。

总体方案：[模型生成与资产库方案](../superpowers/specs/2026-09-20-unified-ai-generation-design.md)

## 1. 表结构调整

| 表 | 处理 | 最终职责 |
|---|---|---|
| async_tasks | 保留并缩减为 19 个字段 | 仅管理文本/图片/视频任务，包含当前动作的消息投递状态 |
| task_outbox | 不再建表 | 当前动作的投递意图整合在 async_tasks |
| ai_generation_records + ai_generation_attempts | 合并为 ai_generation_records | 一行一次调用准备/实际提交，保存快照、响应、文本结果 |
| ai_generation_outputs + media_library_items | 重组为 media_assets | 一行一个已保存的图片/视频，即资产库条目 |
| media_files | 复用现有表 | 文件位置、MIME、大小、真实尺寸/时长 |

只有三张新表：async_tasks、ai_generation_records、media_assets。仍给 ai_model_configs 增加一个内部 capability_cache，不建设能力表。

“通用”仅指文本、图片、视频共用任务流程，不保留任意业务 Handler 注册、task_type、handler_version、payload_version、通用 payload、队列配置列或通用 result_ref。队列直接按 service_type 在代码中映射。

## 2. 关系和一次调用的含义

~~~text
async_tasks 1 ── N ai_generation_records 1 ── N media_assets
                         │                         │
                         └─ text_content          └─ media_files
~~~

- 一次用户生成请求对应一个 async_tasks；API 的 generation_id 直接使用该任务主键，不额外分配生成主记录 ID。
- 每次实际生成 POST 使用一条调用记录。受理请求时先创建 call_no=1、status=prepared 的记录，存放配置和输入快照；started_at 为空代表还未提交模型，不能计为已发生调用。
- 只有明确未受理的协议候选切换等情况才增加调用记录，不能覆盖上一条错误。查询视频进度、重试保存文件不新增调用记录。
- 用户主动重新生成创建新任务，retry_of_id 指向原任务，并创建自己的调用记录。
- 文本正文保存在对应调用记录，不建文本产物表。首期每次文本调用只请求一份正文。
- 每个已持久保存的图片/视频产生一条 media_assets。失败、尚未下载的结果只保留在调用记录的有限结果清单中，不作为可用资产入库。
- 一个任务可能有多条调用记录；任务列表使用任务表和 call_no=1 的快照，详情按 call_no 查看调用过程。资产来源指向产生它的具体调用，避免混淆候选切换和用户重试。
- 同一文件可以被多个业务目标引用，但在资产库只有一条记录；本期不按内容 hash 自动合并不同生成的文件。
- shot_images 只在用户选中并确认采用后写入，继续保留 shot_id 唯一、media_id 必填、state=confirmed。任务受理与生成完成都不预占或覆盖当前采用图；多张候选来自 media_assets，无需增加分镜候选表。

## 3. 公共约定

MySQL 8.0.21+、InnoDB、utf8mb4，标识/状态使用二进制排序规则。ID 继续由雪花工具生成，API 的 ID/版本输出字符串。时间 DATETIME(6)，UTC；外键 RESTRICT，不级联清理生成资产。

沿用当前单用户应用边界，idempotency_key 全局唯一，建议 UUID；不接收客户端声称的用户身份。未来接入多用户时再扩展身份与幂等作用域，不把当前设计当作已有租户隔离。

JSON 只保存真实可变的请求/响应和规范错误，不把调度状态藏进 JSON。普通 DTO 显式列字段，不直接返回密钥或供应商原始 body。

## 4. async_tasks：模型生成任务（19 个字段）

| 字段 | 类型 / 空值 | 说明 |
|---|---|---|
| id | BIGINT UNSIGNED PK | 任务 ID，也是外部 generation_id |
| service_type | VARCHAR(16) 非空 | text/image/video，决定 Worker 队列 |
| status | VARCHAR(16) 默认 queued | 六种业务状态 |
| idempotency_key | VARCHAR(128) 非空，唯一 | 一次用户操作，网络重发复用 |
| request_hash | CHAR(64) ASCII 非空 | 包含接口类型/操作/原请求的 hash |
| retry_of_id | BIGINT UNSIGNED 可空 FK | 用户重新生成时关联旧任务 |
| next_action | VARCHAR(16) 可空 | submit/poll/save；无可调度动作时为 null |
| next_run_at | DATETIME(6) 可空 | 下一次发布的最早时间，含投递退避 |
| message_status | VARCHAR(16) 默认 pending | pending/publishing/published/idle，内部投递状态 |
| message_version | BIGINT UNSIGNED 默认 1 | 一次动作版本；下次查询/保存是新版本 |
| publish_count | TINYINT UNSIGNED 默认 0 | 当前动作的发布尝试数，默认最多 3 次 |
| lock_token | VARCHAR(64) 可空 | 当前发布或执行的唯一令牌 |
| locked_until | DATETIME(6) 可空 | 发布短租约/执行租约，两者共用这两个字段 |
| cancel_requested | TINYINT UNSIGNED 默认 0 | 取消意图，不等于已取消 |
| error | JSON 可空 | 最近规范错误：code/message，不包含原始敏感响应 |
| created_at / updated_at | DATETIME(6) 非空 | 创建与状态修改时间 |
| started_at / finished_at | DATETIME(6) 可空 | 首次执行和已知终态时间 |

删除原来的 task_type、handler/payload 版本、payload、queue_name、幂等 scope、result_ref、execution_count、action_retry_count、progress、deadline_at、lease_owner、state_version、cancel_outcome 和其他通用审计字段。model 配置及输入放在调用记录；前端显示执行动作，不显示推算百分比。

### 4.1 六种业务状态

| status | 说明 |
|---|---|
| queued | 尚未开始 |
| running | 生成、等待上游、查询或保存中 |
| succeeded | 所需结果完整保存 |
| failed | 已知失败；可能已有部分可用结果 |
| cancelled | 提交前取消，或上游明确确认取消 |

waiting、retry_wait、partially_succeeded、needs_attention 不再是状态值。等待视频结果表示 running + next_action=poll；保存表示 running + next_action=save。

部分产物成功但整次需求未完成时 status=failed，error.code=partial_result，详情返回 partial=true 和所有可用资产；界面显示“未全部完成，已有 N 个结果”。文本截断也是 failed，error.code=text_truncated，保留正文。不能为了精简而把不完整结果显示为完整成功。

任务仅保留 queued/running/succeeded/failed/cancelled 五种状态。投递或受理不明统一归为 failed，填写 finished_at，前端展示“失败”。是否允许重新生成仍检查调用记录；sent/unknown 的内部调用记录不能快捷重发。旧任务使用 006_remove_task_unknown.sql 升级，调用记录的受理证据保留。

### 4.2 四种内部投递状态

| message_status | 含义 | 是否被周期发布扫描 |
|---|---|---|
| pending | 到期后尚需发布 | 是 |
| publishing | 发布者持有短租约，等待 broker 确认 | 仅处理过期发布租约 |
| published | 已确认被正确路由到 RabbitMQ | 否 |
| idle | 本轮不再主动发布：已接管、已结束或暂停 | 否 |

业务状态和投递状态含义不同，不能用 status=queued 判断应不应该再次发布。published 即使排队很久也不能自动改为 pending。

一个任务同一时刻只存在一个待发布/执行动作，因此一行 task 足以承载投递意图；本期没有一个任务同时并行派发多个子动作的需求，不需要 outbox 表。

## 5. ai_generation_records：调用记录（17 个字段）

| 字段 | 类型 / 空值 | 说明 |
|---|---|---|
| id | BIGINT UNSIGNED PK | 某次调用记录 ID |
| task_id | BIGINT UNSIGNED 非空 FK | 所属任务 |
| call_no | INT UNSIGNED 非空 | 从 1 开始，锁任务行后分配 |
| config_id | BIGINT UNSIGNED 非空 FK | 原 AI 配置 |
| config_snapshot | JSON 非空 | 配置名、版本、模型标识、供应商、地址、执行预算 |
| request_data | JSON 非空 | input、parameters、source 快照及实际 resolved_parameters |
| credential_cipher | TEXT 可空 | 加密凭据，Worker 专用 |
| adapter | VARCHAR(64) 可空 | 实际适配器及契约版本，如 openai_chat.v1 |
| provider_task_id | VARCHAR(255) 可空 | 异步供应商任务 ID |
| status | VARCHAR(16) 默认 prepared | prepared/sent/succeeded/failed/unknown |
| text_content | MEDIUMTEXT 可空 | 文本正文，不再单独建输出行 |
| response_data | JSON 可空 | 规范化结果清单、真实 usage、finish_reason、上游请求 ID、保存进度 |
| error | JSON 可空 | 本次规范错误，保留历史 |
| created_at / updated_at | DATETIME(6) 非空 | 准备与结果更新 |
| started_at / finished_at | DATETIME(6) 可空 | 实际提交开始、上游调用已知终结 |

任务状态描述整次用户操作是否完成；调用状态描述一次上游请求，不互相复制。例如调用 succeeded 而文件保存失败时，任务可以 failed，但调用仍 succeeded。

prepared 时 started_at=null；发送前在同一任务锁和租约检查下将记录置 sent、写 started_at，再发请求。sent 只证明开始提交，不能证明未受理。provider_task_id 非空后继续查询同一记录。

合并后有少量输入快照复制：新增候选调用时复制原任务配置/输入，再记录该候选实际参数。首期候选切换最多一次，换取不再维护额外尝试表。已发送的请求快照不可编辑；响应摘要可在查询/保存时追加明确结果。

request_data.source 承载受校验的业务上下文。分镜生图固定为 scene=shot_image、shot_id、layout，并由 Service 推导 project_id/episode_id；该场景只允许图片入口。实际使用的提示词、脚本相关内容、参考素材与参数保存在请求快照，排队期间业务内容变更不替换它。source 参与原请求幂等 hash，不接收任意表名、回调或由客户端指定的项目归属。无 source 的请求仍支持独立生成。

response_data 是有定义的内部结构，不存完整上游 body。例如：
- provider_request_id、usage、finish_reason、cancel_result；
- media_manifest：每项有 output_index、预分配 asset_id、实际类型/属性、加密来源定位、保存错误；
- 已知供应商完成标识和输出数量。
大型 base64 不写 JSON，带签名下载地址以加密值保存，普通 API 不透传整份 response_data。资产已经存在时通过 record_id/output_index 查库，不重复复制所有媒体字段。

没有独立“每轮 poll 日志”表；必要诊断使用脱敏运行日志。每次生成提交必须留记录，不能因字段精简而只保留最后一次错误。

## 6. media_assets：生成结果与资产库合一（9 个字段）

| 字段 | 类型 / 空值 | 说明 |
|---|---|---|
| id | BIGINT UNSIGNED PK | 对外资产 ID |
| record_id | BIGINT UNSIGNED 非空 FK | 实际产生它的调用记录 |
| output_index | INT UNSIGNED 非空 | 此调用内产物顺序 |
| media_id | BIGINT UNSIGNED 非空，唯一 FK | 实际媒体文件 |
| media_type | VARCHAR(16) 非空 | image/video |
| name | VARCHAR(255) 非空 | 可修改的显示名 |
| row_version | BIGINT UNSIGNED 默认 1 | 仅重命名的乐观锁 |
| created_at / updated_at | DATETIME(6) 非空 | 入库/修改时间 |

只保存已经转存完成、可正常使用的媒体。不另设资产 status，也没有 generation_output_id/library_item_id 双重身份。media_assets 一行既是一个媒体产物，也是资产库的一项；文本在调用记录中，不混入资产库。

UNIQUE(record_id,output_index) 防止一次保存重试重复入库；UNIQUE(media_id) 保证一文件一库条目。一个请求生成四张图片，就是最多四条资产。失败项在 response_data 的 manifest 中说明，不创建空资产。

media_files 继续保存对象位置/MIME/字节数/尺寸/时长，本表不重复这些字段。提示词、模型、生成来源经 record_id 联查。媒体类型与文件 MIME/任务类型在 Service 校验。

不设全局 is_adopted、used_count 或删除标记。资产可以被多目标引用，采用关系来自 assets、shot_images、shot_videos 的 media_id。采用、替换、取消任务或停用模型都不删除已保存资产。

## 7. 索引与约束

- async_tasks：唯一 idempotency_key；(message_status,next_run_at,id) 投递扫描；(message_status,locked_until,id) 查过期锁；(service_type,status,created_at,id) 历史筛选。
- ai_generation_records：唯一(task_id,call_no)；(config_id,created_at,id)；provider_task_id 普通索引，不假设跨供应商全局唯一。
- media_assets：唯一(record_id,output_index)、唯一(media_id)、(media_type,created_at,id)。
- 外键均 RESTRICT，task 重试引用不能指向自身；调用/输出序号和版本大于零。
- task lock_token/locked_until 成对；succeeded/failed/cancelled 必须有 finished_at，queued/running 没有 finished_at。
- task.next_action/next_run_at 由 Service 与投递状态一起维护，终态清空动作与锁并使旧消息不可执行。
- request_hash 包含具体创建路由、原始业务请求，retry 还包括原任务 ID。先查幂等键再解析当前默认配置。
- source 与项目/分集归属保存到 request_data.source 快照，Service 先校验真实对象。分镜候选与历史查询使用 source_scene=shot_image、source_id，由服务端映射到固定 JSON 字段 scene/shot_id；不暴露任意 JSON 路径。任务按 call_no=1 过滤，资产按实际产出 record 过滤。本期使用分页 JSON 查询，数据增长后按真实执行计划决定是否增加生成列/索引。主列表类型、状态、时间过滤使用普通索引。

## 8. RabbitMQ 投递不再周期放大

### 8.1 新任务/新动作

在一个数据库事务中创建 task 和第一条 prepared 调用记录，并把 task 设置为 pending、message_version=1、next_action=submit。视频下一轮 poll 或保存重试，则在一次动作完成事务中递增 message_version，设置新动作、pending、next_run_at，publish_count=0，清空当前锁。

任务状态和投递意图共行提交，不存在“先改业务状态后忘记写队列表”的双写窗口。

### 8.2 Publisher

1. 仅扫描 pending 且 next_run_at 到期、未终结且有合法动作的记录。
2. 短事务抢占 publishing，生成 lock_token、设置短 locked_until，publish_count 加一，然后释放数据库锁。
3. 发布 persistent 消息 {task_id,message_version}，启用 publisher confirms 和不可路由检查。
4. 收到 ACK 且没有 basic.return 后，条件更新当前 message_version + publishing + 原 lock_token 为 published，清空发布锁。
5. NACK、连接异常、确认超时或 publishing 租约过期时，只对仍是当前版本/原发布状态的记录执行有界补偿。默认每版本最多三次；耗尽后标记 failed 并填写 finished_at，error.code=message_delivery_unknown，message_status=idle，next_run_at=null，清空锁；保留 next_action 供用户安全恢复。
6. 已知成功 published 不在扫描范围，不因 created_at/updated_at 超过阈值而复位。排队慢只告警消费者和积压，不复制消息。

“发布已成功但确认/数据库写回丢失”仍可能形成有限重复，这是无法把数据库和 RabbitMQ 放入一个普通事务的边界；有界确认补偿与对所有 published 任务周期重发不同。

消费者可能在发布者写回之前收到消息。消费者按当前版本接管该动作时，把状态改为 idle、换执行 lock_token。发布者迟到的条件更新失效，只记录已被接管，不再把它改回 published/pending，不再补发。过期 publishing 恢复器同样必须带版本和锁令牌，不能覆盖消费者。

达到发布次数上限后任务为 failed，迟到消息不再接管终态任务。用户明确恢复时，先核对 prepared/poll/save 的安全证据，再推进消息版本；旧消息保持失效。不能借此重新提交 sent/unknown 的模型调用。

### 8.3 Consumer 与动作完成

- 消息版本过期、任务已终结或同动作已有有效执行锁时，不再执行模型调用，按已处理重复消息 ACK。
- 当前有效消息条件接管为 idle，写执行 lock_token/locked_until，任务进入 running。
- 已送达的当前版本消息可以抢占 pending/publishing/published，不因发布补偿刚设置的退避时间而丢弃它；只有可信 Publisher 创建的队列消息可进入此路径。
- 业务写回同时核对版本和执行锁令牌；心跳续期，旧执行者失效后不能覆盖。
- 完成/下一动作/任务终态持久化后 ACK。进程断开时未确认消息由 RabbitMQ 重投；不要先 ACK 再保存任务。
- 下一轮视频 poll 是由当前 Worker 提交的新版本动作，每轮只调度一次，不是补投上一轮已发布消息。

### 8.4 恢复的范围

| 情况 | 行为 |
|---|---|
| pending 到期 | 正常发布 |
| publishing 租约到期、无接管证据 | 有界确认补偿 |
| published 长时间未消费 | 仅告警/检查队列与消费者，不自动重发 |
| idle 执行租约到期 | 有执行中断证据，检查调用状态后最多安排一个新版本安全恢复动作 |
| 有 provider_task_id | 恢复 poll，不重发生成 |
| 仍 prepared 且抢占已使旧锁失效 | 可恢复 submit |
| sent/unknown 且无法确定是否受理 | failed，停止生成 POST，保留内部受理证据 |
| 队列被删除/清空等已证实消息丢失 | 运维按明确任务 ID 单次修复；条件递增版本并检查调用状态，不常驻扫描全部 published |

恢复器一旦推进版本/重置锁，旧条件不再匹配，不能每次扫描继续追加消息。不承诺无消息日志表的 exactly-once；通过 ACK、版本、执行锁和上游受理判断避免重复副作用。

## 9. 保存、归档与采用事务

1. 上游返回后在调用记录持久保存受控 media_manifest，包括每个产物预分配的 asset_id 和稳定 output_index。
2. 以内容确定的对象键 generations/{task_id}/{record_id}/{asset_id}-{sha256}.{ext} 转存 MinIO，避免过期 Worker 的不同内容覆盖有效结果；同一内容的保存重试复用对象键。
3. 事务内创建/复用 media_files、插入 media_assets，核对唯一键与文件归属，更新该调用的保存结果。
4. 所需结果全部就绪才 task=succeeded；部分成功 task=failed + partial_result，已存在资产不回滚、不删除。
5. MinIO 上传在数据库事务外。对象存在但事务未提交时可校验后收尾；归档失败只重试保存。
6. asset_id/record_id/output_index 不因保存重试变化；重复插入必须验证指向同一个文件，不重置用户已经修改的名称。

同步正文或 base64 在持久化前崩溃且供应商无法取回，可能结果丢失。任务记录 failed/result_unavailable，不自动再次扣费生成。文本截断保留 text_content 并明确返回不完整。

首次确认媒体结果后，response_data.archive_started_at 记录独立归档窗口起点，默认 24 小时；保存前检查窗口，不使用原生成截止时间。base64 的预期定位、哈希、元数据先随 manifest 提交，随后上传，最后调度 save；清单中不存 base64 或本地文件路径。归档超时可在有可恢复证据时由 resume 重新开启仅保存窗口。投递暂停的 resume 仅按 prepared/poll/save 证据恢复，不允许重发受理不明请求。

资产采用复用文件并更新业务 media_id 及必要的业务参数，保持文件一份。expected_media_id 保护目标并发替换；已采用同一文件幂等返回。历史模型已停用也允许采用已保存资产。现有镜头回收站仍是业务替换历史，不是全局资产删除状态。

分镜图片采用事务由用户的 apply 请求触发：锁定真实分镜、校验当前 media_id，读取可信资产/调用快照，创建或更新 shot_images 的 media_id、model_id、prompt、layout、aspect、resolution，同时处理旧图回收。缺失参数由采用请求明确补充并校验，episode_id 取目标实际归属。全部业务写入同事务提交；生成 Worker 与前端生成轮询均不得自动执行该事务。

所需生成结果全部保存即任务成功，用户尚未采用不会使任务继续 running。采用冲突或失败不回滚生成资产、不改变原生成任务状态、不重发模型请求。来源分镜被删除后仍保留资产与来源快照；采用到已删除目标必须拒绝。来源关系与当前采用关系不同，其他分镜也可通过明确采用操作引用同一资产。

生成资产长期保留，无自动未采用清理、无首期永久删除接口；MinIO 生成前缀不启用自动过期。物理删除检查必须包含 media_assets 及历史输入引用。未来删除调用历史不得级联删除资产。

## 10. 增量 DDL 草案

可执行文件已拆分到 [2026-09-20-ai-generation](migrations/2026-09-20-ai-generation/README.md)，按说明依次执行预检、三张新表、配置缓存字段和只读核对。以下保留同一份 DDL 便于对照；执行独立文件后不要再重复执行本节 SQL。

仅创建三张表并增加配置缓存列，不执行旧版表的 DROP。实施时先校验现有 17 表，再用版本化迁移在独立 MySQL 测试库验证。

~~~sql
CREATE TABLE async_tasks (
  id BIGINT UNSIGNED NOT NULL,
  service_type VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL,
  status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'queued',
  idempotency_key VARCHAR(128) COLLATE utf8mb4_0900_bin NOT NULL,
  request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
  retry_of_id BIGINT UNSIGNED NULL,
  next_action VARCHAR(16) COLLATE utf8mb4_0900_bin NULL,
  next_run_at DATETIME(6) NULL,
  message_status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'pending',
  message_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
  publish_count TINYINT UNSIGNED NOT NULL DEFAULT 0,
  lock_token VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
  locked_until DATETIME(6) NULL,
  cancel_requested TINYINT UNSIGNED NOT NULL DEFAULT 0,
  error JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_async_tasks_idempotency (idempotency_key),
  KEY idx_async_tasks_publish (message_status, next_run_at, id),
  KEY idx_async_tasks_lock (message_status, locked_until, id),
  KEY idx_async_tasks_history (service_type, status, created_at, id),
  KEY idx_async_tasks_retry (retry_of_id),
  CONSTRAINT fk_async_tasks_retry FOREIGN KEY (retry_of_id)
    REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_async_tasks_type CHECK (service_type IN ('text','image','video')),
  CONSTRAINT ck_async_tasks_status CHECK (
    status IN ('queued','running','succeeded','failed','cancelled')
  ),
  CONSTRAINT ck_async_tasks_action CHECK (
    next_action IS NULL OR next_action IN ('submit','poll','save')
  ),
  CONSTRAINT ck_async_tasks_message CHECK (
    message_status IN ('pending','publishing','published','idle')
  ),
  CONSTRAINT ck_async_tasks_required CHECK (
    CHAR_LENGTH(TRIM(idempotency_key)) > 0
    AND CHAR_LENGTH(request_hash) = 64 AND message_version > 0
  ),
  CONSTRAINT ck_async_tasks_retry_self CHECK (retry_of_id IS NULL OR retry_of_id <> id),
  CONSTRAINT ck_async_tasks_cancel CHECK (cancel_requested IN (0,1)),
  CONSTRAINT ck_async_tasks_lock_pair CHECK (
    (lock_token IS NULL AND locked_until IS NULL)
    OR (lock_token IS NOT NULL AND locked_until IS NOT NULL)
  ),
  CONSTRAINT ck_async_tasks_terminal_time CHECK (
    (status IN ('succeeded','failed','cancelled') AND finished_at IS NOT NULL)
    OR (status IN ('queued','running') AND finished_at IS NULL)
  ),
  CONSTRAINT ck_async_tasks_time CHECK (
    updated_at >= created_at
    AND (started_at IS NULL OR started_at >= created_at)
    AND (finished_at IS NULL OR finished_at >= created_at)
    AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)
  )
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci COMMENT='模型生成任务及当前动作投递';

CREATE TABLE ai_generation_records (
  id BIGINT UNSIGNED NOT NULL,
  task_id BIGINT UNSIGNED NOT NULL,
  call_no INT UNSIGNED NOT NULL,
  config_id BIGINT UNSIGNED NOT NULL,
  config_snapshot JSON NOT NULL,
  request_data JSON NOT NULL,
  credential_cipher TEXT NULL,
  adapter VARCHAR(64) COLLATE utf8mb4_0900_bin NULL,
  provider_task_id VARCHAR(255) COLLATE utf8mb4_0900_bin NULL,
  status VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL DEFAULT 'prepared',
  text_content MEDIUMTEXT NULL,
  response_data JSON NULL,
  error JSON NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  started_at DATETIME(6) NULL,
  finished_at DATETIME(6) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uk_ai_records_call (task_id, call_no),
  KEY idx_ai_records_config_time (config_id, created_at, id),
  KEY idx_ai_records_provider_task (provider_task_id),
  CONSTRAINT fk_ai_records_task FOREIGN KEY (task_id)
    REFERENCES async_tasks (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_ai_records_config FOREIGN KEY (config_id)
    REFERENCES ai_model_configs (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_ai_records_call_no CHECK (call_no > 0),
  CONSTRAINT ck_ai_records_status CHECK (
    status IN ('prepared','sent','succeeded','failed','unknown')
  ),
  CONSTRAINT ck_ai_records_time CHECK (
    updated_at >= created_at
    AND (started_at IS NULL OR started_at >= created_at)
    AND (finished_at IS NULL OR finished_at >= created_at)
    AND (started_at IS NULL OR finished_at IS NULL OR finished_at >= started_at)
  )
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci COMMENT='模型调用记录与文本结果';

CREATE TABLE media_assets (
  id BIGINT UNSIGNED NOT NULL,
  record_id BIGINT UNSIGNED NOT NULL,
  output_index INT UNSIGNED NOT NULL,
  media_id BIGINT UNSIGNED NOT NULL,
  media_type VARCHAR(16) COLLATE utf8mb4_0900_bin NOT NULL,
  name VARCHAR(255) NOT NULL,
  row_version BIGINT UNSIGNED NOT NULL DEFAULT 1,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (id),
  UNIQUE KEY uk_media_assets_record_output (record_id, output_index),
  UNIQUE KEY uk_media_assets_media (media_id),
  KEY idx_media_assets_type_time (media_type, created_at, id),
  CONSTRAINT fk_media_assets_record FOREIGN KEY (record_id)
    REFERENCES ai_generation_records (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT fk_media_assets_media FOREIGN KEY (media_id)
    REFERENCES media_files (id) ON DELETE RESTRICT ON UPDATE RESTRICT,
  CONSTRAINT ck_media_assets_type CHECK (media_type IN ('image','video')),
  CONSTRAINT ck_media_assets_name CHECK (CHAR_LENGTH(TRIM(name)) > 0),
  CONSTRAINT ck_media_assets_numbers CHECK (output_index > 0 AND row_version > 0),
  CONSTRAINT ck_media_assets_time CHECK (updated_at >= created_at)
) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_0900_ai_ci COMMENT='生成图片视频结果与资产库';

ALTER TABLE ai_model_configs
  ADD COLUMN capability_cache JSON NULL
  COMMENT '系统内部协议与能力缓存，不由用户编辑';
~~~

## 11. 保留与校验

凭据只在调用记录加密保存，终态任务默认 7 天后清理；活跃任务与 message_delivery_unknown 的可恢复失败保留所需凭据。下载凭证在转存完成后清理。模型密钥不进入 RabbitMQ 消息、前端、普通日志。源模型停用不影响媒体采用。

用户已执行 SQL，应用不在启动时重复执行。实施验收覆盖三表约束/幂等、同一任务多次调用不覆盖历史、发布 ACK 丢失、发布者迟到写回、已发布未消费不重投、执行中断按证据恢复、视频新版本查询、文本截断、部分媒体保留、归档唯一性及采用冲突。

分镜专项验收：四张候选入库且 shot_images 不变；用户确认后只更新所选图；替换与回收事务一致；重复确认幂等；并发采用、目标删除和采用失败不影响生成资产、不产生新调用；页面刷新后能按来源查询候选。本次业务衔接不新增列或修改上述 DDL 定义。

不安装本地 MySQL，不执行删库/删表或权限变更。当前版本替换旧设计草案，不需要对尚未存在的旧设计表做迁移。
