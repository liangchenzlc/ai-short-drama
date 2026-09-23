## 素材提取输出协议

只返回一个 JSON 对象，顶层恰好包含 `schema_version` 和 `items`：`schema_version` 必须是整数 `1`；`items` 必须是数组，数量不超过 `source.max_candidates`，也不超过 100。没有符合条件的素材时返回 `{"schema_version":1,"items":[]}`。

`items` 中的每个对象必须包含以下字段，不得添加其他字段：
- `kind`：仅使用 `source.extraction.kinds` 中的 `character`、`scene` 或 `prop`。
- `name`：非空字符串，最多 255 字符。
- `description`、`prompt`、`story_function`：各为非空字符串，各最多 8000 字符。
- `importance`：只能是 `core` 或 `continuity`。

可选字段仅有：`aliases`（别名字符串数组，最多 20 个，每项最多 255 字符）、`label`（字符串，最多 120 字符）、`tags`（去重的非空字符串数组，最多 20 项，每项最多 40 字符）、`scene_time`（字符串，最多 60 字符，仅 `kind` 为 `scene` 时允许）。缺省时直接省略可选字段；不要输出 `model_id`、`media_id`、`state`、`row_version` 或服务端生成的候选 ID。

整份 JSON 的 UTF-8 大小不超过 1 MiB。
