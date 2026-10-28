from short_drama.ai.gateway.transport import SafeTransport


class GatewayBase:
    def __init__(self, settings):
        self.settings = settings
        self.transport = SafeTransport(settings)
