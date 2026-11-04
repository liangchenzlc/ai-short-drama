from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="把当前 scope 已激活方法版本中的脚本或模板复制到工作区只读版本路径。",
    when_to_use="当前 Skill 的受控资源需要被沙箱程序读取，但不应执行或修改来源时调用。",
    avoid="不要传入未激活方法、未列入 manifest 的路径；不要覆盖物化结果或将它当成已归档成果。",
    prerequisites="activation_id 来自当前 scope 的活动 Skill；path 是冻结 manifest 中的相对路径。",
    effect="创建版本和校验和独立的工作区文件；不会执行文件，也不会覆盖已有修改内容。",
    returns="返回工作区路径及冻结 ResourceRef。",
    recovery="文件已变化或 manifest 不符时重新读取当前激活资源；不要手工复制外部版本。",
    example='{"activation_id":"375104027219726700","path":"assets/output-template.md"}',
).render()
