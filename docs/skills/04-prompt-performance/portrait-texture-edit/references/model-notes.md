# 模型差异：编辑场景怎么下指令

模型名字与参数变化很快，以下按"能力类型"归类，具体型号以官方文档为准。

## 一、通用三条

1. **必须传原图。** 纯文字描述无法保住身份——"same person as the reference"这类话只是辅助，真正起作用的是图本身。
2. **自然语言模型写完整句子，不要堆标签。** Nano Banana / Gemini、GPT Image、Seedream、Flux 都属于这一类。
3. **编辑指令要显式声明"只改什么"。** 上游实测最有效的一句是 `keep everything, change only X`。

> 依据：`photorealistic-skill/references/models.md`「General」——natural-language models respond best to full descriptive sentences；"always pass that image as a reference and say 'same person as the reference'"。

## 二、按模型族

### GPT Image 2

- 指令风格：自然语言，能吃"保持构图、只改皮肤"这类条件式指令
- 倾向：默认输出偏**商业精修**（干净、通透、饱和），这正是 AI 味来源
- 应对：明确写"哑光、有边界的高光"，并在 AVOID 里点名"不要塑料皮肤"
- 注意：它很擅长"整体提升"，反而容易在提升过程中改掉光影关系

### Nano Banana / Gemini 系

- **最擅长编辑**：`keep everything, change only X` 这类指令跟随度最高
- 负面词：**没有独立的 negative 通道**，只能写在正文末尾，且要用短的 `not X` 句式
- 不要加 Midjourney 参数（`--ar`、`--no`），会污染指令
- 风险：它很听话，你说"提升质感"它可能顺手把整张图重画一遍——所以身份锁与画框锁必须写在第一句

> 依据：photorealistic-skill `models.md`「Nano Banana / Gemini image」——"Very good at following edit instructions on a reference ('keep everything, change only X')"；"Negatives must be written inside the prompt as short 'not X' phrases at the end."；junhao-ye 的平台处理章节同样要求 Nano Banana 用完整自然语言、不加 MJ 参数。

### Seedance / 即梦系（图像编辑）

- 中文短句更稳，长句容易被截断理解
- 避免 `8K`、`超写实`、`极致细节`——这一族对强度词很敏感，容易过锐
- 编辑时把"保持原图"写在最前，不要放在末尾

### Flux / Flux Kontext

- 多数部署**没有负面词通道**，全部正向写：`matte skin with a small highlight on the nose` 而不是 `no plastic skin`
- guidance 值偏低的版本渲染感更弱
- 对"区域性细节"的响应不如 Nano Banana 精确，建议局部重绘配合

> 依据：photorealistic-skill `models.md`「Flux」——"No negative prompt support in most setups; phrase everything positively… Lower guidance values tend to look less 'rendered'."

### Stable Diffusion / SDXL（局部重绘）

- 有独立负面词字段，可以堆：`plastic skin, airbrushed, extra fingers, deformed hands, oversaturated, HDR, cartoon, 3d render`
- CFG 不要拉太高，过高会加剧"过度处理感"
- 修手、修脸用照片训练的 checkpoint 做 inpaint，比整图重绘稳

> 依据：photorealistic-skill `models.md`「Stable Diffusion family」。

### Midjourney

- 编辑能力弱于上面几家，主要用于"重新生成接近的图"，**不满足本技能"不改构图"的要求**
- 若必须用：`--style raw` + 低 `--stylize`（约 0–100），排除项走 `--no`，3–5 条即可
- 结论：需要严格保持原图时，优先选有编辑/参考能力的模型

> 依据：photorealistic-skill `models.md`「Midjourney」；以及 xxg-portrait-rebuild-light 的定位——它处理的是"编辑已有照片"，而非生成近似图。

## 三、负面词：写不写，看模型

| 模型 | 独立负面通道 | 建议 |
| --- | --- | --- |
| Nano Banana / Gemini | 无 | 正文末尾短句 `not X`，最多 3 条 |
| GPT Image 2 | 无 | AVOID 段，2–3 条 |
| Seedream / 即梦 | 视版本 | 少量中文短句，别堆长列表 |
| Flux | 多数无 | 全部改正向描述 |
| SD / SDXL | 有 | 可堆 10–20 条 |
| Midjourney | `--no` | 3–5 项 |

**通用判断**：负面词列表越长，越容易被忽略，甚至被反向理解成"要出现这些"。3–5 条是安全区。

> 依据：photorealistic-skill `SKILL.md`——"Negatives: a few, at the end, phrased as short 'not X' items. Long negative lists get ignored or inverted on many models."

## 四、参考图怎么传

- 单图编辑：只传原图，说"Image 1 是唯一参考，只改 X"。
- 多轮编辑：传 `[当前结果, 最初原图]`，并声明"Image 1 是编辑目标；Image 2 仅用于身份/几何/色彩的锁定"。
- **不要把所有中间版本都传进去**，会互相干扰。

> 依据：xxg-portrait-rebuild-light「Use the Host Image Editor」——"pass exactly `[CURRENT_EDIT, ROOT_BASE]`… Do not attach every intermediate round."

## 五、宽高比

编辑请求必须保持原始宽高比。上游给的容忍阈值是漂移 ≤5%，超过即视为改了画框。

> 依据：xxg-portrait-rebuild-light「Preserve the Frame」——"Accept relative aspect-ratio drift of `≤5%`."
