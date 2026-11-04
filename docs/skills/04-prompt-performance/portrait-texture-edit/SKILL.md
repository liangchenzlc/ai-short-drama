---
name: portrait-texture-edit
description: |
  针对**已经存在的一张人像图片**，生成「只提升人物质感、去 AI 味」的编辑提示词与操作方案。
  硬约束：不换人、不改景别、不改构图、不改机位、不改背景、不改姿态与表情、不改光向。
  只处理人脸皮肤、发丝、服装材质、画面渲染残留（过锐/过干净）。
  触发场景：用户上传或指向一张人像图，说"提升质感"、"去 AI 味"、"AI 感太重"、"塑料感"、
  "磨皮过头"、"皮肤太假"、"衣服像贴图"、"像真人一点"、"修一下但别改人"、"保留原图"。
  不适用：没有原图的从零生成、换背景、换装、改构图、改景别、改姿势、换脸、分镜脚本。
---

# 人物质感编辑 · 去 AI 味（单图）

**这个技能只做一件事：拿一张已有的人像，让它看起来更像被真实拍下来的，同时不改变画面里的任何东西。**

不做生成，不做重绘新图，不做二次创作。提示词是交付物，出图由用户和模型完成。

## 三条不可越界（违反任一条即视为失败）

| 锁 | 具体内容 | 依据 |
| --- | --- | --- |
| **身份锁** | 脸型轮廓与比例、五官间距与形状、发际线与发量、皮肤上的固有标记（痣/疤）、妆容与配饰、看上去的年龄与表情 | xxg-portrait-rebuild-light「Preserve the Identity Signature」六组 |
| **画框锁** | 朝向、画幅比例、构图、裁切、对焦平面、景深、人物在画面中的占比 | 同上「Preserve the Frame」，宽高比漂移容忍 ≤5% |
| **光学锁** | 光向、光位、明暗关系、白平衡、镜头透视，除非用户明确要求改光 | 同上 `texture-only` 作用域（L0+E0+T0+G0+D0+A0+Q0） |

**最重要的一条：不要在提示词里重新描述人物和场景。** 描述即重建——你写"三十岁东亚女性、鹅蛋脸、深藏青西装"，模型就会按这些字重新画一个人，而不是修这张图。原图本身就是身份卡。

> 依据：xxg-portrait-rebuild-light — "Treat the input as the same photograph, never as a reference for a replacement portrait." / "State identity once. Treat the source itself as the identity card; do not invent a new age, personality, beauty description, lens, or aperture."

## 第 0 步：确认输入

- 有原图、且能看清脸 → 继续
- 没有原图 / 只有文字 → **停**，这是生成任务，不属于本技能
- 有原图但用户要换背景、换装、改构图、改景别 → **停**，明确告知这超出了「只提质感」的范围，会破坏画框锁

## 第 1 步：定编辑范围（默认只做第一级）

| 级别 | 改什么 | 什么时候用 |
| --- | --- | --- |
| **L1 质感**（默认） | 皮肤响应、发丝、服装材质、局部渲染残留 | 用户说"提质感/去 AI 味/塑料感" |
| **L2 影调** | 曝光、白平衡、层次 | 用户明确说"偏暗/偏黄/发灰" |
| **L3 光效** | 光的方向或质感 | 用户明确说"光不对/太平/想换成侧光" |
| **L4 画框** | 构图、景别、机位 | **本技能不支持**，请改用生成类方案 |

跨级即越界。用户只说"去 AI 味"时，只做 L1，**不要顺手调色、不要顺手重打光**。

> 依据：xxg-portrait-rebuild-light 的 scope 机制——`texture-only` 强制锁定光、曝光、白平衡、对焦、景深与背景；它同时写明"Never let texture repair become relighting"。

## 第 2 步：诊断（先定位，再写词）

AI 味分五类，对应五种手段，**不要一上来就写提示词**：

| 类型 | 症状 | 手段 |
| --- | --- | --- |
| 皮肤响应 | 塑料、蜡像、磨皮、全脸均匀反光 | 提示词（皮肤段） |
| 光与空间 | 人脸和背景不是一套光、贴片感 | EDIT 里的锁光句 + 局部重绘 |
| 材质 | 衣料像色块、发丝成块、无织纹 | 提示词（材质段） |
| 渲染残留 | 全画面同等锐利、HDR 感、无噪点 | 提示词 + 可选后处理 |
| 结构缺陷 | 手指、牙齿、耳朵、瞳孔错 | **换/修结构，提示词救不了** |

完整判定与强度分档见 `references/diagnosis.md`。

## 第 3 步：写提示词（四行结构）

不要写成一段散文，按四行给模型分工：

```
EDIT：锁什么（身份 / 画框 / 光学），只改什么
SKIN：皮肤怎么响应光
MATERIAL：衣料与头发什么质感
AVOID：本次最可能出现的 2–3 个失败
```

**中文模板（默认，给中文指令友好的模型）：**

```
EDIT：以原图为唯一参考，保持人物身份、五官比例、脸型、发际线、表情、姿态、
机位、景别、裁切、背景与光向完全不变；只优化皮肤与衣料的质感表现，不改变任何结构。
SKIN：肤色连续干净、呈哑光；鼻头与颧骨只保留小的、有边界的反光；
面颊毛孔柔和，鼻翼毛孔略清晰；唇纹独立可见；眼周结构不动；
细节密度随对焦与光照自然衰减，不要全脸同等锐化。
MATERIAL：衣料织纹可见，褶皱有方向，边缘有细微磨损；发丝根根分明不成块，发梢略虚。
AVOID：换脸、改变构图或景别、把皮肤磨成塑料。
```

**英文模板（给英文指令更强的模型）：**

```
Keep this exact photograph. Preserve identity, facial proportions, hairline, expression,
pose, camera angle, framing, crop, background and light direction; change only skin and
fabric rendering.
Skin: clean continuous tone, matte; small bounded highlights on nose tip and cheekbones;
pores soft on cheeks, slightly clearer on nose; lip texture separate; eye area untouched;
detail fading with focus and light rather than uniformly sharp.
Fabric: visible weave, directional creases, faint wear at edges. Hair: individual strands,
no clumps, soft tips.
Not: face swap, reframing, plastic skin.
```

**篇幅**：55–110 词最佳，上限 135 词。写不下说明你想改的东西太多了。

> 依据：xxg-portrait-rebuild-light "Target `55–110` English words, with an absolute ceiling of `135` for combined edits."；四行结构参考其 EDIT/SKIN/QUALITY/AVOID 编译格式，**去掉了 LIGHT 行**——本技能锁光，不写光段。

## 第 4 步：能局部就别整图

| 情况 | 做法 |
| --- | --- |
| 只有脸/局部皮肤假 | **只涂皮肤区域**做局部重绘，遮罩别碰五官边缘 |
| 只有衣服材质假 | 只涂衣物区域 |
| 整张都偏渲染 | 整图编辑，但提示词里必须带画框锁 |

整图重绘是改构图的主要来源。局部重绘能改的，不要整图。

> 依据：beatra-ai/ai-image-realism-skill "Prefer the smallest local repair that can address the issue"；以及"never silently replace an edit request with a newly generated image"。

**遮罩要点**：只覆盖目标材质区域，向内收 2–3 px 避开硬边；不要把五官、发际线、下巴轮廓圈进去。

## 第 5 步：出图

**出几张、跑几轮，由用户决定，本技能不规定数量。** 只给两条有出处的提醒：

1. **一轮只改一个维度。** 同一轮里既改皮肤又改光又调色，出问题无法归因。
2. **多轮编辑会累积色偏、噪点、锐化与接缝。** 做到第三轮以上时，把**最初的原图**作为身份与色彩基准一起传给模型，而不是拿上一轮的结果当基准。

> 依据：xxg-portrait-rebuild-light 的 `multi-round-repair`——"use the latest accepted edit as the target and the first root base as the stable identity/geometry/color reference"；以及"Repair after every round before continuing, and do not attach the full history."

## 第 6 步：验收（先查越界，再查质感）

顺序不能反：先确认没改坏，再确认改好了。

**越界四项（任一不过即失败，直接弃图）**
1. 脸还是同一个人（痣、耳形、发际线、眼距对得上）
2. 构图、景别、裁切没变（宽高比漂移 ≤5%）
3. 背景没被重画
4. 光向没变（阴影还是落在同一侧）

**质感三项**
1. 100% 放大能看见毛孔，但毛孔不是平铺的噪点
2. 反光只出现在朝向主光的平面，不是全脸一层油
3. 衣料有织纹与褶皱方向，不是一个色块

完整清单与评分见 `references/qc-checklist.md`。

## 第 7 步：失败怎么办

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 几乎没变化 | 模型把"保持"理解成"不动" | 只加强一个可观察目标（如"鼻翼毛孔略清晰"），不要加更多约束 |
| 换脸了 | 提示词里重述了人物 | 删掉所有人物/场景描述，只留 EDIT 锁与材质要求 |
| 更塑料了 | 加了光泽词或"精致/无瑕" | 删词，改成正向的哑光 + 有边界反光 |
| 皮肤变成噪点 | 把"毛孔"写成了"细节丰富" | 按区域写：面颊柔和、鼻翼略清晰 |
| 构图变了 | 整图重绘 + 描述了场景 | 改局部重绘，或补画框锁后重来 |

> 依据：xxg-portrait-rebuild-light "If a result is nearly unchanged, strengthen one observable target. If skin becomes artificial, replace SKIN with [clean continuous source skin tone...]"；以及"On retry, replace the failed line instead of appending more instructions."

## 三条禁忌

1. **不写风格词**：`cinematic`、`HDR`、`8K`、`超写实`、`大师作品`、`完美无瑕` 全是 AI 味来源，不是解药。
2. **不造脏**：不要主动加雀斑、痘印、粗毛孔、统一颗粒来"制造真实"——那是另一种假。原图已有的标记保留，但不新增、不放大。
3. **不用本地滤镜顶替提示词**：本技能产出提示词；后处理只在模型仍偏锐时作为补强，且不得裁切、缩放、改构图。详见 `references/post-process.md`。

## 参考文件索引

| 文件 | 内容 |
| --- | --- |
| `references/diagnosis.md` | 五类 AI 味的判定表、强度分档、症状→手段 |
| `references/skin-and-material.md` | 皮肤/发丝/织物的正向词，按区域与材质分类，禁区清单 |
| `references/model-notes.md` | 各编辑模型的能力差异、负面词该不该写、指令风格 |
| `references/qc-checklist.md` | 越界四项、质感三项、副作用清单、评分与决策 |
| `references/post-process.md` | 可选补强：仅处理渲染残留，禁止改画框 |
| `references/evidence.md` | **每条规则的出处**：上游仓库、文件、原文要点、许可 |
