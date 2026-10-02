# 百炼 CosyVoice 真实配音验收

2026-09-30，按用户提供的百炼北京工作空间地址接入 `cosyvoice-v3.5-flash`。用户授权最多 5 次调用，本次将创建音色、查询音色和合成均计入，实际共 **4 次**，全部 HTTP 200；剩余 1 次未使用。没有再次生成视频。

## 目标、边界与验证

1. **目标与完成条件**：项目支持百炼原生非流式 HTTP 配音；官方样本创建的音色可合成中文；经过持久任务、真实对象存储、候选人工采用后，能导出包含配音和中文字幕的样片。真实供应商连通、媒体与导出链路已通过；中文发音、情绪和音色适配须人工试听确认。
2. **不能改**：合计不超过 5 次调用；不重复创建音色；受理不明不自动重发。保存故障恢复已有结果，不重新生成；保留单视频时间轴、30fps、绝对时间、候选人工采用和原有 OpenAI 兼容配音适配器。业务数据库未迁移、未写入模型配置，新功能开关未开启。不增加通用音色克隆界面。
3. **如何验证**：持久 SQLite 台账在每次请求前扣减额度；独立 MySQL 与三个私有 MinIO 测试桶运行真实任务执行器；校验请求幂等和重复消息、故障恢复不增加调用、完整解码、采样率/时长/帧数、字幕图像和 Chrome 播放/拖动/暂停。付费任务直接驱动执行器，未经过 RabbitMQ；音质没有用自动化结果代替人工听审。

## 供应商与调用记录

官方合同参考：[HTTP 合成接口](https://help.aliyun.com/zh/model-studio/cosyvoice-tts-http-api)、[音色复刻接口](https://help.aliyun.com/zh/model-studio/voice-clone-design-http-api)。本模型没有预置音色，因此仅使用用户示例中官方公开音频创建一次音色。

| 次数 | 操作 | 结果 | Request ID |
| --- | --- | --- | --- |
| 1 | 创建官方样本测试音色 | 创建成功 | `0d072f4e-8d0d-9f31-8f53-0df31d3c810d` |
| 2 | 查询音色 | `OK`，目标模型一致 | `292b713f-dfdd-9899-b78a-f3df09dcb912` |
| 3 | 中文短句 1 合成 | 成功，3.28 秒 | `2ef6a8d0-7f08-9cf0-be5d-243f9938d627` |
| 4 | 中文短句 2 合成 | 成功，3.52 秒 | `324d9e4d-20b2-9b5c-916f-0a4f1099999c` |

可复用配置：

| 字段 | 值 |
| --- | --- |
| 服务类型 | `audio` / 配音模型 |
| 供应商 | 阿里云百炼 |
| Base URL | `https://ws-0e96b40vjn2kf77o.cn-beijing.maas.aliyuncs.com/api/v1` |
| 模型 | `cosyvoice-v3.5-flash` |
| 台词面板音色 ID | `cosyvoice-v3.5-flash-dramatest-1e1da3808d034cbb8f4be09c39cd01f2` |
| 密钥 | 配置页填写用户提供的密钥，本文不记录 |

适配器 `dashscope_speech.v1` 请求 `/services/audio/tts/SpeechSynthesizer`，使用 `input.text/voice/format=wav/sample_rate=24000`。模型返回 JSON 音频地址，先加密存入恢复清单，再下载验证和归档。此地址通常 24 小时有效，应及时恢复保存；过期不能保证再次下载，不自动补发合成。用户给出的 WebSocket 地址仅用于 SDK 实时方案，本次验证的是同工作空间的 HTTP 接口。

## 实际产物

| 台词 | 任务 ID | 原始音频 |
| --- | --- | --- |
| 别担心，我会找到回家的路。 | `363566568514260992` | WAV / PCM 16-bit / 24 kHz / 单声道 / 3.28 秒 |
| 天快亮了，我们现在就出发。 | `363566568627507200` | WAV / PCM 16-bit / 24 kHz / 单声道 / 3.52 秒 |

- 两条音频完整 FFmpeg 解码成功；非静音，采样峰值分别约 0.421、0.386。
- 第二条归档注入一次本地 MinIO PUT 失败；任务从 `archive_retrying` 恢复成功，调用台账保持 4 条。
- 每条生成任务创建请求及执行消息均重放一次，无重复合成、无重复候选。生成后两条台词都未自动采用；随后显式采用，并验证重复采用幂等。
- 实际导出任务 `363567356368130048`：1280×720、10.000 秒、30fps / 300 帧、AAC / 48 kHz / 双声道。台词从 0 秒和 5 秒开始；中文字幕按实际音频时长生成并烧录。
- 演示画面为本地纯色，配乐轨为本地生成的 220 Hz 测试音，验证循环、淡入淡出和对白压低配乐，不代表正式配乐或视频供应商效果。
- Chrome 对两条 WAV 与混音 MP4 的播放、拖动、暂停全部通过；字幕截图核对中文内容可见。
- 供应商 `usage.characters` 每句均为 24，应用按字符串记录 `input_characters=13`；两种统计均保留，不据此推算账单金额。

本地保留文件（`.runtime` 已被 Git 忽略）：

- [短句 1](../../backend/.runtime/cosyvoice_acceptance/line-1.wav)、[短句 2](../../backend/.runtime/cosyvoice_acceptance/line-2.wav)
- [混音字幕样片](../../backend/.runtime/cosyvoice_acceptance/mixed-preview.mp4)、[SRT](../../backend/.runtime/cosyvoice_acceptance/subtitles.srt)
- [Chrome 截图](../../backend/.runtime/cosyvoice_acceptance/mixed-preview-chrome.png)
- `report.json`、`media-verification.json`、`render-verification.json`、`browser-verification.json`、`calls.sqlite` 提供调用与媒体证据。

## 清理与限制

工程回归：后端单元/API 全量 **543 passed**，声音制作独立 MySQL 集成 **4 passed**，包含新增百炼协议、无效回执和未知受理保护用例。Ruff 检查及格式检查、`git diff --check` 通过。集成测试启动器曾因模块路径和 Windows 系统临时目录权限失败，改用项目模块路径及专用临时目录后 4 项全部通过；这些测试没有调用真实模型。

独立数据库、三个私有测试桶及其中的媒体、临时加密凭据均已清理。云端测试音色保留，可在相同地域/工作空间和模型下复用；删除它需要另一次供应商请求，本次未执行。

当前应用代码支持该配置，业务环境仍需按[部署说明](../production-features.md)迁移、启用声音功能并填写配置。未做长期稳定性、并发压测、长文本、多音字、角色一致性或人工音质验收。真实图片批次与最终人工成片核对仍未完成，不应将整体三个 Goal 标记为全部验收完成。
