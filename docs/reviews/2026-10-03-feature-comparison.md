# 功能盘点、同类项目对比与后续建议

核查日期：2026-10-03。本文记录当前项目与 5 个 GitHub 同类项目的功能对比，供后续需求讨论和开发排期参考。

## 结论与核查口径

当前项目从代码层面已形成“小说改编 → 剧本确认 → 素材准备 → 分镜图片/视频 → 剪辑与成片导出”的主要流程，同时具备账号协作、Agent、批量生成、配音、字幕、配乐和角色音色设计。下一阶段建议优先补齐现有操作入口，再加强角色与镜头连续性、生成前检查及工程交付。

本次核查遵循以下口径：

- 当前项目以实际挂载的后端接口、可达前端组件和请求链路为依据；测试文件仅作为实现意图的辅助证据。
- “已实现”表示存在代码和相应调用链，不代表当前部署已开放、所有模型均兼容或真实供应商验收通过。
- 只有后端接口、前端封装或展示按钮的能力，单独标明接入缺口；未找到可靠证据的能力不直接判定为绝对不存在。
- 同类项目依据公开 README、功能说明和关键源码；排除明确的 roadmap 和未接通的占位操作。
- 优先级是产品建议，尚未形成具体实施方案、工期估算或已批准的开发范围。

批量、声音与原生对白视频已有实现，但受功能开关控制。[配置代码](../../backend/src/short_drama/core/config.py)中 `generation_batches_enabled`、`audio_production_enabled`、`native_video_enabled` 默认关闭。当前部署的开关、数据库迁移和 Worker 状态未在本次核查中验证。

部分既有说明存在滞后：根目录 README 和 `frontend/PRODUCT.md` 的旧列表仍将批量生成、配音、字幕或配乐列为未实现；`docs/architecture.md` 中也有旧的账号、声音能力描述。本文按实际源码判定，不能依据这些旧列表重复立项。

## 当前项目已有功能

| 功能 | 已实现范围 | 当前边界与主要依据 |
| --- | --- | --- |
| 项目与分集 | 创建、编辑、搜索、分页、画幅与风格设置、项目归档 | 归档保留成果；见 [项目接口](../../backend/src/short_drama/api/v1/projects.py)与[项目页面](../../frontend/src/features/projects/ProjectOverview.tsx) |
| 小说与剧本 | TXT 导入、分别自动保存、AI 改编候选、切换编辑稿、明确确认、版本冲突保护 | 小说修改不会自动重写剧本；见 [写作接口](../../backend/src/short_drama/api/v1/episode_writing.py)与[改编组件](../../frontend/src/features/projects/NovelScriptGeneration.tsx) |
| 素材提取与三层库 | 提取角色/场景/道具，逐项审核、新建或复用；个人、项目、分集素材管理 | 同项目共享修改需确认；跨范围复制不带入原生成历史和音色；见 [素材库组件](../../frontend/src/features/assets/AssetLibraryPanel.tsx)与[提取组件](../../frontend/src/features/projects/ScriptAssetExtraction.tsx) |
| 素材与分镜生图 | 持久参考图、图片上传、候选预览与采用、来源过期提示 | 素材和分镜各最多保存 16 张输入参考图，实际使用受模型能力限制；见 [参考图组件](../../frontend/src/features/generations/ReferenceImages.tsx) |
| 分镜制作 | AI 拆镜候选、追加或替换、新增、内容编辑、时长、素材关联；单图/四/五/九宫格设置 | 后端归档、移动、排序已有，分镜页面缺少入口；见 [分镜页面](../../frontend/src/pages/projects/episode/StoryboardStage.tsx)与[分镜接口](../../backend/src/short_drama/api/v1/episode_storyboard.py) |
| 分镜视频 | 使用已采用分镜图作参考，提示词、时长、清晰度保存，候选播放和人工采用 | 分镜流程主要使用一张已采用分镜图；通用视频任务已有首尾帧选择；见 [视频候选组件](../../frontend/src/features/projects/ShotVideoCandidates.tsx)与[通用生成组件](../../frontend/src/features/generations/CreateGeneration.tsx) |
| 批量生成 | 素材图、分镜图、分镜视频批次；预检、最多 100 项、受控并发、暂停/恢复/取消、失败项重试 | 受 capability 开关控制，不自动采用或自动串联生图生视频；见 [批次组件](../../frontend/src/features/generations/BatchGeneration.tsx) |
| Agent 创作 | 私有持续对话、讨论与执行、多步计划批准、指定媒体任务、停止、共享候选、采用并继续 | 首版为单协调 Agent 与内置版本化 Skills；时间线、音频和导出仍走现有工作台；见 [Agent 运行组件](../../frontend/src/features/agents/AgentConversationRuntime.tsx) |
| 账号与协作 | 邮箱验证、登录、密码重置、定向邀请、成员移除/退出、项目隔离、个人模型配置 | 当前主要为主人和协作者，不含独立团队空间、只读访客、实时编辑、评论和任务分配；见 [协作组件](../../frontend/src/features/projects/ProjectCollaboration.tsx) |
| 模型与任务中心 | 文本/图片/视频/音频配置、默认模型、目录探测、密钥服务端加密；任务取消、安全恢复和重试 | 参数与协议兼容性依供应商而异；用量显示不等同于金额成本管理；见 [任务详情](../../frontend/src/features/generations/TaskDetail.tsx) |
| 媒体资产库 | 图片/视频浏览、详情、来源、重命名和采用 | 后端接受音频类型，但统一资产库前端只开放图片/视频；见 [媒体页面](../../frontend/src/pages/media-library/MediaLibraryPage.tsx) |
| 剪辑与成片 | 单视频轨追加、排序、分割、裁剪、删除、静音、撤销重做、同步来源、合成预览、MP4 导出 | 输出 720p/1080p；缺少多视频轨、转场和变速；见 [剪辑工作台](../../frontend/src/features/projects/AssemblyWorkbench.tsx)与[成片 API](../api/episode-assembly.md) |
| 配音、字幕与配乐 | 台词提取与校对、TTS 候选试听及采用、SRT 导入导出与烧录、配乐裁剪/循环/淡入淡出/对白压低 | 配乐限一条，字幕时间主要人工核对，无字级强制对齐；见 [声音面板](../../frontend/src/features/projects/SoundPanel.tsx)与[制作说明](../production-features.md) |
| 原生角色对白视频 | 角色音色设计、试听、采用、对白校对，参考样音驱动视频；画内说话与画外音 | 最多两位角色轮流讲话；音色设计不同于样音克隆；跨镜头音色稳定性尚需真实验收；见 [角色声音组件](../../frontend/src/features/projects/NativeVoicePanel.tsx)与[模型适配器](../../backend/src/short_drama/ai/adapters.py) |

分镜排序与成片片段排序是不同能力：后者已有可达交互，前者仍缺少页面操作。配音、音频试听与统一音频资产库也应分别统计。

## 5 个同类项目对比

| 同类项目 | 已核实的特色功能 | 当前项目覆盖情况 | 值得借鉴的功能 |
| --- | --- | --- | --- |
| [火宝短剧 Huobao](https://github.com/chatfire-AI/huobao-drama/blob/master/README.md) | 小说改编、素材提取、批量生成、视频拼接；可编辑 Agent Skills；多语言界面；桌面安装包 | 主要制作流程、批量任务、Agent、成片导出均已有 | 提示词与创作模板管理、首次配置引导；面向个人用户时可考虑桌面版 |
| [Jellyfish](https://github.com/Forget-C/Jellyfish/blob/main/README.md) | 角色/场景/道具/服装管理；镜头准备状态；剧本角色混淆检查；提示词模板 | 素材复用、候选审核、任务中心已有；服装模型和专项检查可深化 | 服装与造型管理、生成前镜头准备检查、剧本一致性检查 |
| [CineGen](https://github.com/UllrAI/CineGen-ShortDrama/blob/main/README.md) | 角色不同造型版本；每镜头起始帧/结束帧；结合关键帧生成视频 | 通用任务已有首尾帧；分镜页面目前主要使用一张已采用分镜图 | 将首尾帧接入分镜流程，按镜头选择角色造型 |
| [LocalMiniDrama](https://github.com/xuanyustudio/LocalMiniDrama/blob/main/README.md) | 角色四视图、视觉锚点；宫格拆图；上一镜尾帧衔接下一镜；完整工程 ZIP；列表与画布双视图 | 参考图、宫格生图、批量制作已有；缺少专门的多视图、切格和工程包入口 | 角色多视图、镜头衔接、宫格拆图、完整工程导入导出 |
| [虾客漫 Xiakeman](https://github.com/XiakeMan777/xiakeman-ai-short-drama/blob/main/docs/FEATURES.md) | 节点画布、3D 机位预演；故事板规则检查；声音设计/克隆、音效与转写；裁剪/分割/变速 | 固定流程、音色设计、粗剪、字幕配乐已有；画布、规则质检和声音工具可深化 | 导演规划检查、ASR 转写、声音克隆、变速；画布和 3D 可后置 |

对比时需保留以下边界：

- [CineGen 导出页面](https://github.com/UllrAI/CineGen-ShortDrama/blob/main/components/StageExport.tsx)的 MP4、EDL/XML 按钮没有处理逻辑，不能将其展示界面计作已完成的导出能力。
- [Jellyfish 视频编辑页](https://github.com/Forget-C/Jellyfish/blob/main/front/src/pages/aiStudio/editor/VideoEditor.tsx)的剪切、效果、预览和导出按钮没有接通对应操作，不能据此认定具备专业多轨剪辑。
- [Jellyfish 一致性检查](https://github.com/Forget-C/Jellyfish/blob/main/backend/app/chains/agents/consistency_checker_agent.py)主要检查剧本中的角色身份、指代及行为主体混淆，不等同于跨集剧情或画面一致性验收。
- [虾客漫故事板质检](https://github.com/XiakeMan777/xiakeman-ai-short-drama/blob/main/src/components/step4/storyboardBoardQuality.ts)检查结构化规划、文本和规则；其自动修复开关当前返回关闭状态，不能记作已开放的自动返工。
- LocalMiniDrama 的部分参考图、全景和视频能力仍有 roadmap 标记，未统一计为已实现。“本地运行”也不等于图片、视频完全离线生成。
- 未确认这些项目普遍具备按金额计价的费用预算。参考图数量限制、Token 或调用次数限制不能替代财务成本统计。

## 功能缺口与建议优先级

P0 表示优先补齐现有能力；P1 表示下一阶段重点；P2 表示按实际制作需求推进。下表是需求记录，不代表功能已开始实施。

| 编号 | 优先级 | 建议功能 | 当前情况 | 建议补充范围 | 参考与依据 |
| --- | --- | --- | --- | --- | --- |
| F01 | P0 | 分镜归档与排序入口 | 后端接口和前端封装已有，页面没有入口 | 归档、上移/下移或拖动排序，保留版本校验和冲突反馈 | [接口接入审计](../api/frontend-coverage-audit.md) |
| F02 | P0 | 项目活动记录 | 后端有活动接口，协作页面未展示 | 展示谁修改、生成或采用了什么，支持定位相关对象 | [接口接入审计](../api/frontend-coverage-audit.md) |
| F03 | P0 | 音频资产库入口 | 音频候选和试听已有，统一库只展示图片/视频 | 音频列表、试听、搜索和来源定位；跨范围复用继续遵守权限 | [接口接入审计](../api/frontend-coverage-audit.md) |
| F04 | P1 | 角色多视图与造型版本 | 已有角色确认图和参考图复用 | 正/侧/背视图；服装、年龄阶段等造型；镜头明确绑定版本，变更时提示依赖影响 | LocalMiniDrama、CineGen、Jellyfish |
| F05 | P1 | 分镜首尾帧与镜间衔接 | 通用任务支持首尾帧，分镜流程未接入 | 每镜头选择首尾帧；提取上一镜尾帧供下一镜使用；支持多参考图，保留模型能力约束 | CineGen、LocalMiniDrama |
| F06 | P1 | 剧本与故事板专项检查 | 已有版本、来源、参数校验 | 检查角色指代、服装变化、对白时长、空间站位与相邻镜头承接，提供可定位的问题及人工处理入口 | Jellyfish、虾客漫 |
| F07 | P1 | 完整工程打包与迁移 | 已有 MP4、SRT、局部草稿下载和资源复制 | ZIP 包含剧本、分镜、素材、媒体和剪辑数据；导入检查完整性、处理 ID 与权限；附分镜表，不带出密钥或临时签名 URL | LocalMiniDrama |
| F08 | P1 | 可管理的创作模板 | 已有内置提示词和版本化 Skills | 改编、素材、分镜、视觉风格模板的保存、版本与复用入口；模板编辑保留既有执行授权和候选采用规则 | 火宝、Jellyfish |
| F09 | P1 | 自动转写与字幕对齐 | 字幕编辑、SRT 导入导出已有，时间主要人工核对 | ASR 转写、句级/字级对齐、剪辑改变后的字幕复核提示，保留手工修正 | 虾客漫 |
| F10 | P1 | 金额成本管理 | 部分 Token/调用用量显示及执行数量限制已有 | 提交前估算费用、项目费用汇总、金额限额提醒；区分估算、供应商账单和未知受理状态 | 产品经营建议，非本次确认的竞品普遍能力 |
| F11 | P2 | 宫格拆图与逐格采用 | 已有宫格生图及整图参考 | 自动切格、人工调整边界、逐格绑定镜头或生成视频；切分结果保留来源和明确采用 | LocalMiniDrama |
| F12 | P2 | 剪辑与声音增强 | 单视频轨粗剪、单条配乐已有 | 先补变速、转场、多段配乐和音效轨，再按需求扩展多视频轨；声音时间变化须提供复核机制 | 虾客漫 |
| F13 | P2 | 声音克隆与音色验收 | 已有音色设计、试听、角色绑定；原生对白最多两人 | 样音克隆、多人对白；真实验证跨镜头音色稳定性，分别记录工程结果和人工听感 | 虾客漫 |
| F14 | P2 | 画布与导演预演 | 已有明确的分阶段工作流 | 先做与现有业务数据同源的画布视图；自由节点和 3D 机位预演后置 | LocalMiniDrama、虾客漫 |

协作深化属于另一个可选方向：只读访客、审片评论、任务分配和所有者转让可以在团队需求明确后单独排期。当前账号协作已有实现，不能整体标为缺失；独立团队空间和实时编辑暂不建议优先于制作连续性与交付能力。

## 建议开发顺序

1. **补齐现有入口（F01–F03）**：优先复用已有接口，完成分镜整理、项目活动记录和音频资产库。
2. **减少画面返工（F04–F06）**：按角色造型版本、首尾帧衔接、生成前专项检查的顺序推进；新增检查须区分规则结论与模型建议。
3. **完善工程交付（F07）**：先支持完整工程备份与迁移，再评估剪映、EDL 或 FCPXML 等外部工程互通需求。
4. **提高持续制作效率（F08–F10）**：结合使用频率推进模板、字幕自动对齐与金额成本管理。
5. **按制作场景扩展（F11–F14）**：宫格拆图、剪辑声音增强、声音克隆、画布和 3D 预演分别评估投入及收益。

每项进入开发前，应重新核对当时的代码和供应商文档，明确接口、数据迁移、页面入口及验证范围。未来生成结果仍须先成为候选，由用户明确采用；首尾帧衔接、规则检查或画布操作不能绕过现有保存、版本、权限与幂等约束。

## 来源索引

### 当前项目

- [后端接口与前端接入审计](../api/frontend-coverage-audit.md)：分镜归档/排序、项目活动记录及音频资产库缺口。
- [播放恢复、批量生成与声音制作](../production-features.md)：批次、配音、字幕、配乐、混音和部署边界；其中历史环境状态不作为当前部署结论。
- [成片合成与导出 API](../api/episode-assembly.md)：草稿、预览、导出、恢复与快照行为。
- [Agent 验收记录](../agent-verification.md)：既有供应商验收的范围；本次未复验，也未将记录扩展为所有模型均可用。

### GitHub 同类项目

| 项目 | 功能说明与关键源码 |
| --- | --- |
| 火宝短剧 | [README](https://github.com/chatfire-AI/huobao-drama/blob/master/README.md)、[Skills 管理接口](https://github.com/chatfire-AI/huobao-drama/blob/master/backend/src/routes/skills.ts) |
| Jellyfish | [README](https://github.com/Forget-C/Jellyfish/blob/main/README.md)、[服装编辑页面](https://github.com/Forget-C/Jellyfish/blob/main/front/src/pages/aiStudio/assets/CostumeAssetEditPage.tsx)、[剧本角色一致性检查](https://github.com/Forget-C/Jellyfish/blob/main/backend/app/chains/agents/consistency_checker_agent.py) |
| CineGen | [README](https://github.com/UllrAI/CineGen-ShortDrama/blob/main/README.md)、[造型与首尾帧数据模型](https://github.com/UllrAI/CineGen-ShortDrama/blob/main/types.ts)、[导出页实现边界](https://github.com/UllrAI/CineGen-ShortDrama/blob/main/components/StageExport.tsx) |
| LocalMiniDrama | [README](https://github.com/xuanyustudio/LocalMiniDrama/blob/main/README.md)、[更新记录](https://github.com/xuanyustudio/LocalMiniDrama/blob/main/CHANGELOG.md)、[角色四视图](https://github.com/xuanyustudio/LocalMiniDrama/blob/main/backend-node/src/services/characterLibraryService.js)、[视觉锚点](https://github.com/xuanyustudio/LocalMiniDrama/blob/main/backend-node/src/services/characterGenerationService.js)、[尾帧衔接](https://github.com/xuanyustudio/LocalMiniDrama/blob/main/backend-node/src/services/tailFrameLinkService.js)、[分镜表导出](https://github.com/xuanyustudio/LocalMiniDrama/blob/main/frontweb/src/utils/exportStoryboardSheet.js) |
| 虾客漫 | [功能说明](https://github.com/XiakeMan777/xiakeman-ai-short-drama/blob/main/docs/FEATURES.md)、[已知限制](https://github.com/XiakeMan777/xiakeman-ai-short-drama/blob/main/docs/KNOWN_LIMITATIONS.md)、[故事板规则质检](https://github.com/XiakeMan777/xiakeman-ai-short-drama/blob/main/src/components/step4/storyboardBoardQuality.ts)、[声音设计/克隆](https://github.com/XiakeMan777/xiakeman-ai-short-drama/blob/main/src/lib/mimoTtsClient.ts)、[裁剪/分割/变速](https://github.com/XiakeMan777/xiakeman-ai-short-drama/blob/main/src/features/canvas/nodes/VideoCompositionNode.tsx)、[3D 导演](https://github.com/XiakeMan777/xiakeman-ai-short-drama/blob/main/src/features/canvas/nodes/Director3DNode.tsx) |

GitHub 链接指向核查时的默认分支，后续可能变化。本文是日期化的功能对比记录，实施前需重新核查对应版本。

## 本次验证与未验证范围

本次功能分析只读核查了项目源码、接口、关键测试文件及同类项目公开文档/源码，未修改业务代码，未读取环境密钥，未调用付费模型。

文档落盘时核对相对链接目标、表格列数与 Git 差异。纯文档变更未运行前后端测试或构建；未启动当前项目和竞品，未核验当前业务数据库、部署开关、Worker 或真实供应商效果。因此本文不能作为上线验收、供应商兼容性验收或竞品实机体验报告。
