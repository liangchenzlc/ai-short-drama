# 剧本创作工作台接口

适用于 standard 项目，项目仅所有者访问。路径均在 /api/v1 下，E 表示 /projects/{project_id}。ID 与并发版本均为十进制字符串。

> 2026-10-28：小说／大纲保存及 novel_script、story_outline、script_generate 场景已退役，不保留兼容 API。旧说明保存在[历史记录](../reviews/2026-10-28-重构前数据库与写作契约记录.md)。

## 当前文档

| 方法 | 路径 | 请求／响应 |
| --- | --- | --- |
| GET | `E/writing` | project_id、content_version、editing_script、confirmed_script_id、active_writing_tasks |
| PUT | `E/script` | content、content_version、script_id、change_source?；首次 script_id 为 null |
| GET | `E/story-settings` | row_version、logline、synopsis、genre、character_bios |
| PUT | `E/story-settings` | 完整四字段及 row_version，返回新版本 |

读取未创建的稿返回 null，不插入空稿。正文保留空白与换行，允许空串，最大 1 MiB UTF-8；每次受理保存推进 content_version，内容相同也推进，阻止迟到旧请求覆盖最后输入。409 writing_version_conflict 保留本机输入。非空当前剧本保存即生效；空稿取消可用状态，不回退旧稿。普通保存不能替代私有 Agent 候选采用回执。

剧本正文以纯文本保存，保留输入中的空白和换行；不再保存额外的富文本结构。

## 故事设定

首次 GET 返回四项空值及版本 0，不创建记录。PUT 首次以 0 建立版本 1，后续 CAS 递增，冲突返回 story_settings_version_conflict。四字段都为非 null 字符串，保留空白；上限分别是 200、20000、100、20000 字符。设定版本独立于正文，不修改当前剧本，也不自动注入生成提示词。

## 选区改写

POST /ai/generations/text 携带 Idempotency-Key，首次 202，同键同请求重放 200。source.scene 为 script_rewrite，必填 project_id、content_version、script_id、start、end、selected_text，instructions 必须非空。模型配置与参数沿用文本生成约定。

偏移是 Unicode 码点半开区间，浏览器必须将 UTF-16 偏移转换，禁止截断 surrogate pair。服务端验证所选区间逐字相等，只冻结选文与前后各最多 2000 码点；模型只返回替换文本，服务端拼接前后文。新请求不读取隐藏小说／大纲。

活动改写锁定整篇剧本，刷新 writing 可恢复任务投影，离页不取消任务。任务先持久化原始输出，再将正文、发布状态、并发版本、业务回执和任务成功同事务提交。空输出、截断、超限、取消、版本／目标变化均不覆盖正文。受理不明只核对或重放原体原键；恢复保存复用原输出，不重新调用模型。

| 错误码 | 处理 |
| --- | --- |
| writing_version_conflict／writing_target_changed | 409，保留输入并核对 |
| writing_target_busy | 409，目标已有活动任务 |
| writing_selection_changed | 409，选文已变 |
| writing_source_empty | 422，来源未保存非空内容 |
| writing_result_too_large | 422，结果超过正文上限 |
| writing_retry_requires_source | 409，需捕获当前来源后创建新请求 |

## 变更快照

GET E/script-revisions 提供无正文摘要分页，GET E/script-revisions/{id} 提供前后正文。普通按键编辑不逐次建快照。当前页面无历史恢复入口，助手待审写入链继续维护快照。

源码入口：api/v1/creation/writing/script、service/creation/writing/script、dao/creation/writing/script；前端 pages/creation/script、features/creation/writing、api/creation/writing、types/creation/writing。
