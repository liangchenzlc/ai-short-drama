# 归档来源：Awesome Seedance 2.0 Prompts

| 项 | 内容 |
| --- | --- |
| 分类 | `04-prompt-performance` |
| 上游 | GitHub · AtlasCloudAI/awesome-seedance-2-prompts |
| 地址 | https://github.com/AtlasCloudAI/awesome-seedance-2-prompts |
| 许可 | CC BY 4.0 |
| 采用度 | 11 star（2026-10-16 新建，体量小但同源权威） |
| 快照时间 | 2026-10-20 |
| 性质 | 纯提示词数据库（无 SKILL.md，区别于 2.5 的 Skill 包装版） |
| 四维评分 | 效率 4 / 质量 4 / 人数 2 / 价值 4 → 加权 **3.85**（与 2.5 同档；人数低因新建仓，但同源已验证质量） |

## 入选理由

Seedance 2.0 的实测提示词库，2,706 条带分类、视频示例链接与作者溯源，并提供简体中文版（zh.json）。与已收录的 2.5 提示词库**同作者、同许可、同结构**，形成 2.0 / 2.5 双版本覆盖；中文指南直接整理自火山引擎官方《Doubao Seedance 2.0 系列提示词指南》，可作为项目提示词模板库批量导入的语料，而不是又一套流水线。

## 已归档内容

- `data/prompts.json`：2,706 条，英文主库（id / title / description / prompt / category / video_url / author_name / source）
- `data/prompts_by_locale/zh.json`：2,706 条，简体中文版
- `docs/PROMPTING_GUIDE.zh.md`：火山引擎官方提示词指南中文整理
- `LICENSE`：CC BY 4.0

## 未归档

- `README.md`（约 2.2 MB）：为 2,706 条提示词的整表渲染副本，与 `data/` 结构化数据冗余，故不入库以控制体积。
- 其余 19 个语种包（ar / de / en / es / fr / hi / id / it / ja / ko / nl / pl / pt / ru / sv / th / tr / vi / zh-TW）：未归档，仅保留简体中文版。
- `skills/` 为空：本仓不含可安装 Skill，仅为数据，因此本目录是「提示词库」而非「技能」。

## 使用约束

- 本目录为**提示词语料库**，非可运行 Skill；接入前需按项目 `AGENTS.md` 的分层约定挑选 / 转译后使用。
- 上游许可以上表为准；再分发需保留署名（CC BY 4.0）。
- 本快照未跟踪上游后续提交，更新请回到上游地址重新获取。
