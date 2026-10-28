from short_drama.agent.runtime.claiming import RuntimeClaiming
from short_drama.agent.runtime.publishing import RuntimePublishing
from short_drama.agent.runtime.sending import RuntimeSending
from short_drama.agent.runtime.settlement import RuntimeSettlement


class AgentRuntimeStore(RuntimePublishing, RuntimeClaiming, RuntimeSending, RuntimeSettlement):
    """组合独立职责；共享同一会话，事务仍由具体操作完整持有。"""
