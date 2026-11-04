from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="读取当前已激活 Skill 冻结 manifest 中指定的资料、模板、图片或脚本文件。",
    when_to_use="Skill 正文要求参考某个具体附件，且该资料影响当前任务时调用。",
    avoid="不要读取未激活 Skill 的文件；不要把 Skill 路径交给普通附件工具或直接执行脚本。",
    prerequisites=(
        "activation_id 来自 load_skill；path 必须是该激活 manifest "
        "中列出的相对路径；offset/limit 用于分段读取。"
    ),
    effect="只读，版本由 activation 冻结；图片按需水合，不会执行文件。",
    returns="返回文件种类、冻结版本引用、正文/图片内容或分页游标。",
    recovery="路径不在 manifest 时重新读取当前激活的资源清单；不要拼接目录外路径。",
    example='{"activation_id":"375104027219726700","path":"references/method.md","offset":0,"limit":4000,"purpose":"核对分镜方法步骤"}',
).render()
