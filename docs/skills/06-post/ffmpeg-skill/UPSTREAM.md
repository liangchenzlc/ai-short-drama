# 归档来源：ffmpeg-skill（本地音视频处理）

| 项 | 内容 |
| --- | --- |
| 分类 | `06-post` |
| 上游 | GitHub · kajisho5/ffmpeg-skill |
| 地址 | https://github.com/kajisho5/ffmpeg-skill |
| 许可 | MIT |
| 采用度 | 2,024 star |
| 快照时间 | 2026-10-20 |
| 四维评分 | 效率 5 / 质量 5 / 人数 4 / 价值 5 → 加权 **4.85** |

## 入选理由

纯本地、零 API 成本，覆盖无损剪切/拼接、9:16 重构、字幕与卡拉OK、响度标准化（-14 LUFS）、HDR 转 SDR、LUT 调色、平台导出质检，并提供 44 个可直接复用的脚本与 gotchas 踩坑清单；项目成片渲染本身走 FFmpeg，是迁移成本最低的一项。

## 已归档内容

SKILL.md、references/（5 份含 gotchas）、scripts/（44 个）、templates/（10 个平台模板）、docs/、README、LICENSE

未归档：docs/demos 的 gif 演示、assets/、evals/、tests/ 未归档

## 使用约束

- 本目录只做**方法论与规则参考**，不构成可直接运行的集成；接入前需按项目 `AGENTS.md` 的分层约定改造。
- 上游许可以上表为准；升级或再分发时需同步复核许可条款。
- 本快照未跟踪上游后续提交，更新请回到上游地址重新获取。
