# 标准图片组与逐图工具

素材图和分镜图生成后直接成为作品，多张默认第一张为主图，不展示候选或确认采用步骤。
标准分集界面提供 1／2／4 张；原公共生成接口及画布入口继续共用生成服务、协议适配、任务执行和归档。
所有 ID、版本使用十进制字符串，时间为 UTC。

## 共享图片组

| 方法 | 路径（前缀 `/api/v1`） | 返回／用途 |
| --- | --- | --- |
| GET | `/image-groups/current/{asset\|shot}/{target_id}` | 当前组或 null；旧主图首次读取建立兼容组 |
| GET | `/image-groups/history/{asset\|shot}/{target_id}?offset=0&limit=20` | 当前与历史组分页，limit 1–100 |
| GET | `/image-groups/{group_id}` | 图片组详情 |
| PUT | `/image-groups/{group_id}/primary` | 设置主图，返回更新后的组 |
| GET | `/image-groups/media/{media_id}/content` | 授权同源图片字节，private/no-store |

组包含 `id/kind/status/is_current/target_kind/target_id/expected_count/primary_index/primary_version/target_version/notice/created_at/task_id/items`。
状态为 `generating/completed/partial/failed`；图位包含 `id/output_index/media_id/url/width/height/item_version/status/error/prompt/history`，
图位状态为 `generating/ready/failed`。`history` 项为 `{media_id,url,width,height}`，URL 每次读取现签。

图位按 `output_index` 固定，不按完成顺序重排。第一张失败时，终态将序号最小的成功图设为主图并返回提示。
若来源内容、原主图或用户选择已变化，自动写主图停止，结果仍保存。只改下一次张数不会清空当前组；
公共图片生成回执的 `image_group_id` 标识受理后建立的新组。

主图请求体如下，值须使用当前实际读取结果：

```json
{
  "item_id": "620501",
  "expected_item_version": "2",
  "expected_primary_version": "1",
  "expected_target_version": "7"
}
```

三类版本均校验，冲突返回 409，不自动覆盖。手动选择历史图可恢复该组为当前组；
原组仍保留历史。素材信息已修改时，可重新生成或将核对后的当前图片设为主图。
分镜主图沿用图片原有布局、画幅和分辨率，手动设置时更新当前引用上下文，不改下一次生成设置。

项目成员可读整组、全部图位和原图历史；他人的 `task_id/prompt` 为 null，无法读取其任务和操作。
个人素材组仅本人可访问。content API 只允许属于可访问组或其原图历史的媒体，不能据此读取任意媒体。

## 私人处理记录

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| POST | `/image-operations` | 建立操作，必须带 `Idempotency-Key`，返回 201 |
| GET | `/image-operations/{id}` | 仅本人读取执行状态 |
| POST | `/image-operations/{id}/execute` | multipart，可选 `file`；本地处理或准备 AI 输入 |

创建请求为 `{item_id,expected_item_version,operation_type,parameters,restore_media_id?}`。
同人同键同内容返回原回执，不同内容冲突；仅当前组可创建操作。记录冻结源媒体、图位版本及来源摘要。
回执包含 `id/item_id/operation_type/status/task_id/result_media_id/result_group_id/mask_media_id`，
状态为 `pending/prepared/running/completed/conflict/failed`。

| operation_type | 主要 parameters / file | 行为 |
| --- | --- | --- |
| `replace` | 上传 file，或 `restore_media_id` | 上传替换；历史 ID 必须属于该图位 |
| `crop` | `x/y/width/height`，0–1 的图片内选区 | 自由裁切，保存真实像素尺寸 |
| `annotation` | file | 已合成 PNG，尺寸须与原图一致；前端支持画笔／矩形／文字及撤销重做 |
| `resize` | `output_width/output_height/interpolation` | 两边 1–4096；nearest/bilinear/high；不调用模型 |
| `split` | `rows/columns`，各 1–5，乘积至少 2 | 新组最多 25 张，行优先排序，第一张主图，旧组保留 |
| `mask` | `instructions`、file | 与原图同尺寸、带透明编辑区域的 PNG 蒙版 |
| `emotion` | 人脸选区、`preset/instructions` | 裁切／身份参考；按能力使用蒙版，服务端最终羽化融合 |
| `texture/angle/lighting` | `instructions` 及对应角度／灯光参数 | 使用当前图为参考的单图编辑 |
| `reverse_prompt` | 可选 `instructions` | 文字任务，返回反推提示词，不修改图片 |

本地处理直接进入 completed。单图结果替换原图位，旧媒体保留在该位置历史，编辑非主图不改变主图。
宫格切分产生新组。AI 结果保存时重新检查原图和来源；过期结果保存在非当前历史组，操作为 conflict。
分镜在生成期间归档时仍保存结果，不恢复镜头或更新主图。

AI 操作 execute 得到 prepared 后，再使用公共生成 API：

```json
{
  "config_id": "201",
  "source": {"scene": "image_operation", "operation_id": "70001"},
  "input": {"prompt": "", "reference_media_ids": []},
  "parameters": {"count": 1}
}
```

图片工具使用 `/ai/generations/image`，仅生成一张；反推使用 `/ai/generations/text`，省略图片 input。
服务端从操作记录装配真实提示词、参考图、蒙版和来源，不信任客户端构造的媒体快照。
能力不足在调用前拒绝，不自动移除参考图或降级为无蒙版重绘。
反推当前仅接入声明图片理解能力的 OpenAI Chat Completions／Responses 协议，
校验实际图片字节数及提示词上限；不按模型名推测视觉能力。

操作和生成分别保存原幂等键。生成受理不明时只用 `/ai/generations/by-idempotency-key`
查原请求，返回 null 不代表允许自动再发付费 POST。恢复归档仅保存原任务结果。
本地存储及供应商错误保留原图；页面提供就近错误、读取重试和原请求核对。

预览、下载、复制本人提示词、加入私人参考图为前端入口。私人参考图写入本人创作草稿，
不改变共享图片组。全景和视频加工不属于本次图片工具合同。
