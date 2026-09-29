# 成片合成与导出 API

路径前缀：`/api/v1/projects/{project_id}/episodes/{episode_id}/assembly`。项目、分集、媒体和任务 ID 使用十进制字符串；片段 ID 兼容原有十进制字符串和新增片段的稳定 UUID。服务端校验项目、分集及来源归属。

| 方法与路径 | 用途 |
| --- | --- |
| GET 空路径 | 草稿、实际视频时长、来源差异、最近任务及当前成片 |
| POST /initialize | 幂等创建草稿，并提交未完成的视频检测 |
| PATCH 空路径 | 保存完整片段顺序、包含状态、裁剪、静音及清晰度 |
| POST /sync | 明确同步已采用分镜视频 |
| POST /exports | 提交不可变导出快照，返回 202 |
| POST /previews | 按相同剪辑规则生成 360p 合成预览，复用匹配快照的缓存，返回 202 |
| GET /exports?offset=0&limit=20 | 分页导出记录，返回 items/has_more |
| GET /exports/{id} | 任务状态及播放地址 |
| POST /exports/{id}/cancel | 取消排队或运行中的任务 |
| POST /exports/{id}/retry | 根据原快照创建新任务，返回 202 |
| POST /exports/{id}/apply | 设为本集当前成片 |
| GET /exports/{id}/download | 以附件方式流式下载 MP4 |

GET 返回活动 `clips` 和按分镜去重的 `sources` 素材列表。删除轨道片段不会删除素材；空轨道仍保留素材列表。任务返回只读 `timeline`（片段、来源、入出点、静音），用于准确标识旧成片对应的编辑版本。

PATCH 请求包含 `row_version`、可选 `request_id`、`resolution`（720p/1080p）和完整 `clips`。每个片段提交 `id`、`included`、`muted`、`trim_in_ms`、`trim_out_ms`，数组顺序就是导出顺序。`trim_out_ms=null` 表示原片结尾。新片段使用小写 UUID，并附 `source_clip_id` 引用本集已有片段；服务端复制来源媒体。客户端不能提交媒体 ID 或存储地址替换视频。一次提交也可以继续引用本次此前声明的新片段。

未提交的片段从轨道移除，记录仍保留，便于会话内撤销和避免同步后自动重新添加。重复 ID、未知来源、跨分集来源及不足一帧的裁剪被拒绝。新客户端用 `request_id` 重试同一次保存，网络重试不会重复创建片段；相同标识不同内容返回 409。旧客户端不提交该字段时仍按版本冲突处理。

同步请求包含 `row_version` 和最新 GET 的 `source_hash`。保留编辑顺序和分割、裁剪，追加新分镜，归档分镜不参与输出。明确替换来源时更新该分镜的所有片段，超出新视频长度的裁剪会标记为无效，不静默截短。已移除片段不会重新加入。前端同步后清空撤销历史，防止撤销到不同媒体版本。

导出/预览请求包含 `row_version`、`source_hash`，使用旧来源时还须 `acknowledge_stale_source=true`。导出、预览与任务重试必须携带 `Idempotency-Key`，网络重发保留同一个键。请求冲突返回 409，缺少视频、尚未检测、非法裁剪或超限返回 422。预览任务通过相同的任务查询、取消和下载接口访问，不出现在正式导出记录中，也不能采用为正式成片。新预览请求取消同集过期的未完成预览。

采用请求包含 `row_version`、当前草稿 `context_hash`；采用不同于当前草稿的旧成片需 `acknowledge_stale_source=true`。导出成功不会自动采用。

状态为 queued/running/succeeded/failed/cancelled。阶段包括 preparing/rendering/joining/uploading/complete。媒体播放地址有有效期，通过 GET 刷新。GET 不调用 ffprobe，也不下载素材。

实际时长由后台读取视频文件获得，优先使用 ffprobe；开发环境仅有 FFmpeg 时会解码首帧并读取容器信息。片段最多 300 个，默认总时长上限 1 小时。输出 H.264、30fps、AAC 48kHz 双声道、MP4 faststart；保持画幅并补边，静音或无声片段生成静音轨。

新导出快照 version 2 将入出点按 30fps 四舍五入到整数帧，分割共用一个边界。中间片段使用 PCM 音频，最终拼接时编码一次 AAC；最终视频时长误差上限 40ms，不按片段数累加放宽。历史 version 1 快照保持兼容。

检测任务会生成 360p、30fps H.264 播放代理、代表缩略图和最多 24 格的连续采样拼图，每格 160×90，将托管对象地址及格数/时间间隔存入只读 `video_metadata`。GET 的片段及来源返回可空 `filmstrip: {url, count, interval_ms}`，URL 为短期签名地址；裁剪坐标仍基于原视频，导出仍读取原始媒体。已有草稿调用幂等 `/initialize` 仅补齐缺失的预览素材，不同步来源、不修改 row_version 或剪辑；前端打开时触发一次，轮询读取结果。也可通过“同步分镜视频”补齐代理。预览与正式导出共用独立 render Worker，调度器优先投递正式导出；运行中的任务不抢占。
