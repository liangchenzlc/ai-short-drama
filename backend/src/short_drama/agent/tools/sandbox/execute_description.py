from short_drama.agent.tools.contracts import ToolDescription

DESCRIPTION = ToolDescription(
    purpose="在当前隔离工作区的受限沙箱中执行一条 shell 命令。",
    when_to_use="文件工具无法完成的构建、测试、转换或计算需要执行程序时调用。",
    avoid=(
        "不要用 shell 搜索替代 ls/glob/grep；不要访问宿主机、凭据或网络边界；"
        "不要用命令执行未经用户授权的破坏性操作。"
    ),
    prerequisites=(
        "command 为单条命令，路径含空格须引用；timeout 可选且单位为秒。命令在隔离 scope 中执行。"
    ),
    effect="执行沙箱内命令，可能修改当前工作区文件；不直接写项目数据库。",
    returns="返回 stdout/stderr 合并文本和退出码；超大输出可能截断。",
    recovery="检查退出码和输出后按需修正；仅输出截断时缩窄命令结果或通过文件工具分段读取。",
    example='{"command":"python -m pytest tests/test_example.py -q","timeout":120}',
).render()
