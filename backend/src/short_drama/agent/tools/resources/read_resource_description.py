from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="按当前任务附件目录中的 ref_id 读取授权文本、媒体或文件资源。",
    when_to_use="用户附件或已获准资源需要作为本轮事实、图片或媒体输入时调用。",
    avoid=(
        "不要传宿主路径、MinIO bucket/key 或 Skill 内部路径；Skill 包资源使用 read_skill_resource。"
    ),
    prerequisites=(
        "ref_id 来自当前任务附件/资源目录；purpose 说明为何读取，媒体可选 detail/crop，长文本用 "
        "offset/limit 分段。"
    ),
    effect="只读，服务端按被冻结的 ResourceRef 解析存储身份并重新校验访问权。",
    returns="返回资源内容或受控图像/媒体引用及分页信息；读取结果不自动变成作品正文。",
    recovery="资源不可用或过期时重新读取当前目录，不要改用推测的 URL 或路径。",
    example='{"ref_id":"attachment:375104027219726700","purpose":"核对用户提供的场景参考","offset":0,"limit":4000}',
).render()
