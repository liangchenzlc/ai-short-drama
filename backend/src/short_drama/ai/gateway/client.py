from short_drama.ai.gateway.canvas import CanvasGateway
from short_drama.ai.gateway.polling import GenerationPolling
from short_drama.ai.gateway.runninghub import RunningHubGateway
from short_drama.ai.gateway.submissions import GenerationSubmissions
from short_drama.ai.gateway.transport_client import GatewayTransport


class GenerationGateway(
    GenerationSubmissions, RunningHubGateway, CanvasGateway, GenerationPolling, GatewayTransport
):
    """组合独立职责；共享同一会话，事务仍由具体操作完整持有。"""
