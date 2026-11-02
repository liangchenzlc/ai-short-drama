from short_drama.agent.sandbox.resources import ImportResources
from short_drama.agent.tools.sandbox.runtime import resource_spec

SPEC = resource_spec(
    "runtime__import_resources",
    "按授权 ResourceRef 显式导入当前代码环境的相对路径，不接受宿主路径或 bucket/key。",
    ImportResources,
    "import_resources",
    "resources",
)
