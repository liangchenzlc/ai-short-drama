from short_drama.agent.sandbox.resources import ExportArtifacts
from short_drama.agent.tools.sandbox.runtime import resource_spec

SPEC = resource_spec(
    "runtime__export_artifacts",
    "将当前工作区的相对文件清单归档为会话授权资源；图片、音频和视频经真实检查后返回媒体身份。",
    ExportArtifacts,
    "export_artifacts",
    "paths",
)
