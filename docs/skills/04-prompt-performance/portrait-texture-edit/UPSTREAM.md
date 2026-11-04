# 来源记录：人物质感编辑（本项目合成）

> **性质**：本技能不是上游仓库的归档快照，而是基于多个公开 skill 的**正文**重新组织的原创合成件。
> 中文表述为自行撰写，未逐句复制上游文本；每条规则的出处登记在 `references/evidence.md`，可逐条回查。

| 项 | 内容 |
| --- | --- |
| 分类 | `04-prompt-performance`（去 AI 感 · 图/视频提示词方法论） |
| 性质 | 本项目合成（first-party） |
| 合成时间 | 2026-10-29（初版同日重写） |
| 应用场景 | **已有一张人像，用提示词提升质感、去 AI 味** |
| 硬约束 | 不换人、不改景别、不改构图、不改机位、不改背景、不改姿态表情、不改光向 |
| 处理对象 | 人脸皮肤、发丝、服装材质、渲染残留 |
| 不做 | 从零生成、换背景、换装、改构图、改景别、换脸、分镜 |

## 一、为什么重写（初版的问题）

初版是按"生成"写的，与本项目实际场景不符，已整体推翻。具体问题：

| 初版问题 | 后果 | 重写后 |
| --- | --- | --- |
| 按生成流程设计（先建参考图再生成） | 用户已有图，流程从第一步就错了 | 改为单图编辑，第 0 步先判断有没有原图 |
| 建议"一次出 4 张" | 越权规定出图数量 | 明确写出"出图数量由用户决定"，只给"一轮只改一个维度"的依据性建议 |
| 建议"加颗粒、加瑕疵"造真实感 | 违反上游明文禁止（不造脏） | 改为"不造脏"，并单列禁区章节 |
| 允许重打光修贴片感 | 会改变光向，违背"不改原图" | 光向纳入锁定项，只有用户明确要求才放开（L3） |
| 规则多为经验描述，无出处 | 无法验证 | 新增 `references/evidence.md`，逐条标注出处与原文 |

## 二、依据来源（全部读过正文）

| 上游 | 地址 | 许可 | 采用度 | 本技能采用了什么 |
| --- | --- | --- | --- | --- |
| xxg-portrait-rebuild-light | https://github.com/moskoo/xxg-portrait-rebuild-light | MIT | 222 star | **主干**：编辑已有照片的定位、scope 机制（texture-only 锁光）、身份六组、画框锁与 ≤5% 比例阈值、皮肤三条信号、区域化细节、不造脏、55–110 词篇幅、多轮用原图做基准 |
| photorealistic-skill | https://github.com/ricardochristovao/photorealistic-skill | MIT | 1 star | 缺陷三分类（People/Scene/Finish）、结构走 inpaint 渲染走降渲染、触发词黑名单、负面词少而短、各模型编辑能力差异 |
| im2-clean-image | https://github.com/q2522879285-source/im2-image-skills | 未核实 | 11 star | 材质-光物理层、信息密度衰减、"不用 8K 替代材质物理"、"宁可保留轻微模糊也不凭空造细节"、负面词卫生 |
| photo-retouch-pro | https://github.com/qzfcoder/photo-retouch-pro | 未声明 | 13 star | 四级强度分级、编辑前锁保护区、"不靠饱和/锐化/美白/模糊制造可见效果" |
| muse-creative-skills · photoreal-realness | https://github.com/nanbada/muse-creative-skills | MIT | 1 star | 先锁身份再加真实感、"崩す not 盛る"（破而非堆）、不承诺必定真实 |
| ai-image-realism-skill | https://github.com/beatra-ai/ai-image-realism-skill | 未核实 | 4 star | 最小局部修复优先、不得把编辑请求换成重新生成、一轮修一个问题簇 |
| sns-realistic-portrait-prompt | https://github.com/junhao-ye/sns-realistic-portrait-prompt | 未核实 | 8 star | perfect/flawless/8K/CGI/doll-like 黑名单、先写可见事实再写摄影语言 |

完整逐条对照见 `references/evidence.md`（含英文原句摘录与编号 R1–R39、本项目推导 P1–P6）。

## 三、本项目新增（上游没有的）

1. **L1–L4 编辑范围分级**：把上游的 scope 机制改写成中文项目能直接用的四级，并明确 L4（画框）本技能不支持
2. **越界四项检查顺序**：身份 → 画框 → 背景 → 光向，按"最不可挽回"排序
3. **中文四行提示词模板**：EDIT / SKIN / MATERIAL / AVOID，贴合中文指令友好的模型
4. **诊断五分类**：把上游的 People/Scene/Finish 三分组拆成皮肤、光空间、材质、渲染残留、结构五类，并给出"提示词能不能修"的判定
5. **明确不规定出图数量**：只给"一轮一个维度"与"多轮用原图做基准"两条有出处的建议

## 四、不采用的上游做法

| 上游做法 | 不采用的原因 |
| --- | --- |
| photorealistic-skill "Always generate 2-4 variants" | 生成场景建议，本技能不规定出图数量 |
| beatra 的 `count=1` 与付费调用链 | 绑定第三方账号额度，违反准入硬门槛③ |
| muse 的 20 个 realness blocks 全量注入 | 上游自己写明不能全贴 |
| xxg 禁止用 FFmpeg/Pillow 出成品 | 本技能放宽为"保守补强"，风险已在 `post-process.md` 标注 |

## 五、使用约束

- 本技能只产出**提示词与判定标准**，不含可运行代码，不接任何付费服务。
- 上游许可未核实/未声明的部分只吸收观点，**不复制文本、不再分发其资产**；要引入原文须先复核许可。
- 参数为经验值（尤其后处理的 Texture / Clarity 数值），换模型或换分辨率需重新标定，标定结果回写本文件。
- 不跟踪上游后续提交；上游升级时回原地址核对，不静默沿用旧结论。
