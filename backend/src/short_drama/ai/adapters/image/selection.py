"""Protocol choice is pure: explicit interface or a trusted provider catalog."""

from urllib.parse import urlsplit

from short_drama.ai.adapters.image.contracts import ResolvedImageProtocol
from short_drama.ai.adapters.image.registry import IMAGE_INTERFACES, ImageProtocol
from short_drama.ai.types import GenerationError


def resolved(protocol: ImageProtocol, snapshot: dict) -> ResolvedImageProtocol:
    return ResolvedImageProtocol(
        protocol.id,
        protocol.adapter.adapter_id,
        protocol.revision,
        protocol.adapter.capabilities(snapshot),
    )


class ExplicitProtocolStrategy:
    def resolve(self, snapshot: dict, interface_type: str) -> ResolvedImageProtocol:
        protocol = IMAGE_INTERFACES.get(interface_type)
        if protocol is None:
            raise GenerationError("unsupported_protocol")
        return resolved(protocol, snapshot)


class CatalogProtocolStrategy:
    def resolve(self, snapshot: dict, interface_type: str = "auto") -> ResolvedImageProtocol:
        host = (urlsplit(snapshot["base_url"]).hostname or "").lower()
        protocol = {
            "api.openai.com": "openai-image",
            "generativelanguage.googleapis.com": "gemini-image",
            "api.x.ai": "grok-image",
            "dashscope.aliyuncs.com": "qwen-image",
            "dashscope-intl.aliyuncs.com": "qwen-image",
            "dashscope-us.aliyuncs.com": "qwen-image",
        }.get(host)
        if host.endswith(".volces.com"):
            protocol = "volcengine-ark-image"
        elif host.endswith(".maas.aliyuncs.com"):
            protocol = "qwen-image"
        if protocol is None:
            raise GenerationError("unsupported_protocol")
        return resolved(IMAGE_INTERFACES[protocol], snapshot)


EXPLICIT = ExplicitProtocolStrategy()
CATALOG = CatalogProtocolStrategy()


def select_image_protocol(snapshot: dict) -> ResolvedImageProtocol:
    interface_type = (snapshot.get("runtime_profile") or {}).get("protocol", "auto")
    return (CATALOG if interface_type == "auto" else EXPLICIT).resolve(snapshot, interface_type)
