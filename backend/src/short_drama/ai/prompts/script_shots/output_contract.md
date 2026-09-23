## 分镜输出协议

只返回一个 JSON 对象，顶层仅有 `shots` 字段。`shots` 必须是包含 1～100 个对象的数组；按剧本原文顺序排列，不能省略必填字段，也不能添加 `id`、`position` 等字段。

每个镜头对象恰好包含以下字段：
- `title`：非空字符串，最多 255 字符。
- `source_excerpt`：非空字符串，最多 8000 字符；必须是从 `source.content` 逐字复制的连续片段，不改写、不省略、不拼接；各镜头引用的片段依原文顺序出现。
- `story_beat`：非空字符串，最多 8000 字符。
- `script`：非空字符串，UTF-8 编码不超过 32768 字节。
- `duration_ms`：整数，范围 1000～10000，不得写成字符串；结合 `source.storyboard.average_shot_duration_ms` 调整镜头分配。
- `asset_ids`：数组，最多 50 项；每项为 `source.assets` 中已有素材的十进制 ID 字符串，不得重复；无匹配素材时使用 `[]`。

整份 JSON 的 UTF-8 大小不超过 1 MiB。不要返回 `schema_version`、解释或多余字段。
