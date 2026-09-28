# 成片合成与导出 API

路径前缀：`/api/v1/projects/{project_id}/episodes/{episode_id}/assembly`。所有 ID 使用十进制字符串，服务端校验项目、分集及任务归属。

| 方法与路径 | 用途 |
| --- | --- |
| GET 空路径 | 草稿、实际视频时长、来源差异、最近任务及当前成片 |
| POST /initialize | 幂等创建草稿，并提交未完成的视频检测 |
| PATCH 空路径 | 保存完整片段顺序、包含状态、裁剪、静音及清晰度 |
| POST /sync | 明确同步已采用分镜视频 |
| POST /exports | 提交不可变导出快照，返回 202 |
| GET /exports?offset=0&limit=20 | 分页导出记录，返回 items/has_more |
| GET /exports/{id} | 任务状态及播放地址 |
| POST /exports/{id}/cancel | 取消排队或运行中的任务 |
| POST /exports/{id}/retry | 根据原快照创建新任务，返回 202 |
| POST /exports/{id}/apply | 设为本集当前成片 |
| GET /exports/{id}/download | 以附件方式流式下载 MP4 |

PATCH 请求包含 `row_version`、`resolution`（720p/1080p）和 `clips`。每个片段提交 `id`、`included`、`muted`、`trim_in_ms`、`trim_out_ms`，数组顺序就是导出顺序。`trim_out_ms=null` 表示原片结尾。客户端不能提交媒体 ID 或存储地址替换视频。

同步请求包含 `row_version` 和最新 GET 的 `source_hash`。保留编辑顺序，追加新增分镜，取消勾选归档分镜，更换来源时重置裁剪。不同视频的旧裁剪不能直接复用。

导出请求包含 `row_version`、`source_hash`，使用旧来源时还须 `acknowledge_stale_source=true`。导出与重试必须携带 `Idempotency-Key`，网络重发保留同一个键。请求冲突返回 409，缺少视频、尚未检测、非法裁剪或超限返回 422。

采用请求包含 `row_version`、当前草稿 `context_hash`；采用不同于当前草稿的旧成片需 `acknowledge_stale_source=true`。导出成功不会自动采用。

状态为 queued/running/succeeded/failed/cancelled。阶段包括 preparing/rendering/joining/uploading/complete。媒体播放地址有有效期，通过 GET 刷新。GET 不调用 ffprobe，也不下载素材。

实际时长由后台读取视频文件获得，优先使用 ffprobe；开发环境仅有 FFmpeg 时会解码首帧并读取容器信息。片段最多 300 个，默认总时长上限 1 小时。输出 H.264、30fps、AAC 48kHz 双声道、MP4 faststart；保持画幅并补边，静音或无声片段生成静音轨。
