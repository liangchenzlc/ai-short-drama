from short_drama.service.creation.assembly.document.editing import AssemblyEditing
from short_drama.service.creation.assembly.document.projection import AssemblyProjection
from short_drama.service.creation.assembly.document.rendering import AssemblyRendering


class ProjectAssemblyService(AssemblyProjection, AssemblyEditing, AssemblyRendering):
    """组合独立职责；共享同一会话，事务仍由具体操作完整持有。"""
