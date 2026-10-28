from short_drama.service.skills.catalog.editing import SkillEditing
from short_drama.service.skills.catalog.imports import SkillImports
from short_drama.service.skills.catalog.listing import SkillListing
from short_drama.service.skills.catalog.resource_content import SkillResources


class AgentSkillService(SkillListing, SkillImports, SkillResources, SkillEditing):
    """组合独立职责；共享同一会话，事务仍由具体操作完整持有。"""
