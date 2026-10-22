# 成片合成与导出 API

路径前缀：`/api/v1/projects/{project_id}/assembly`。项目、媒体和任务 ID 使用十进制字符串；片段 ID 兼容原有十进制字符串和新增片段的稳定 UUID。服务端校验项目及来源归属。

两种编辑保存入口都支持 `aspect: auto | 16:9 | 9:16`，默认 `auto`。自动模式按时间轴选择首个有效分镜视频，其次导入视频，使用服务端探测的显示宽高；无视频使用 16:9。720p／1080p 按短边缩放，预览短边为 360。探测未完成或失败不能猜测比例。同步、重排和探测不会覆盖手动选择。草稿返回 `output_width/output_height/output_source/output_status`，任务冻结前三项，Worker 按快照执行。完整规则见[创作模式维护说明](../instruction/创作模式维护说明.md#成片输出尺寸)。

| 方法与路径 | 用途 |
| --- | --- |
| GET 空路径 | 草稿、实际视频时长、来源差异、最近任务及当前成片 |
| GET /editor | 标准多轨编辑器的画面、声音、素材与版本组合状态 |
| PUT /editor | 原子保存整份画面、声音、字幕与构图 |
| POST /initialize | 幂等创建草稿，并提交未完成的视频检测 |
| PATCH 空路径 | 兼容旧版单画面保存；不得覆盖已经写入的 v2 多轨草稿 |
| POST /sync | 明确同步已采用分镜视频 |
| GET /materials | 当前项目最近 300 个已发布图片、视频及音频素材 |
| POST /materials/import | 将本人媒体或当前项目媒体接入成片素材 |
| POST /materials/upload | multipart 本地素材上传，字段名 file |
| GET /transcription-models | 本地 Whisper 配置状态与本人可选云端识别模型 |
| POST /transcriptions | 冻结整集或当前片段的人声并提交字幕识别，返回 202 |
| POST /audio-analysis | 提交实际波形、LUFS、峰值与响度增益分析，返回 202 |
| GET /media-jobs | 本人最近 40 个识别及分析任务 |
| GET /media-jobs/{id} | 本人媒体任务状态、结果及恢复许可 |
| POST /media-jobs/{id}/cancel | 请求取消本人的媒体任务 |
| POST /media-jobs/{id}/acknowledge | 明确记录原识别任务已核实，保留原任务及错误 |
| POST /exports | 提交不可变导出快照，返回 202 |
| POST /previews | 按相同剪辑规则生成 360p 合成预览，复用匹配快照的缓存，返回 202 |
| GET /exports?offset=0&limit=20 | 分页导出记录，返回 items/has_more |
| GET /exports/{id} | 任务状态及播放地址 |
| POST /exports/{id}/cancel | 取消排队或运行中的任务 |
| POST /exports/{id}/retry | 根据原快照创建新任务，返回 202 |
| POST /exports/{id}/apply | 设为本项目当前成片 |
| GET /exports/{id}/download | 以附件方式流式下载 MP4 |
| GET /current/download | 流式下载本项目明确采用的当前成片，无需访问作者私有任务 |

GET 返回活动 `clips` 和按分镜去重的 `sources` 素材列表。删除轨道片段不会删除素材；空轨道仍保留素材列表。任务返回只读 `timeline`（片段、来源、入出点、静音），用于准确标识旧成片对应的编辑版本。

## 多轨组合编辑

标准前端使用 `/editor`。未初始化时 `assembly=null`，先调用 `/initialize` 后重新读取。已初始化时返回基础聚合字段及 `editor_version=2`、`sound`、`materials`、`subtitle_font`、`voice_context_hash`。`sound` 包含声音版本、文档、媒体试听地址和原有配音信息。签名地址只用于读取，不写入草稿或保存请求。

`GET /editor?material_ids=123,456` 可为本机恢复补齐最近 300 条之外的媒体元数据，最多 900 个正 unsigned 64-bit 十进制 ID；仍只返回当前项目已发布且有权读取的媒体，缺失或越权 ID 不返回。非法 ID 返回 422。

`PUT /editor` 请求：

| 字段 | 规则 |
| --- | --- |
| `schema_version` | 固定为 2 |
| `row_version` / `sound_row_version` | 当前画面／声音版本的十进制字符串；声音未创建时为 `"0"` |
| `request_id` | 1–128 字符，同次保存重发保持相同标识和完整内容 |
| `resolution` | `720p` / `1080p` |
| `clips` | 完整有序数组，最多 300 段；总输出时长最多一小时 |
| `sound` | 下述声音文档，新编辑器使用 `schema_version=2` |

两个版本同时校验；声音验证或任一写入失败，画面与声音全部回滚。409 保留本地整份输入等待明确处理。相同 `request_id` 与相同完整请求重放原保存，不重复创建片段；复用标识但更改请求返回 409。成功响应返回组合状态。旧声音接口的 v1 文档同样不能覆盖已存在的 v2 文档。

片段沿用 `id`、`source_clip_id`、`included`、`muted`、整数毫秒入出点，并增加：

| 字段 | 规则 |
| --- | --- |
| `source_type` | `shot` / `video` / `image`；分镜来源仍核对本项目归属，独立来源须先接入当前项目 |
| `media_id` | 合法来源媒体 ID；服务端核对类型、项目、时长及权限，不能传 URL |
| `display_duration_ms` | 图片展示时长，34–3600000ms；视频来源不使用图片时长 |
| `transform` | `{fit: "contain" / "cover", scale: 1–5, x: 0–1, y: 0–1}`，默认适应、1 倍、居中 |

`shot_id` 在独立视频／图片片段中为空。新增片段 ID 使用稳定 UUID；`source_clip_id` 只能引用合法片段，不可提交客户端素材栏的临时条目标识。未提交片段从轨道移除，但不删除来源素材。

声音文档保留 v1 兼容字段，同时增加以下 v2 字段。时间均为 0–3600000 的整数毫秒，媒体 ID 仍为字符串。

| 字段 | 规则 |
| --- | --- |
| `picture_locked` | 画面轨的编辑锁定状态 |
| `tracks` | 原声 `original`、对白 `dialogue`、字幕 `subtitles`、配乐 `music`、音效 `effect`；每轨包含 `id`、`muted`、`locked`，ID 不重复 |
| `audio_clips` | 最多 600 段，每段包含稳定 `id`、`kind`、`media_id`、`name`、`start_ms`、`duration_ms`、`trim_in_ms`、`trim_out_ms`、`volume`（0–2）、`gain_db`（-60–24）、`muted`、`loop`、`loop_offset_ms`、`fade_in_ms`、`fade_out_ms`、`ducking` |
| `anchor_clip_id` / `anchor_source_in_ms` | 声音、字幕可选的关联画面与来源坐标；为空时固定在整集时间，画面编辑不移动它 |
| `dialogue_id` | 声音片段可选的旧配音来源标识，明确采用配音时据此稳定更新，不重复入轨 |
| `subtitles` | 最多 2000 条，包含 `id`、`start_ms`、`end_ms`、非空 `text` 及可选锚点；按时间排列且不能重叠 |
| `subtitle_style` | `color`、`outline_color`、`outline_width`（0–8）、`background`、`background_color`、`background_opacity`（0–1）、`alignment`（1–9）、`margin_percent`（0–30）、`bold` |
| `burn_subtitles` / `font_size` | 是否烧录；字号 12–72，字体元数据由服务端冻结 |

循环偏移用于分割／裁剪后的连续播放，不能在第二段重新从循环起点开始。轨道静音影响预览、导出及识别来源；锁定限制直接编辑，关联声音仍随画面编辑更新。同步分镜和完成视频检测会同步声音锚点并按需补齐原声，相关版本随实际修改递增。

## 素材、识别与声音分析

素材读取返回 `{media_id,name,kind,duration_ms,width,height,url,poster,has_audio}`。导入请求为 `{media_id}`，仅接受当前项目或本人个人媒体；个人媒体复制成当前项目独立文件，不带入私人生成记录。上传接受位图图片、视频和音频，按真实文件检测，拒绝 SVG、空文件、超配置大小或超过一小时的素材。导入／上传本身不自动修改时间轴，入轨后通过组合保存生效。

识别模型列表返回 `{local_available,models:[{id,name}]}`。本地状态只表示地址已配置，不等于服务健康。识别请求为 `{row_version,clip_id?,language,model_config_id?}`，不传片段表示整集；不选模型使用独立 Whisper，不因本地不可用自动回退云端。云端必须是本人启用且声明转写能力的 OpenAI 音频模型。

识别及分析必须带 `Idempotency-Key`，受理不明保留原体及原键核实。识别冻结画面时间、实际人声轨和模型版本，混合原声／对白，排除配乐、音效和静音。结果只返回字幕建议，不修改当前字幕。结果包含 `subtitles`、`language`、`start_ms`、`end_ms`、`ranges`、`source_hash`、`voice_context_hash`；前端预览后明确应用并通过 `/editor` 保存。跨镜头分句按字符比例估计时间，不提供词级对齐。应用前重新核对人声上下文；仅字幕、配乐、构图、名称或锁轨改变不作废识别。

声音分析请求为 `{media_id,trim_in_ms,trim_out_ms?}`，仅限当前项目音视频且范围不能超过真实时长；结果包含分析范围、最多 1024 个波形采样、`integrated_lufs`、`peak_db`、`gain_db`，静音的响度／峰值可为 null。相同范围的已成功或活动分析仅在本人任务中复用；统一响度目标为 -16 LUFS、峰值 -1dB，实际增益仍由编辑保存。

媒体任务响应包含 `id`、`kind`、`status`、`stage`、`progress`、`can_cancel`、`can_retry`、`error`、`result`。任务列表、详情、取消与核实均只对发起人开放。未提交渠道的失败／取消任务可主动以新键重建；相同旧键始终读取原任务。云端发出前持久标记 `result.submitted`；发出后失败不自动恢复，也不能换键绕过。用户到渠道核实后调用 `/acknowledge`，仅限已结束的识别任务，记录 `reconciled` 与 UTC `reconciled_at`，之后才允许主动新建；确认不删除错误，不将失败改成成功。运行中任务先等待结束。

## 旧版编辑与成片导出

导出任务、预览及历史仅对发起人开放。GET 聚合响应另含 `current_work: null | {media_id,url,width,height,duration_ms,is_stale}`，项目所有者可播放和下载明确采用的成片；该投影不包含作者的任务 ID、快照或生成参数。临时地址失效后重新 GET 聚合响应，刷新只更新当前作品和任务进度，不能覆盖本地剪辑草稿。

PATCH 请求包含 `row_version`、可选 `request_id`、`resolution`（720p/1080p）、`aspect`（auto/16:9/9:16）和完整 `clips`。每个片段提交 `id`、`included`、`muted`、`trim_in_ms`、`trim_out_ms`，数组顺序就是导出顺序。`trim_out_ms=null` 表示原片结尾。新片段使用小写 UUID，并附 `source_clip_id` 引用本项目已有片段；服务端复制来源媒体。客户端不能提交媒体 ID 或存储地址替换视频。一次提交也可以继续引用本次此前声明的新片段。

未提交的片段从轨道移除，记录仍保留，便于会话内撤销和避免同步后自动重新添加。重复 ID、未知来源、跨项目来源及不足一帧的裁剪被拒绝。新客户端用 `request_id` 重试同一次保存，网络重试不会重复创建片段；相同标识不同内容返回 409。旧客户端不提交该字段时仍按版本冲突处理。

同步请求包含 `row_version` 和最新 GET 的 `source_hash`。保留编辑顺序和分割、裁剪，追加新分镜，归档分镜不参与输出。明确替换来源时更新该分镜的所有片段，超出新视频长度的裁剪会标记为无效，不静默截短。已移除片段不会重新加入。前端同步后清空撤销历史，防止撤销到不同媒体版本。

导出/预览请求包含 `row_version`、`source_hash`，使用旧来源时还须 `acknowledge_stale_source=true`。导出、预览与任务重试必须携带 `Idempotency-Key`，网络重发保留同一个键。请求冲突返回 409，缺少视频、尚未检测、非法裁剪或超限返回 422。预览任务通过相同的任务查询、取消和下载接口访问，不出现在正式导出记录中，也不能采用为正式成片。新预览请求取消同集过期的未完成预览。

采用请求包含 `row_version`、当前草稿 `context_hash`；采用不同于当前草稿的旧成片需 `acknowledge_stale_source=true`。导出成功不会自动采用。

状态为 queued/running/succeeded/failed/cancelled。阶段包括 preparing/rendering/joining/uploading/complete。媒体播放地址有有效期，通过 GET 刷新。GET 不调用 ffprobe，也不下载素材。

实际时长由后台读取视频文件获得，优先使用 ffprobe；开发环境仅有 FFmpeg 时会解码首帧并读取容器信息。片段最多 300 个，默认总时长上限 1 小时。输出 H.264、30fps、AAC 48kHz 双声道、MP4 faststart；默认保持画幅并补边，也可按已保存构图填充／缩放。多轨声音按文档混合，静音或无声时间生成静音轨，字幕使用冻结样式和字体烧录。画面快照版本 2、声音快照版本 1／2 分别保留兼容读取。

新导出快照 version 2 将入出点按 30fps 四舍五入到整数帧，分割共用一个边界。中间片段使用 PCM 音频，最终拼接时编码一次 AAC；最终视频时长误差上限 40ms，不按片段数累加放宽。历史 version 1 快照保持兼容。

检测任务会生成 360p、30fps H.264 播放代理、代表缩略图和最多 24 格的连续采样拼图，每格 160×90，将托管对象地址及格数/时间间隔存入只读 `video_metadata`。GET 的片段及来源返回可空 `filmstrip: {url, count, interval_ms}`，URL 为短期签名地址；裁剪坐标仍基于原视频，导出仍读取原始媒体。已有草稿调用幂等 `/initialize` 不同步来源或改写画面剪辑，仅提交缺失的检测；检测完成若确实改变 v2 关联声音，会同时递增声音与画面版本，客户端须核对新版本。前端打开时触发一次并轮询读取结果。也可通过“同步分镜视频”补齐代理。预览与正式导出共用独立 render Worker，调度器优先投递正式导出；运行中的任务不抢占。

部署、迁移、字体和验证边界见[成片编辑器维护说明](../instruction/标准成片编辑器维护说明.md)。本次不增加导出前检查步骤，导出成功仍须明确设为当前成片。
