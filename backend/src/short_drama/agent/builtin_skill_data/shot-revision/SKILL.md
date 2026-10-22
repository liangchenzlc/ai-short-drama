---
name: drama-shot-revision
description: 定点调整已选镜头的画面、动作、提示词、时长或关联，保留未授权内容并检查前后衔接。
license: 参考 zenstory-ai/drama-skills short-drama-storyboard 与 short-drama-review，MIT，归档 2026-10-20；本项目修订规则。
metadata:
  short-drama-display-name: 镜头定点修订
  short-drama-tags: [分镜, 修订, 镜头, 关联]
  short-drama-exclusions: 不用旧快照覆盖新编辑，不顺手重排整片，不把提示词修订当成视频已重生成。
---

# 镜头定点修订

适用于修改已选分镜或画布节点的指定内容。目标名称存在歧义时按真实身份澄清。

必要输入是原镜头保存版本、指定问题、允许修改的字段及可见的衔接依据。未选上下镜头不可自动展开读取；可以说明衔接判断的局限。

1. 固定镜头原本承担的信息和用户要求，判断是文字修订、参数变化、真实素材关联还是顺序变化。
2. 保留无关字段和已确认事实，只写必要差异；改动作时同步核对该镜头的起止状态。
3. 用实际选入身份处理关联。时长和参数按当前工具契约，不把文字建议当作可直接执行的接口字段。
4. 明确修改授权后调用最小结构批次，保留版本校验。冲突时保留草案并等待刷新或明确处理。
5. 以真实回执报告变更，提示词或时长修改不自动生成、重配音、删除旧媒体或更换当前采用结果。

按需使用[局部影响检查](references/method.md)及[修订表](assets/output-template.md)。自检目标、数量、无关内容保留及声画衔接。

来源许可见[MIT 原文](references/upstream-license.txt)。
