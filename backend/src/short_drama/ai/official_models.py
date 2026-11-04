"""公开官方型号目录；只把已核实型号用于协议能力和模型获取。"""

import json
from importlib.resources import files
from urllib.parse import urlsplit

CATALOG = json.loads(
    files("short_drama.ai").joinpath("data/official_media_models.json").read_text(encoding="utf-8")
)


def protocol_models(interface_type: str) -> dict[str, dict]:
    return {
        model["id"]: model
        for provider in CATALOG["providers"]
        if provider["interface_type"] == interface_type
        for model in provider["models"]
    }


def official_model_options(service_type: str) -> list[dict]:
    return [
        {"id": model["id"], "interface_type": provider["interface_type"]}
        for provider in CATALOG["providers"]
        if provider["service_type"] == service_type
        for model in provider["models"]
    ]


def builtin_catalog(base_url: str, service_type: str, interface_type: str) -> list[str] | None:
    host = urlsplit(base_url).hostname
    for provider in CATALOG["providers"]:
        if (
            host in provider["hosts"]
            and provider["service_type"] == service_type
            and interface_type in {"auto", provider["interface_type"]}
            and provider["discovery"] == "builtin"
        ):
            return [model["id"] for model in provider["models"]]
    return None
