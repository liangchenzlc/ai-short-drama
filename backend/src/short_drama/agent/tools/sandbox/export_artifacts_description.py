from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="把当前 scope 工作区中的明确文件归档为可交接的会话资源。",
    when_to_use="文件已完成且父任务或用户需要读取、下载或交接它时调用。",
    avoid="不要导出临时文件、凭据或未完成内容；不能用摘要声称文件已归档。",
    prerequisites="paths 是当前工作区内已存在的相对文件路径；每次最多提交契约允许的文件数。",
    effect="读取并归档列出的文件；图像/音频/视频会被真实检查并返回媒体身份。",
    returns="返回真实 ResourceRef/result_refs；只有这些引用表示文件已归档并可交接。",
    recovery="归档失败时保持原文件并按错误修复后再导出；不要手工伪造资源引用。",
    example='{"paths":["outputs/continuity-report.md"]}',
).render()
