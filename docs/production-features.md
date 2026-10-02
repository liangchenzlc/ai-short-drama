# 播放恢复、批量生成与声音制作

最新方案采用[角色音色驱动的原生有声视频](plans/2026-09-30-native-video-voice.md)。角色先设计、试听并绑定百炼样音，再让视频模型根据音频参考和分镜对白生成原生人声。下文逐句 TTS 流程仅适用于 `legacy` 历史项目。新功能默认关闭，业务数据库尚未迁移，真实原生音色一致性尚待另行验收。

本次交付包含应用代码、增量迁移、迁移脚本和自动验收。没有对现有业务数据库执行迁移。后续按用户授权完成 ModelHub Mini 真实视频验收，共 3 次 480p / 4 秒生成请求；详见[供应商验收报告](plans/2026-09-30-modelhub-video-acceptance.md)，其中记录了原始输出尺寸和时长偏差。新功能默认关闭，完成下述部署后启用。

## 三个 Goal 的交付边界

| Goal | 完成条件 | 保持不变 | 验证方法 |
| --- | --- | --- | --- |
| 成片播放异常恢复 | 错误保持可见；刷新原任务地址后恢复位置、音量、静音、倍速，停在暂停态；处理超时、失败和离开页面 | 只 GET 原任务，不重新合成、不调用模型、不修改剪辑或候选采用 | Chrome 真实坏媒体请求、连续十轮恢复、晚到事件、重复点击、错误媒体身份、超时及移动屏幕 |
| 分镜与素材批量生成 | 单集或素材库范围预检、勾选确认、持久批次；同配置跨批次共享限流；支持暂停/取消/重启、局部成功与安全重试 | 不自动采用、不跨项目、不自动串联生图生视频；受理不明不重发 | 独立 MySQL、多调度器竞争、丢回执幂等、冻结输入、来源变化暂停、候选保留、浏览器交互 |
| 配音、字幕和配乐 | 配音候选人工采用、角色音色、台词提取后校对；单条上传配乐、单轨 SRT、冻结快照后混音导出 | 单视频时间轴、30fps、裁剪/分割/排序/静音、原有自动保存不变；不自动拉伸声音、延长视频或移动音频时间 | 二进制配音适配、完整解码、原文件与代理留存、MySQL/HTTP/队列、真实 FFmpeg 帧数/声音采样/中文烧录 |

真实供应商的小规模图片/视频批次与中文配音音质、音色可用性须单独验收，不能由本地模拟结果代替。

## 部署

1. 备份数据库，停止接收新生成/导出任务，让旧任务完成；保留任务、候选和媒体。先完成既有 assembly/timeline 迁移。
2. 在 `backend` 目录，以具备目标数据库 DDL 权限的部署账户执行：

   ```powershell
   .\.venv\Scripts\python.exe scripts/generation_batch_ddl.py --apply
   .\.venv\Scripts\python.exe scripts/sound_ddl.py --apply
   .\.venv\Scripts\python.exe scripts/check_db_schema.py
   ```

   批次脚本创建缺失表。声音脚本用单条原子 ALTER 扩展模型、任务、媒体类型约束，再创建声音草稿、角色音色、媒体引用三张表；可重复执行及中断后重跑。SQL 位于 `数据库模型/migrations/2026-09-30-*`。不要用完整建库 SQL 升级现有库。
3. 创建私有音频桶 `MINIO_AUDIO_BUCKET`，默认 `short-drama-audio`。本地 Compose 可运行 `docker compose run --rm minio-init`；该命令也会幂等检查既有图片、视频桶。
4. API、调度器及所有 worker 部署同一版本、同一加密密钥，保持各进程不同的雪花节点编号。新增配音 worker：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_generation.ps1 -Role audio
   ```

   默认队列为 `tasks.ai.audio`；自定义命名空间沿用现有 topology 规则。调度器和 render worker 都必须运行，不能让旧代码 worker 消费新配音消息。
5. FFmpeg 须包含 libx264、AAC、libass。烧录中文字幕需设置 `RENDER_SUBTITLE_FONT_PATH` 与 `RENDER_SUBTITLE_FONT_FAMILY`，API/render worker 必须能访问同一字体文件。Linux 可安装 `fonts-noto-cjk`，使用 `NotoSansCJK-Regular.ttc` / `Noto Sans CJK SC`；Windows 可用 `C:/Windows/Fonts/msyh.ttc` / `Microsoft YaHei`。字体校验值写入导出快照，文件变化时旧任务重试会明确失败，需新建导出。
6. 参照 `backend/.env.example` 启用 `GENERATION_BATCHES_ENABLED=true`、`AUDIO_PRODUCTION_ENABLED=true` 后重启服务。图片批次默认并发 2、视频 1，同配置跨批次共享；手动单任务不计入此限额。
7. 在 AI 配置页添加“配音模型”，手填地址、模型、密钥；音色标识在台词面板填写。支持两种接口：OpenAI 兼容 `POST /audio/speech` 接受 `model/input/voice/response_format=wav` 并返回音频二进制，地址可为 `/v1` 或完整 `/audio/speech`；百炼原生 CosyVoice 使用对应地域或工作空间的 HTTPS `/api/v1` 地址，模型如 `cosyvoice-v3.5-flash`，自动请求 `/services/audio/tts/SpeechSynthesizer`，归档返回的 WAV 地址。不要将 WebSocket 地址或 `/compatible-mode` 作为原生配音地址。v3.5 须先在百炼创建同模型音色，应用不提供创建音色界面；本次可复用的音色及配置见[百炼验收报告](plans/2026-09-30-cosyvoice-acceptance.md)。

回退：先停止新任务，关闭两个开关，保留新表、音频与快照。已有音频后不要回滚类型约束，也不要让旧 worker 处理版本 3 声音快照。旧版本 1/2 成片快照保持原有渲染行为。

## 使用和恢复

- 素材库、分镜可跨当前分页勾选，或明确预检“当前范围全部”。预检区分已有采用、待审核、活动任务、受理不明及依赖缺失，确认后提交。每批最多 100 项，图片每项 1～4 张，视频每项 1 个；任务中心可重开批次记录。
- “恢复保存”只保存已有结果；“重新生成失败项”新建关联批次，可能再次计费。子任务不能绕过批次直接重新生成。模型或待执行来源修改后暂停，需要核对并重新预检。
- 成片工作台的声音面板支持手工台词，或明确发起文本模型提取，查看原文后放入编辑区校对。提取不会自动覆盖已保存台词、采用候选或发起配音。配音读取已保存台词，试听后人工采用。
- 文本、角色、模型或音色变化会使旧配音过期。已有活动任务、受理不明、可恢复保存的任务会阻止同条台词重复生成。角色默认音色仅手动填入，不覆盖旧台词。
- 台词配音从本集声音面板重新生成，以校验当前保存的台词；任务详情可跳回来源。相同有效候选重复采用返回当前状态，不重复修改草稿。
- 所有时间均为绝对毫秒。视频裁剪、顺序或静音变化后要求重新核对，不自动跟随移动。声音与视频分别管理版本，声音保存更新成片版本，导航保存屏障覆盖两者。
- 配乐 MP3/WAV/M4A 最大 100 MiB、60 分钟，完整解码验证，不接受视频轨道。原文件与 48 kHz 试听代理留存，最近 100 个本集上传可重新选择；成片使用原音频混合，输出 48 kHz 双声道 AAC。
- 配乐限一条，可裁剪、循环、淡入淡出、调音量和对白压低配乐；原视频与配音音量独立，片段静音继续有效。大量台词分组混合，限制同时打开的解码器及 Windows 命令长度。
- SRT 使用 UTF-8，单轨、有序、不重叠，可导入编辑和导出已保存版本。从实际配音时长填入字幕后仍需人工核对，不提供字级强制对齐。
- “合成预览”提供后台混音与字幕预览；即时粗剪播放器用于检查视频画面。未核对时间、未采用/过期配音、声音字幕越界会阻止导出。后台失败保留记录，可重试冻结快照。

## 成片合成与导出

- 创建草稿前须在分镜中生成并采用视频；可用数量只计算已采用的视频。无视频的预留片段保留在草稿中，自动跳过时间轴、预览和导出，保留原来的纳入决定；后续采用视频并同步后正常参与剪辑。检测中、失效或不足一帧的素材不能拖入轨道。
- 合成预览和导出前会先保存视频、声音及字幕，再获取最新草稿版本。声音功能关闭时沿用视频合成；启用后仍需完成声音时间核对。首次工作台载入失败可重新加载，不会误报保存成功。
- 预览临时断线后继续追踪原任务，自动恢复状态；取消后等待后台确认。导出进度与剪辑保存独立更新，保存冲突不会隐藏导出结果，打开的导出记录也会刷新状态。
- 导出失败可查看原因并重试冻结快照；成功后直接播放、下载 MP4 或明确设为当前成片。历史成片的片段媒体、缩略图和画幅来自导出快照，后续替换分镜视频不会改变旧成片回看。
- 新媒体检测只对未被活动探测任务覆盖的媒体发起检测；损坏的本地恢复缓存会跳过并重新编码，避免上传失败后的重试持续复用坏文件。

本轮验证包含真实 FFmpeg 合成、隔离 MySQL 中的上传失败恢复/下载/采用，以及 Chrome 中的加载重试、预留片段自动跳过、断线恢复、保存冲突期间的导出状态、声音保存顺序和桌面/手机操作。使用合成测试媒体，未修改现有业务剪辑或发起 AI 生成。

## 验证和已知限制

详细记录见 `plans/2026-09-30-production-completion.md`。工程测试使用本地合成画面、正弦波/静音 WAV 和模拟供应商，不能验证中文音质。

```powershell
# backend
.\.venv\Scripts\python.exe -m pytest tests/unit tests/api -q
# TEST_DATABASE_URL 需使用独立测试账户及以 _test 结尾的数据库名。
# 测试创建随机临时数据库并清理，不使用业务库作为测试库。
.\.venv\Scripts\python.exe -m pytest tests/integration -q
# 真实本地 RabbitMQ/MinIO，使用随机命名空间、音频桶及模拟供应商。
$env:RUN_GENERATION_BROKER_TESTS='1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_generation_broker.py -q
# 完整业务链路，供应商仍为本地替身；实际 RabbitMQ/MinIO。
$env:TEST_PRODUCTION_INFRA='1'
.\.venv\Scripts\python.exe -m pytest tests/integration/test_production_generation.py -q

# frontend
npm.cmd test
npm.cmd run build
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/prepare-timeline-acceptance.ps1
$env:PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='C:\Program Files\Google\Chrome\Application\chrome.exe'
# Windows 另开终端：node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4175
$env:PLAYWRIGHT_EXTERNAL_SERVER='1'
npx.cmd playwright test
```

浏览器媒体验收需支持 H.264 的 Chrome，部分 Playwright Chromium 包不能解码此处 MP4。Windows 使用外部测试 Vite 避免自动 WebServer 退出挂起。

真实供应商验收要记录配置 ID、任务/批次 ID、台词/音色、实际时长、恢复结果，禁止记录密钥。至少覆盖小规模图片/视频批次、视频轮询、中文短句试听、保存故障恢复与最终人工混音/字幕核对。业务数据库只读检查发现文本和图片配置各 1 个；随后使用用户提供的 ModelHub 配置在独立环境完成真实视频单项、分镜批次、查询、保存故障恢复与播放验收，未写入业务模型配置。用户随后授权百炼 CosyVoice 最多 5 次调用，实际 4 次完成音色创建、状态查询及两条中文合成，真实归档、保存恢复、人工采用操作、字幕混音导出和 Chrome 播放均通过；详见[百炼验收报告](plans/2026-09-30-cosyvoice-acceptance.md)。真实图片批次、中文音质试听及最终人工混音验收仍待完成。
