# 依据：每条规则从哪来

本技能不是凭经验写的。下面逐条列出规则与出处，**上游原文用引号摘录，未改动语义**。
凡是本项目自行推导、上游没有直接表述的，单独列在第四节，不与有出处的规则混在一起。

## 一、身份、画框与光向（三条锁）

| 编号 | 规则 | 出处 | 原文要点 | 许可 |
| --- | --- | --- | --- | --- |
| R1 | 把输入当作同一张照片，而不是"替换人像"的参考 | xxg-portrait-rebuild-light · `SKILL.md` | "Treat the input as the same photograph, never as a reference for a replacement portrait." | MIT |
| R2 | 身份六组必须锁定 | 同上 | 六组为 face outline/proportions、feature spacing/shape/size、hairline/parting/hair mass、source-identifying skin anchors、makeup/accessories、apparent age/expression | MIT |
| R3 | 画框必须锁定 | 同上 · `Preserve the Frame` | "Retain orientation, aspect ratio, composition, focal plane, depth of field, and subject-to-frame scale." | MIT |
| R4 | 宽高比漂移容忍 ≤5% | 同上 | "Accept relative aspect-ratio drift of `≤5%`." | MIT |
| R5 | 只提质感时必须锁光 | 同上 · `texture-only` 作用域 | 强制 `L0 + E0 + T0 + G0 + D0 + A0 + Q0`，"Preserve the source lighting, highlight placement, exposure, white balance, focal plane, depth of field, and background." | MIT |
| R6 | 质感修复不得变成重打光 | 同上 | "Never let texture repair become relighting, tone correction become a new light source…" | MIT |
| R7 | 不要在提示词里重述身份 | 同上 | "State identity once. Treat the source itself as the identity card; do not invent a new age, personality, beauty description, lens, or aperture." | MIT |

## 二、皮肤与材质

| 编号 | 规则 | 出处 | 原文要点 | 许可 |
| --- | --- | --- | --- | --- |
| R8 | 真实感由三条可分离信号构成 | 同上 · `Apply Optical Skin Realism` | ①clean low-frequency skin tone ②source-driven diffuse/specular，高光限于朝光面 ③fine, region-specific microdetail | MIT |
| R9 | 细节按区域分配 | 同上 | "cheek pores softer, nose pores slightly clearer, lip texture separate, eye-area structure undisturbed. Do not tile one pore pattern across the face." | MIT |
| R10 | 不要全脸等同锐化 | 同上 | "Never sharpen the whole face, every hair, clothing, and background equally." | MIT |
| R11 | **不造脏** | 同上 | "Do not manufacture realism with dirt, darkness, coarse pores, uniform grain, random color patches, or exaggerated facial lines." | MIT |
| R12 | 结果先要像一张干净照片 | 同上 | "The result must first read as a clean photograph at normal size." | MIT |
| R13 | 原有标记保留但不点名不放大 | 同上 | "Preserve source-existing marks as identity anchors, but do not list or generate new imperfections by default." | MIT |
| R14 | 不得用"更白/更美"类语言 | 同上 | "do not use `fair`, `whiter`, or beauty-grade language unless the user explicitly requests a complexion change." | MIT |
| R15 | 材质要靠物理属性而非强度词 | im2-clean-image · `SKILL.md` | "Do not rely on generic `8K`, `high resolution`, `ultra realistic`, or `sharp details` as substitutes for material physics."；材质层要写 hero surfaces / surface response / material separation | 未核实 |
| R16 | 宁可保留轻微模糊，也不凭空造细节 | 同上 · `description` | "prefer residual blur over invented restoration detail." | 未核实 |

## 三、提示词工程

| 编号 | 规则 | 出处 | 原文要点 | 许可 |
| --- | --- | --- | --- | --- |
| R17 | 篇幅 55–110 词，上限 135 | xxg-portrait-rebuild-light | "Target `55–110` English words, with an absolute ceiling of `135` for combined edits." | MIT |
| R18 | 重试时替换失败的那一行，不追加 | 同上 | "On retry, replace the failed line instead of appending more instructions." | MIT |
| R19 | 几乎没变时，只加强一个可观察目标 | 同上 · `Validate` | "If a result is nearly unchanged, strengthen one observable target." | MIT |
| R20 | 皮肤变假时的替换句式 | 同上 | 改为 "clean continuous source skin tone; bounded source-shaped highlights; faint region-specific microdetail only where focus and light resolve it" | MIT |
| R21 | 自然语言模型写完整句子 | `photorealistic-skill/references/models.md` | "Natural-language models … respond best to full descriptive sentences. Write the prompt as a paragraph, not a tag list." | MIT |
| R22 | 编辑必须传原图 | 同上 | "When the request involves a specific existing person or an image to recreate, always pass that image as a reference" | MIT |
| R23 | 负面词少而短，长列表会被忽略或反转 | 同上 · `SKILL.md` | "a few, at the end, phrased as short 'not X' items. Long negative lists get ignored or inverted on many models." | MIT |
| R24 | 负面词槽不要变成"旧失败回忆" | im2-clean-image | "never let the negative slot become a memory of old failures or a list of concepts that accidentally summon themselves." | 未核实 |
| R25 | 风格词黑名单 | photorealistic-skill | 列 `cinematic, dramatic, epic, stunning, gorgeous, perfect, flawless, masterpiece, 8k, ultra-detailed, hyperrealistic, award-winning, studio, rim light, bokeh, HDR, octane, unreal engine` 为 trigger words | MIT |
| R26 | 避免 perfect/flawless/8K/CGI/doll-like | junhao-ye/sns-realistic-portrait-prompt · `SKILL.md` | "避免 `perfect face`、`flawless skin`、`8K ultra detailed masterpiece`、`CGI`、`doll-like` 等容易製造蠟像感或無法驗證的堆詞" | 未核实 |
| R27 | 先写可见事实，再写摄影语言 | 同上 | "先寫可見事實，再寫攝影語言。使用具體細節取代空泛品質詞" | 未核实 |

## 四、操作策略

| 编号 | 规则 | 出处 | 原文要点 | 许可 |
| --- | --- | --- | --- | --- |
| R28 | 优先最小范围的局部修复 | beatra-ai/ai-image-realism-skill · `SKILL.md` | "Prefer the smallest local repair that can address the issue" | 未核实 |
| R29 | 不得把编辑请求偷偷换成重新生成 | 同上 | "never silently replace an edit request with a newly generated image" | 未核实 |
| R30 | 一轮只修一个问题簇 | 同上 | "fix one highest-impact problem cluster per pass" | 未核实 |
| R31 | 结构缺陷走 inpaint，渲染缺陷走降渲染 | `photorealistic-skill/references/defects.md` | Fix 列：手/牙/眼/耳走 inpaint；Sharpness/Contrast/Color/Noise 走 `degrade.py` | MIT |
| R32 | 多轮编辑要用最初原图做基准 | xxg-portrait-rebuild-light · `multi-round-repair` | "use the latest accepted edit as the target and the first root base as the stable identity/geometry/color reference"；"do not attach the full history" | MIT |
| R33 | 先锁身份再加真实感 | nanbada/muse-creative-skills · `image/photoreal-realness/SKILL.md` | "Character lock first: fix the identity anchor … before realism — this skill never changes identity, only believability." | MIT |
| R34 | 真实感靠"崩す"而非"盛る" | 同上 | 方法注明出处：@gaget12090105 "崩す (break down), not 盛る (dress up)"，标注为社区经验 | MIT |
| R35 | 强度分级，可见但不夸张 | qzfcoder/photo-retouch-pro · `SKILL.md` | 四级：`natural-clean` / `visible-polish` / `commercial-impact` / `impact-plus`；"Do not achieve visibility through indiscriminate saturation, sharpening, skin whitening, or heavy blur." | 未声明许可 |
| R36 | 编辑前锁保护区域 | 同上 | "Lock protected regions before editing." | 未声明许可 |
| R37 | 模型差异：Nano Banana 擅长 "keep everything, change only X" | photorealistic-skill · `models.md` | "Very good at following edit instructions on a reference ('keep everything, change only X')." | MIT |
| R38 | Flux 多数无负面词通道 | 同上 | "No negative prompt support in most setups; phrase everything positively" | MIT |
| R39 | SD 负面词字段可堆 | 同上 | 列 `plastic skin, airbrushed, extra fingers, deformed hands, oversaturated, HDR, cartoon, 3d render` | MIT |

## 五、本项目推导（无单一直接出处）

以下不是抄来的，是基于上面多条依据做的工程判断，使用时按经验值对待：

| 编号 | 内容 | 推导基础 |
| --- | --- | --- |
| P1 | 把编辑范围分成 L1 质感 / L2 影调 / L3 光效 / L4 画框 | R5 的 scope 机制 + R6 的边界约束，重新按中文项目习惯命名 |
| P2 | 「越界四项」检查顺序（身份→画框→背景→光向） | R2/R3/R5 三组锁，按"最不可挽回"排序 |
| P3 | 中文四行提示词模板（EDIT/SKIN/MATERIAL/AVOID） | R8 的三条信号 + xxg 的 EDIT/LIGHT/SKIN/QUALITY/AVOID 编译格式，改为中文语序 |
| P4 | 诊断五分类（A 皮肤 / B 光空间 / C 材质 / D 渲染残留 / E 结构） | R31 的缺陷三分组（People/Scene/Finish），拆出"材质"与"光空间" |
| P5 | 轻/中/重三档与本项目交付场景的对应 | R35 的四级强度，收敛为三档 |
| P6 | 后处理仅作可选补强、且不得改画框 | R11（不造脏）+ R3（锁画框）共同约束 |

## 六、明确未采用的上游做法

| 上游做法 | 为什么不采用 |
| --- | --- |
| photorealistic-skill "Always generate 2-4 variants" | 那是**生成**场景的建议；本技能只产出提示词，出图数量由用户决定 |
| beatra 的 `count=1` 默认值 | 同上，且它绑定付费调用，本技能不引入 |
| muse 的 20 个 realness blocks 全量注入 | 上游自己写明"Never paste all 20 — more blocks ≠ more real" |
| 用 Pillow/OpenCV/FFmpeg 产出最终图（xxg 明令禁止） | 本技能保留"可选后处理"但限定为保守补强，且禁止裁切缩放改构图；与 xxg 的严格禁止相比属于放宽，已在 `post-process.md` 标注风险 |

## 七、许可与合规

- MIT 来源（xxg-portrait-rebuild-light、photorealistic-skill、muse-creative-skills）：可商用，本技能为语义重写 + 中文重组，未复制代码资产。
- 未核实/未声明许可（im2-clean-image、sns-realistic-portrait-prompt、photo-retouch-pro、ai-image-realism）：**只吸收观点，不复制文本、不再分发其资产**；若后续要引入其代码或提示词原文，必须先复核许可。
- 摘录的英文原句仅用于本仓库内部溯源，随本文件一并保留出处与许可状态。
