from short_drama.service.generations.execution.archive import ResultArchiving
from short_drama.service.generations.execution.provider import ProviderExecution
from short_drama.service.generations.execution.settlement import ExecutionSettlement


class GenerationExecutionService(ProviderExecution, ResultArchiving, ExecutionSettlement):
    """组合独立职责；共享同一会话，事务仍由具体操作完整持有。"""
