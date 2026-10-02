# ModelHub 视频供应商真实验收

验收日期：2026-09-30。结论：`seedance-2.0-mini` 的真实提交、异步查询、结果下载、应用归档、分镜批次限流和保存失败恢复通过。供应商原始输出存在尺寸及尾部时长偏差，不能标记为精确 480 行 / 4.000 秒交付。

## 授权与调用范围

- 用户指定 `https://api.modelhub.cc`，Mini 480p 试片，最多生成 4 个、每次请求 4 秒。
- 实际生成 POST 共 **3 次**，均返回独立供应商任务 ID 并成功；第 4 次额度未使用。无图片模型、文本模型或配音模型调用。
- 模型 ID：`seedance-2.0-mini`。所有请求固定 `duration=4`、`resolution=480p`、`ratio=16:9`、`functionMode=omni_reference`。
- 文档依据：[ModelHub 快速入门](https://modelhub.cc/quick-start)。提交 `POST /v1/videos/generations`，查询 `GET /v1/videos/tasks/{task_id}`。
- 独立验收脚本在发送 POST 前，用 SQLite 事务持久记录额度；超过 4 次或模型/时长/分辨率不匹配即拒绝发送。网络结果不明也不回退计数，生成 POST 不自动重发。
- 密钥通过隐藏输入读取，在临时数据库加密保存；未写入代码、报告、终端日志或明文环境文件。验收清理后临时库及其中的加密密钥已删除。

## 真实任务与结果

| 场景 | 应用任务 ID | 供应商任务 ID | 结果 |
| --- | --- | --- | --- |
| 单项文生视频 | 363548312793518080 | 2293971b-0476-44f4-afe9-fa25edb44a14 | 成功 |
| 分镜批次第 1 项，上传参考图 | 363549153722109952 | bead5340-7f79-4b3e-93a2-84b4f0ac5f94 | 成功 |
| 分镜批次第 2 项，上传参考图及保存恢复 | 363549153751470081 | b4ca2b45-2be3-4c9d-aaca-ccb611f4e46e | 成功 |

批次 ID：`363549153713721344`，最终状态 `succeeded`，2 项成功。参考图为本地绘制的红色小帆船 PNG，没有额外生图费用。

每个文件实测一致：

| 指标 | 请求 / 实测 |
| --- | --- |
| 请求档位和时长 | 480p，4 秒 |
| 原始像素 | **864 × 496**；不是精确 480 行 |
| 视频轨时长 | **4.041667 秒** |
| 容器 / 音频轨时长 | **4.096 秒** |
| 视频 | H.264，24 fps，97 帧 |
| 音频 | 供应商视频自带 AAC；没有独立配音服务调用 |
| 完整 FFmpeg 解码 | 3 / 3 通过 |
| Chrome 播放、跳转至 2 秒、暂停 | 3 / 3 通过，无媒体错误 |

文件保留供应商原始字节，未通过裁剪、缩放或转码掩盖差异。现阶段可确认该供应商的 480p / 4 秒请求链路可用，不能承诺原始文件具有精确像素或精确 4.000 秒时长。此处 24 fps 为生成素材帧率，未更改应用 30 fps 成片时间轴。

## 应用行为证据

1. 单项与批次创建各重放一次同幂等键，返回同一任务 / 批次，不重复调用供应商。
2. 两个分镜预检通过后创建批次，先暂停；调度派发为 0。恢复后仅第 1 项提交，另 1 项等待。
3. 第 1 项归档成功后才派发第 2 项，同配置视频活动数没有超过 1。执行器每次额外收到一次同版本重复投递，未产生额外 POST。
4. 不同进程多次读取同一持久数据库继续查询和归档，任务 ID 保持不变。
5. 最后一项取得供应商结果后，验收包装层故意拒绝一次本地 MinIO PUT。应用记录 `archive_retrying`，维持 `next_action=save` 和原供应商任务 ID；随后成功恢复保存。
6. 注入失败前和恢复后，POST 计数均为 3。最终 `ShotVideo` 采用记录数为 0，生成结果没有自动采用。

本次用真实供应商、MySQL、MinIO 和现有 `GenerationBatchService` / `GenerationExecutionService` 执行。验收驱动器直接驱动持久调度与 worker 服务逻辑，**本次未通过 RabbitMQ 投递这些付费任务**；实际 RabbitMQ 的独立工程验收记录见[制作功能实施记录](2026-09-30-production-completion.md)。

## 本地交付及复核

以下文件位于本机忽略目录 `.runtime`，不随 Git 提交；MP4 是未经修改的供应商输出：

- [单项视频](../../backend/.runtime/modelhub_acceptance/363548312793518080.mp4)
- [批次视频 1](../../backend/.runtime/modelhub_acceptance/363549153722109952.mp4)
- [批次视频 2](../../backend/.runtime/modelhub_acceptance/363549153751470081.mp4)
- [脱敏任务记录](../../backend/.runtime/modelhub_acceptance/report.json)
- [媒体参数和 SHA-256](../../backend/.runtime/modelhub_acceptance/media-verification.json)
- [Chrome 验收记录](../../backend/.runtime/modelhub_acceptance/browser-verification.json)
- [保存故障注入记录](../../backend/.runtime/modelhub_acceptance/injected-save-failure.json)

无需调用供应商即可重新检查媒体：在 `frontend` 运行 `node .runtime/modelhub-playback.mjs`。该脚本只在本地启动临时 HTTP 服务，用 Chrome 打开上述 MP4，验证播放、拖动和暂停，然后关闭浏览器与服务。

验收独立数据库及本轮专属 MinIO 对象已清理，本地 MP4、报告、参考图和提交额度账本保留。业务数据库未迁移或写入，新功能开关未更改，未在业务配置中新增模型。本地驱动器已标记关闭，继续运行不会发起生成。

配音供应商按用户要求暂不验证。真实图片批次、真实中文配音和最终人工混音验收不属于本次视频供应商验收结论；整体制作 Goal 不据此标记全部完成。
