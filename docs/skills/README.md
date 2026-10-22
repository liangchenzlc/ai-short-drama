# AI 短剧 / 漫剧 Skill 归档

本目录存档从候选清单（`AI漫剧制作可下载Skill清单（30个）.xlsx`，含主清单 30 项、备选 7 项、微表情专项 10 项、画质专项 12 项，共 **59 项**）中筛选出的高质量 skill，按制作环节分类归档，仅作方法论与规则参考。

- 快照时间：2026-10-20
- 候选 59 项 → **入选 9 项**（15.3%）；**补充收录 1 项**（用户追加的 Seedance 2.0 提示词库，不在原 59 候选清单内）
- 归档体积：约 16 MB / 578 个文件（只收文本资产，图片、演示素材与运行时依赖不入库）
- 每项来源、许可、评分与取舍理由见各自目录下的 `UPSTREAM.md`

## 一、分类规则

按「主职责所属制作环节 + 可复用粒度」划分，**每个 skill 只进一个目录**。判定顺序：

1. **单职责优先**：只覆盖一个环节的，直接归入该环节目录。
2. **多环节按主产出物**：同时覆盖若干环节时，看最终交付物落在哪个环节。例：脚本+分镜提示词 → 产出物是逐镜画面提示词，归 `02-storyboard`。
3. **套件归 00**：覆盖 ≥4 个环节、自带总控/任务下发/质检闭环的，整体归 `00-pipeline`，不拆分其子技能。

| 目录 | 纳入条件 | 本目录收录 |
| --- | --- | --- |
| `00-pipeline` 全流程编排 | ≥4 环节 + 总控与质检闭环 | drama-skills、manju-script-generator |
| `01-script` 剧本与内容 | 原著改编、分集剧本、大纲、对白、爆款结构 | ai-short-drama-writer |
| `02-storyboard` 分镜与镜头 | 分镜脚本、镜头语言、关键帧、首尾帧 | manga-script-master |
| `03-asset` 角色与美术资产 | 角色三视图、身份锚点、服装连续性、资产库 | seedance-2.0 |
| `04-prompt-performance` 提示词与表演 | 图/视频提示词方法论、微表情、情绪曲线、去 AI 感 | awesome-seedance-2.5-prompts、awesome-seedance-2-prompts |
| `05-audio` 配音与音频 | 多角色 TTS、音色分配、混音 | multi-voice-dubbing |
| `06-post` 剪辑与后期 | 剪辑自动化、FFmpeg、调色、画质修复 | ffmpeg-skill、jianying-editor |

## 二、准入标准

**四维加权**（1–5 分，项目价值权重最高，因为归档目的是服务本仓库而非做通用榜单）：

| 维度 | 权重 | 判断依据 |
| --- | --- | --- |
| 效率 | 25% | 能否减少手工环节、产出是否可直接被 Agent 消费 |
| 质量 | 25% | 方法论成熟度、文档与参考资产完整度、是否有 eval/测试 |
| 使用人数 | 15% | GitHub star、ClawHub 下载/安装量（本次均为实测值，非清单自述） |
| 项目价值 | 35% | 与现有分层架构的契合度、与已装 `short-drama-director` 套装的互补性 |

计分：`0.25×效率 + 0.25×质量 + 0.15×人数 + 0.35×价值`，**≥ 3.60 且满足全部硬门槛**方可入选。

**五道硬门槛（缺一即淘汰，不看分数）**：

1. **可获取**：能拿到 SKILL.md 源文件。SkillsMP 域名在当前网络不可达，WorkBuddy 技能市场为包管理安装而非文件，均不满足。
2. **许可可用**：MIT / Apache-2.0 / CC BY / MIT-0 可用；CC BY-NC-SA 等非商用条款不得进入商业产品。
3. **无不可替代的闭源强依赖**：需第三方付费 Key（NANOPHOTO、LIBTV、HitPaw、Runware、EAGL 等）且能力可被现有适配层覆盖的，不选。
4. **不与已装套装重复**：本机已安装 `short-drama-director` + 12 个 `sd-*` 引擎，全流程编排类若与其同构，增量价值不足。
5. **采用度托底**：star < 100 且下载 < 300 的，须有独立评测/示例或官方背书，否则不计入。

## 三、入选清单（9 项）

| 分类 | Skill | 来源 | 许可 | 采用度（实测） | 评分 |
| --- | --- | --- | --- | --- | --- |
| 00-pipeline | Drama Skills | GitHub · zenstory-ai | MIT | 3,098 star | **4.85** |
| 00-pipeline | manju-script-generator | ClawHub · chen6896qqwee | 未标注 | 1,205 下载 | 3.85 |
| 01-script | AI短剧/漫剧创作大师 | ClawHub · kingsunzhang2026-oss | MIT-0 | 1,846 下载 / 32 安装 | 3.75 |
| 02-storyboard | 漫剧脚本与AI分镜大师 | GitHub · snailzsh | MIT | 78 star（含 evals） | 3.70 |
| 03-asset | seedance-2.0（角色一致性+表情控制） | GitHub · Emily2040 | MIT | 7,827 star | 4.25 |
| 04-prompt-performance | Awesome Seedance 2.5 Prompts | GitHub · AtlasCloudAI | CC BY 4.0 | 244 star / 1,114 条提示词 | 3.85 |
| 05-audio | multi-voice-dubbing | GitHub · ZJU-REAL/Easel | Apache-2.0 | 3,876 star（主仓） | 4.00 |
| 06-post | ffmpeg-skill | GitHub · kajisho5 | MIT | 2,024 star | **4.85** |
| 06-post | jianying-editor-skill | GitHub · luoluoluo22 | MIT | 4,326 star | 3.80 |

> 上表为对原 59 候选清单的筛选结果（入选 9 项）。下方为**用户追加补充**——原清单未含、但在对话中按相同准入标准新增收录的 skill。

### 补充收录（用户追加，不在原 59 候选内）

| 分类 | Skill | 来源 | 许可 | 采用度（实测） | 评分 |
| --- | --- | --- | --- | --- | --- |
| 04-prompt-performance | Awesome Seedance 2.0 Prompts | GitHub · AtlasCloudAI | CC BY 4.0 | 11 star / 2,706 条提示词（含中文版） | 3.85 |

- 与已收录的 **Seedance 2.5** 提示词库同作者、同许可、同结构，形成 **2.0 / 2.5 双版本覆盖**。
- 性质为**纯提示词数据库**（无 SKILL.md，区别于 2.5 的 Skill 包装版）；中文指南整理自火山引擎官方《Doubao Seedance 2.0 系列提示词指南》。
- 评分沿用四维加权（效率 4 / 质量 4 / 人数 2 / 价值 4 → 3.85）；人数偏低因该仓 2026-10-16 新建，但同源已验证质量，且可直接作为项目提示词模板库的导入语料。
- 详细取舍见 `04-prompt-performance/awesome-seedance-2-prompts/UPSTREAM.md`。

## 四、全量评估：主清单 30 项

四维分值依次为「效率 / 质量 / 人数 / 价值」。

| # | Skill | 归档分类 | 四维 | 结论 | 理由 |
| --- | --- | --- | --- | --- | --- |
| 1 | Drama Skills | 00-pipeline | 5/5/4/5 | **入选** | 11 个子技能按环节切分，自带连续性校验与独立审查，含 tests/evals，工程完成度最高 |
| 2 | ai-manga-drama | — | 3/3/1/1 | 筛除 | 38 star；核心生图/生视频绑定 neta 闭源接口，与现有适配层重复 |
| 3 | ai-mandrama-skills | — | 3/3/1/2 | 筛除 | 27 star；9 阶段 SOP 有价值但需即梦账号，且流程与 Drama Skills 同构 |
| 4 | comic-drama-master | — | 3/4/2/2 | 筛除 | **实证不符**：`bytedance/agentkit-samples` main 分支无此技能，仅存在于未合并分支 `feat/add-comic_drama_gen_use_case`，清单给的安装命令实际会失败 |
| 5 | 漫剧制作全流程（WorkRally） | — | 3/3/2/2 | 筛除 | WorkBuddy 技能市场为客户端安装，非文件形态，无法归档到仓库 |
| 6 | AI 短剧全链路制作流水线 | — | 3/3/2/2 | 筛除 | 同上；且总控定位与已装 `short-drama-director` 重叠 |
| 7 | 鲸剧Skills | — | 3/3/2/2 | 不可获取 | SkillsMP 域名在当前网络不可达（连接失败），无法取得正文 |
| 8 | manga-animation（polecomic） | — | 4/3/3/1 | 筛除 | 需火山方舟 ARK Key，单 Key 全流程与项目多模型适配架构冲突 |
| 9 | Comic Drama Generate | — | 4/3/3/1 | 筛除 | 需 NANOPHOTO Key，且依赖 3 个前置技能，接线成本高于收益 |
| 10 | AI短剧/漫剧创作大师 | 01-script | 4/3/4/4 | **入选** | 剧本层最完整：爆款结构 + 100+ 钩子 + 分镜 JSON 导出；采用度同类最高，MIT-0 |
| 11 | 漫剧脚本与AI分镜大师 | 02-storyboard | 4/4/2/4 | **入选** | 逐镜画面提示词 + 完播率/一致性双约束，自带 evals，MIT |
| 12 | ai-short-drama（onlyshot） | — | 3/3/2/3 | 不可获取 | SkillsMP 不可达；即梦/红果方法论与已选分镜技能重叠 |
| 13 | manju-script-generator | 00-pipeline | 4/4/3/4 | **入选** | 32,672 字符，三板分离 + 七要素镜头语言 + Seedance 2.0 全片段，覆盖面最广 |
| 14 | character-design | — | 3/3/1/4 | 筛除 | 仅 6 star、4.5KB 单文件，无参考资产；观点（视频参考用特写+全身像）可并入 03 的做法 |
| 15 | art-asset-designer | — | 3/3/1/3 | 不可获取 | SkillsMP 不可达 |
| 16 | character-prompt（火宝短剧） | — | 4/4/5/2 | 筛除 | **许可否决**：所在仓库 CC BY-NC-SA（非商用），16,413 star 也不能进商业产品 |
| 17 | seedance-director | — | 4/3/1/4 | 不可获取 | SkillsMP 不可达（分数达线但硬门槛①失败） |
| 18 | AI漫剧分镜 | — | 3/2/2/3 | 筛除 | WorkBuddy 市场包；纯文本轻量产出，能力被 02 已选技能覆盖 |
| 19 | manga-drama-generator | — | 3/3/2/3 | 不可获取 | SkillsMP 不可达 |
| 20 | 漫剧镜头生成 | — | 3/3/2/2 | 筛除 | WorkBuddy 市场包；单镜头动态化属模型能力，非提示词方法论 |
| 21 | manju-animator | — | 4/3/2/3 | 筛除 | 680 下载、无许可声明；运镜/动态规则与 03、04 已选内容重叠 |
| 22 | nano-banana-pro | — | 3/4/4/1 | 筛除 | 强依赖 NANOPHOTO Key，项目已有自建图片适配层 |
| 23 | jimeng-cli-skill | — | 3/2/1/1 | 筛除 | 10 star，CLI 封装即梦账号，属可替换的执行通道 |
| 24 | Awesome Seedance 2.5 Prompts | 04-prompt-performance | 4/4/3/4 | **入选** | 1,114 条实测提示词可批量导入，CC BY 4.0，附结构化的 cinematography/多参考文档 |
| 25 | libtv-skill | — | 3/3/4/1 | 筛除 | 需 LIBTV_ACCESS_KEY，是平台客户端 SDK 而非方法论 |
| 26 | Tencent MPS | — | 3/4/3/3 | 筛除（观察） | 腾讯云官方、覆盖超分/去水印/字幕/声音克隆，但需密钥 + COS 配置，与现有 MinIO 渲染链路耦合成本高；建议作为后续「画质增强」阶段的候选供应商 |
| 27 | multi-voice-dubbing | 05-audio | 4/4/4/4 | **入选** | 按 cast 逐行分配音色与情绪，解决 AI 平读；Apache-2.0 可商用 |
| 28 | edge-tts-chinese-roleplay | — | 3/3/1/3 | 筛除 | 27 star，edge-tts 免费方案实用但规则单薄，易被 05 的音色映射吸收 |
| 29 | jianying-editor-skill | 06-post | 4/4/5/3 | **入选** | 4,326 star，剪映工程结构与字幕/时间线文档对画布剪辑有参照价值 |
| 30 | ffmpeg-skill | 06-post | 5/5/4/5 | **入选** | 44 个脚本 + gotchas，覆盖 9:16 重构/响度/HDR/LUT/导出质检，项目渲染本就基于 FFmpeg |

## 五、全量评估：备选补充 7 项

| # | Skill | 四维 | 结论 | 理由 |
| --- | --- | --- | --- | --- |
| 1 | OpenMontage | 2/4/5/2 | 筛除 | 70,077 star 但 AGPL-3.0 传染性强、整体是独立生产系统，与项目架构不可拼接；仅适合读设计文档，不值得整仓入库 |
| 2 | sora-2-generate | 3/3/4/1 | 筛除 | NANOPHOTO Key + 前置技能链，属执行通道 |
| 3 | video-prompt-generator | 3/3/3/2 | 筛除 | 同上；提示词能力与 04 已选技能重叠 |
| 4 | manju-director | 4/3/2/2 | 筛除 | 702 下载、无许可；总控职责与 Drama Skills、本地套装三方重复 |
| 5 | jimeng-api | 3/2/2/1 | 筛除 | 即梦接口封装，无方法论增量 |
| 6 | audio-theater | 4/3/2/3 | 筛除 | 有声剧混音（侧链闪避/空间化）思路可借鉴，但角色配音主链路与 05 已选重复 |
| 7 | NetShort 竖屏短剧（出海） | 3/3/2/2 | 筛除 | 面向出海投流团队，与本项目当前市场不匹配 |

## 六、全量评估：微表情专项 10 项

| # | Skill | 四维 | 结论 | 理由 |
| --- | --- | --- | --- | --- |
| 1 | facial-expression-prompting | 4/4/1/3 | 筛除 | 五问因果链方法好，但 36 star；表演章节与 03 已选 seedance-2.0 重叠 |
| 2 | seedance-emotion-director | 4/4/1/3 | 筛除 | 同上，情绪曲线/四维同在公式已被 03 覆盖 |
| 3 | biaoqing-yanyi | 4/3/1/3 | 筛除 | 18 star，六层公式可读，无资产支撑 |
| 4 | cosprompt | 4/3/3/3 | 筛除 | 1,276 下载但主攻绘图 Prompt，微表情框架与 04 提示词库重复 |
| 5 | storyboard-character-acting | 4/4/1/3 | 不可获取 | SkillsMP 不可达 |
| 6 | seedance-characters | 4/5/4/4 | **入选 → 03-asset** | 角色契约 + identity lock + 服装连续性，7,827 star，直击跨镜串脸 |
| 7 | seedance-prompts-skill | 3/3/1/3 | 不可获取 | SkillsMP 不可达 |
| 8 | 情绪台词表演导演 | 4/3/2/3 | 筛除 | WorkBuddy 市场包，非文件形态 |
| 9 | 写实人物视频提示词 | 4/4/2/3 | 筛除 | 市场包；FACS/AU 思路已记录在 03 的表演参考中 |
| 10 | 故事对白与剧本台词说人话 | 3/3/2/3 | 筛除 | 市场包；对白打磨可后续以 01 的结构模板承接 |

## 七、全量评估：场景画质优化专项 12 项

| # | Skill | 四维 | 结论 | 理由 |
| --- | --- | --- | --- | --- |
| 1 | storyboard-light-reconstruction | 4/4/1/3 | 不可获取 | SkillsMP 不可达；光影重构方法论可在 02 分镜提示词中局部借鉴 |
| 2 | image-relighting | 3/4/2/2 | 筛除 | 需 EACHLABS Key，外部重打光服务 |
| 3 | image-denoise-enhance（美图） | 3/3/2/3 | 筛除 | 需 meitu-cli + 开放平台凭证；降噪可由 ffmpeg-skill 的 denoise 覆盖 |
| 4 | image-superres-enhance（美图） | 3/3/2/3 | 筛除 | 同上；AI 超分能力更适合在渲染链路内调用 |
| 5 | image-lowlight-enhance（美图） | 3/3/2/3 | 筛除 | 同上 |
| 6 | image-enhance（Easel 本地） | 3/3/4/3 | 筛除 | Pillow/OpenCV 传统增强，与 ffmpeg-skill 的 scale/denoise/unsharp 重叠 |
| 7 | restore-and-upscale（Runware） | 3/4/2/2 | 筛除 | 需 Runware Key，按损伤选模型思路可参考 |
| 8 | HitPaw Image & Video Enhancer | 2/3/1/1 | 筛除 | 需 Key + 点数计费，16 个模型皆为托管服务 |
| 9 | image-edit（ClawHub） | 3/3/3/2 | 筛除 | 4k+ 下载但依赖 IC-Light/ClipDrop 外部服务，非本地能力 |
| 10 | color-correct | 4/4/2/3 | 筛除 | 9 款电影感 Look + .cube LUT 实用，但 ffmpeg-skill 已含 color.py/LUT 工作流，避免两套调色入口 |
| 11 | color-grade-ai | 4/4/2/3 | 筛除 | 需 Ruby/Python 链路生成 LUT，与上条同因 |
| 12 | 图片画质修复（美图开拍） | 2/3/2/2 | 筛除 | WorkBuddy 市场包，一键式黑盒 |

## 八、三个值得说明的「高星落选」

- **OpenMontage（70,077 star）**：星数是全部候选里最高的，但 AGPL-3.0 + 独立生产系统形态，接入即传染，只能当架构读物，不构成可复用资产。
- **character-prompt / 火宝短剧（16,413 star）**：角色提示词规范确实好，但仓库为 CC BY-NC-SA，非商用条款与商业化冲突，只能内部裸读、不能入库再分发。
- **Tencent MPS（官方，591 下载）**：能力与本项目缺口（画质修复、去水印、字幕翻译）高度吻合，卡在密钥 + COS 配置；已列为下一阶段候选，而不是本期归档。

## 九、目录结构与使用

```
docs/skills/
├── README.md                      # 本文：规则、评分、全量结论
├── 00-pipeline/
│   ├── drama-skills/              # 11 个子技能 + UPSTREAM.md
│   └── manju-script-generator/
├── 01-script/ai-short-drama-writer/
├── 02-storyboard/manga-script-master/
├── 03-asset/seedance-2.0/         # 64 份 references
├── 04-prompt-performance/awesome-seedance-2.5-prompts/   # 1,114 条提示词
├── 05-audio/multi-voice-dubbing/
└── 06-post/
    ├── ffmpeg-skill/              # 44 个脚本 + 5 份 references
    └── jianying-editor/
```

约定：

- 每个技能目录下的 `UPSTREAM.md` 是唯一可信的来源记录（分类、地址、许可、采用度、评分、归档范围、未归档部分）。
- 归档内容为**文本资产**：`SKILL.md`、参考文档、规则表、脚本与模板；图片、演示 GIF、测试夹具、运行时依赖不入库，按 `UPSTREAM.md` 的上游地址取用。
- 接入项目时按 `AGENTS.md` 的分层约定改造（提示词进 `ai/business_prompts`，规则进 `service`，不要直接把技能当运行时依赖）。
- 上游许可以 `UPSTREAM.md` 为准；再分发或用于商业功能前需复核，特别是 `manju-script-generator`（未声明许可）。
- 本快照不跟踪上游后续提交，更新请回上游重新获取并同步修订 `UPSTREAM.md`。

## 十、英文技能中文化（2026-10-20 补充）

归档时部分技能原文为英文，已按需翻译为中文（结合 AI 短剧制作场景与术语语义，而非字面直译）。

**翻译原则**

- 保留可执行的标识与代码：frontmatter 的 `name` 字段、模型 ID、命令行参数（`--dry-run`、`--codec` 等）、脚本名（`cut.py`、`multivoice.py`）、JSON 键名、文件路径与相对链接一律原样保留。
- 保留产品/品牌名与行业术语：Seedance 2.0、即梦（Dreamina）、剪映（JianYing）、FFmpeg、LUT、HDR/SDR、LUFS、T2V/I2V/R2V 等不翻译；术语在中文首次出现时补一句释义（如 "R2V 参考生视频"、"首帧/尾帧"）。
- 仅本地化说明性文字与章节标题；表格中的"命令列"保持英文（本就是命令），"说法列"译为口语化中文。

**已翻译（原为中文缺失 / 英文为主）**

| 技能 | 原语言 | 处理方式 |
| --- | --- | --- |
| `03-asset/seedance-2.0` | 英文 | 全文译为中文（根技能 + 运行循环/关卡/权威层级/加载地图） |
| `04-prompt-performance/.../seedance-2-5-skill` | 英文 | 全文译为中文（创作路线/连续性/Atlas 执行层/计费状态机） |
| `04-prompt-performance/.../universal-video-prompt-skill` | 英文 | 全文译为中文（与模型无关的规范 + 编译/降级/转场/审查） |
| `06-post/ffmpeg-skill` | 英文 | 全文译为中文（工作流/请求→脚本映射表/报告格式/避坑） |
| `06-post/jianying-editor` | 中英混合 | 本地化英文标题、intro 与规则注释；代码/CLI/路径原样保留 |

**本就是中文、无需翻译**

- `01-script/ai-short-drama-writer`：主体为中文，英文仅出现在 JSON 字段名与品牌名（ViFlow、MIT-0）。
- `05-audio/multi-voice-dubbing`：主体为中文，英文仅出现在代码、`.env` 变量与 provider 名（edge-tts、CosyVoice2）。
- `00-pipeline` 的 drama-skills 套件（11 个子技能）与 manju-script-generator：原始即中文。
- 补充收录的 `04-prompt-performance/awesome-seedance-2-prompts`：中文指南 `PROMPTING_GUIDE.zh.md` 与中文数据 `zh.json` 已就位；英文 `prompts.json` 为原始数据（非技能说明文档），无需翻译。

**校验**：20 个 `SKILL.md` 的 YAML frontmatter 全部合法；翻译后 4 个纯英文技能的"纯正文中文占比"由 0% 提升到 35–56%，其余英文均为命令/路径/代码（按上述原则保留）。
