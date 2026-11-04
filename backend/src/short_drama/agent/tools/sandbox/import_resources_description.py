from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="把已授权的会话 ResourceRef 显式复制到当前沙箱 scope 的相对路径。",
    when_to_use="子任务或代码运行确实需要在工作区打开已交接文件时调用。",
    avoid="不要提供宿主绝对路径、URL、bucket/key 或自行构造的 ResourceRef。",
    prerequisites=(
        "每个 resource 必须来自本 scope 获准目录/明确交接；path 是工作区相对路径，不能含 .. "
        "或绝对路径。"
    ),
    effect="把授权资源实体复制进当前 scope 工作区；不修改来源资源。",
    returns="返回导入路径、校验后的资源身份和文件元数据。",
    recovery="授权/校验失败时回到来源目录确认 ResourceRef；不要尝试直接下载或换用存储路径。",
    example='{"resources":[{"resource":{"schema_version":1,"kind":"text","ref_id":"sandbox:abc","uri":"artifact://123","source":"sandbox_artifact","version":null,"checksum":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","metadata":{},"availability":"available"},"path":"inputs/notes.md"}]}',
).render()
