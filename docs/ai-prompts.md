# AI 创作提示词与接口验证

2026-09-24：按用户审批后的方案接入五类业务提示词，版本统一升级为 v2。只改变生成指令及其版本记录，不改变 HTTP 请求、模型结果 JSON、数据库表或候选采用流程。

## 入口与模板

模板根目录为 `backend/src/short_drama/ai/prompts/`，通过包资源加载，可随 wheel 一起部署。

| 业务场景 | 模板 | 主要行为 |
| --- | --- | --- |
| `novel_script` | `novel_script/system.md` | 忠于原著因果、可表演动作与对白、单集节奏与连续性；输出剧本正文 |
| `script_assets` | `script_assets/*.md` + 按类别加载 `asset_views/*.md` | 先提取实体，再自动补全视觉设计，输出人物/道具/场景分项图片提示词及既有 JSON |
| `script_shots` | `script_shots/*.md` | 按叙事节拍拆镜；在现有 `script` 字符串中组织场景、画面、起点、动作、终点、声音、冻结首帧 |
| `asset_image` | `asset_image/system.md` + 对应 `asset_views/*.md` | 人物与道具默认四视图，场景固定单图；注入已保存素材与本次补充要求 |
| `shot_image` | `shot_image/system.md` + 单张/多格规则 | 单张冻结起点，多格展示本镜动作；不将资产四视图复制为分镜布局 |
| `shot_video` | `shot_video/system.md`，版本 shot-video-v2 | 单图或宫格作为全能参考，不锁定首帧；按分镜组织动作、表演、运镜、光线与终点，成片不复制宫格边框和编号；系统和用户部分独立记录，视频接口接收合并文本 |

装配入口为 `ai/business_prompts.py` 和 `ai/prompts/registry.py`，版本由 `PROMPT_VERSIONS` 管理。`GenerationContextService` 在创建任务时冻结提示词和来源数据；历史任务重试仍使用原先冻结输入，不静默替换旧任务。

人物四视图：左侧约三分之一面部特写，右侧全身正面、角色自身左侧面、背面。道具四视图：2×2 正面、侧面、背面、关键细节。场景：一个地点、时间状态与观察方向的一张完整图。

人物和道具设定板建议 16:9；当前 API 支持 16:9、9:16、1:1、4:3、3:4，不新增 21:9。最终以实际生成参数为准，候选数量仍表示图片张数，不表示四视图中的视图数量。

## 视觉补全

剧本未描述的普通视觉细节由 AI 根据时代、世界观、人物职业、经济条件、经历、性格、阵营、行为、剧情用途和项目画风直接补全，不新增用户确认步骤。已给事实与既有设计优先，不能借补全制造新事件、证据、秘密或剧情性伤势。

设计写入 `description` 和分项 `prompt`，不冒称原文明示，不标“待确认”。项目画风必须展开写入素材 `prompt`，因为独立素材出图没有单独的项目风格字段。跨次生成只有输入提供了已有设定或参考图时才有复用依据。

补全视觉设计不等于自动采用素材或生成图片：既有候选、版本检查、确认图片和采用结果流程保持原有行为。

## 来源

- 本地 `drama-skills`：`short-drama-write`、`short-drama-assets`、`short-drama-image-prompts`、`short-drama-storyboard`，以及人物/地点/道具参考图、身份与变体、冻结关键帧等专项指导。
- [公开四视图版式参考](https://github.com/liangdabiao/Seedance2-Storyboard-Generator/blob/main/.claude/skills/seedance-storyboard-generator/SKILL.md) §4.3：借鉴面部特写＋全身三视图布局，没有照搬固定身材比例。
- [多视图一致性问题记录](https://github.com/RainNameless/aigccat/issues/1)：角色自身左右、姿态、表情与不对称特征的连续性。该记录是问题讨论，不作为出图效果已验证的证据。

## 验证记录

- `tests/unit tests/api`：449 项通过，覆盖提示词装配、来源快照、结构化输出校验、参考图片和 API 契约。
- 隔离 MySQL 8.4：`test_business_prompt_api.py`、`test_asset_image_generation.py`、`test_production_generation.py` 共 33 项通过、1 项跳过。使用一次性容器和随机测试库，测试结束已清理，不改动应用库。
- 新增 10 个 HTTP 场景：三种文本入口、三类素材图片、四种分镜图片布局；验证 HTTP 202 创建/200 幂等重放、提示词与版本快照、任务执行、中文多行结果解析、图片归档与候选保存。
- wheel 构建成功，19 份非空 UTF-8 Markdown 模板均进入安装包，隔离 Python 可从 wheel 加载全部 10 种组合。
- Python lint 与格式检查通过。

数据库验证使用真实 MySQL、FastAPI 路由和业务服务；模型及对象存储为可控测试替身。未调用付费模型、未评测真实四视图画质。跳过项为显式 opt-in 的真实 RabbitMQ/MinIO 联合测试；数据库测试通过不能替代这些外部设施验证。

运行集成测试时需给 `TEST_DATABASE_URL` 配置专用测试服务器（数据库名以 `_test` 结尾，并有创建/删除临时数据库权限），再执行：

```powershell
python -m pytest tests/integration/test_business_prompt_api.py tests/integration/test_asset_image_generation.py tests/integration/test_production_generation.py -q
```

## 生效与容量

提示词资源有进程内缓存。已运行的 API/Worker 需要重启并加载新代码；新建任务使用 v2，历史任务仍保持其冻结输入。没有自动修改现有剧本、资产提示词、分镜或图片。

素材提取仍遵循配置 `EXTRACTION_MAX_OUTPUT_TOKENS`（默认 8192）及请求限制。详细视觉规格会增加输出量；素材很多时应按类别/分集划分请求，或在模型支持范围内调整既有输出额度。超过额度的输出仍按原有截断错误处理，不把不完整结果当成功。

## 分镜参考图上传修复（2026-09-28）

本地 MinIO 的签名图片地址为 `http://127.0.0.1:9000/...` 时，OpenAI 图片编辑流程原先把已入库素材当作外部网址下载，被网络安全检查以 `unsafe_address` 拦截，尚未提交给图片模型。这与提示词内容无关。

现在执行器按冻结的 `reference_media_ids` 查询媒体记录，通过已配置的对象存储 SDK 读取图片，并按原顺序组成 `/images/edits` 的 multipart 文件上传。此读取能力只在服务内部传递，不接受客户端提供的下载回调或存储路径，也不进入任务 JSON。每图 50 MiB、合计 100 MiB，检查真实文件格式和读取预算；任意外部网址仍经过原有地址安全检查。前端错误映射增加参考图缺失、存储不可用、图片过大、格式无效及地址拦截的中文说明。

修复验证：459 项单元/API 测试、34 项隔离 MySQL 集成测试通过，1 项可选基础设施测试跳过；lint 与格式检查通过。另从此次失败任务读取三张真实 MinIO 素材图（约 2.13、1.98、1.71 MB），验证文件格式及 multipart 组装。真实素材验证在本地截获模型请求，没有调用收费模型或改写现有任务。API、数据库、MinIO 健康检查通过，后端服务已重载。

历史失败记录不会自动变成成功；可以重试原任务。要使用此前批准的 v2 提示词，请在分镜制作界面新建生成任务，历史重试仍保持原冻结提示词。

### 参考图上传超时

后续两次任务在提交约 5.8 MB 参考图时约 10 秒失败，报 `transport_error`。无密钥、无效模型和无效文件的同量级网络探针复现了 `ProtocolError(TimeoutError('The write operation timed out'))`；普通只读连接正常。原因是 urllib3 在发送请求体期间也沿用 `connect` 超时，原先统一的 10 秒设置截断了文件上传。

multipart 请求现使用本次动作剩余预算作为连接/上传等待上限，普通 JSON 请求仍保留 10 秒连接上限；不增加重试，不放宽 TLS 或地址检查。嵌套的写入超时映射为 `upload_timeout`，并为它和 `transport_error` 提供中文说明。中断后的提交仍保留 `unknown` 状态，不能据此认定服务商没有受理或没有计费。

修复后同量级无认证探针已收到预期的 HTTP 401 响应，未调用有效模型。此验证证明传输请求得到响应，不代表已验证真实生成结果。
