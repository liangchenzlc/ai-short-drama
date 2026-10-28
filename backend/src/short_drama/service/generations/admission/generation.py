from short_drama.service.generations.admission.actions import GenerationActions
from short_drama.service.generations.admission.admission import GenerationAdmission
from short_drama.service.generations.admission.queries import GenerationQueries


class AIGenerationService(GenerationAdmission, GenerationQueries, GenerationActions):
    """组合独立职责；共享同一会话，事务仍由具体操作完整持有。"""

    create_generation = GenerationAdmission.create
    get = GenerationQueries.detail
