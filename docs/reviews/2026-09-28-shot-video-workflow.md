# 分镜图生视频实施与验收

> 后续用户明确分镜图应作为全能参考，允许四宫格、五宫格等。本文中的单首帧设计已由 [全能参考修正记录](2026-09-28-shot-video-omni-reference.md) 替代；下文保留作为首次实施的历史记录。

日期：2026-09-28。第一版代码、数据库迁移、页面交互与本地协议验证完成；未提交真实付费视频任务，供应商实际出片质量及账户权限尚未验证。

## 使用流程

进入分集的分镜制作，选中分镜，切换到「分镜视频」。系统使用当前已采用的单张分镜图作为首帧，自动组合分镜内容、关联人物/道具/场景说明和项目风格。用户检查或修改视频提示词，点击生成，播放候选并采用。系统提示词可展开查看；无需额外调用文本模型或重复上传首帧。

系统提示词参考 `C:/Users/snow/code/drama-skills` 中视频提示词技能的动作因果、空间连续性、运镜与光线规则，落在 `backend/src/short_drama/ai/prompts/shot_video/system.md`。用户部分说明动作、运镜、环境光线与结束状态，沿用已保存的分镜时长。接口不支持独立 system 消息时，发送系统与用户部分合并的文本，任务中分别保存两部分和模板版本。

第一版限定单镜、单首帧；人物/道具/场景通过首帧和文字上下文保持一致，不额外传入设定板图片。不含批量视频、宫格拆分、自动剪辑或精确口型配音。

## 实现约束

- 生成前保存草稿，绑定分镜版本、上下文哈希和首帧媒体 ID；缺图、宫格图、过期图或模型参数不匹配时阻止提交。
- `shot-video-context-v1` 覆盖分镜上下文、首帧、视频提示词与设置；修改视频提示词只让视频过期，不影响分镜图。
- 新结果进入候选，明确采用后才成为当前视频；旧来源采用需确认，并保留过期标记。并发冲突不能用确认旧来源绕过。
- 复用任务、幂等、历史、归档、取消和条件恢复机制；提交结果未知时保留保护，避免自动重复收费。
- 新增字段为 `shot_scripts.video_prompt/video_settings` 与 `shot_videos.context_hash/first_frame_media_id`。迁移位于 `docs/数据库模型/migrations/2026-09-28-shot-video/`，应用脚本为 `backend/scripts/apply_shot_video_migration.py`，本地业务库已执行并通过结构检查。

## 当前 ModelHub 配置

当前 `seedance-2.0-mini` / `https://api.modelhub.cc` 已接入独立适配器 `modelhub_video.v1`，本地运行中能力接口已返回首帧支持。

- 提交：`POST /v1/videos/generations`，使用 multipart 上传 `image_file_1`，`functionMode=first_last_frames`、`ratio=auto`。
- 查询：`GET /v1/videos/tasks/{task_id}`，成功视频取 `result.url`，沿用现有媒体归档。
- 文档参数范围：4–15 秒、480p/720p。项目现有分镜时长上限 10 秒，页面实际可生成 4–10 秒，不自动拉长短镜头。
- Worker 从对象存储读取已绑定的首帧；检查 PNG/JPEG/WebP 和单图 10 MiB 上限，不向供应商传本机 MinIO URL，不在任务参数中保存图片二进制。
- 公开目录说明该模型为自动路由，实时可用渠道决定文件输入等能力。本地能力检查不能证明账户或渠道可用，不以协议测试替代真实出片验收。

协议依据为 2026-09-28 读取的 [ModelHub 快速开始](https://modelhub.cc/quick-start) 和 [公开模型目录](https://modelhub.cc/api/public/models)。其他 ModelHub 模型未宣称已支持。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 后端 unit + API | 495 passed |
| 分镜视频/分镜/制作集成测试 | 6 passed，1 skipped |
| 前端 Node 测试 | 108 passed |
| TypeScript + Vite 生产构建 | 通过，保留既有 chunk 大小提示 |
| Ruff 检查及格式检查 | 通过，245 个文件 |
| 浏览器分镜视频验收 | 保存后提交、首帧绑定、播放、采用、重新加载恢复、窄屏布局、冲突保留草稿均通过 |
| 本地 API、数据库、MinIO 健康接口 | 均返回 200 |

后端协议测试通过真实本地 HTTP 检查 multipart 图片字节、首尾帧顺序、查询地址、参数拒绝和未知结果处理；没有使用真实供应商生成。MySQL 集成测试使用独立临时容器，覆盖 Ark 与 ModelHub 执行器首帧读取、归档、候选、采用和过期，以及旧结构升级。跳过项为显式选择启用的真实 RabbitMQ/MinIO 集成测试；pytest 缓存权限提示不影响断言结果。

浏览器脚本：`frontend/scripts/shot-video-acceptance.mjs`。使用隔离接口数据和浏览器生成的可播放 WebM，未修改业务分镜或调用付费接口。桌面与手机截图位于 `output/playwright/shot-video-*.png`。

本地 API、scheduler 和各类型 Worker 已在确认无活动任务后重新加载；临时 MySQL 测试容器已停止。未提交或推送 Git，工作区原有改动保留。
