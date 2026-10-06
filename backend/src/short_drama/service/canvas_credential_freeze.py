"""仅将本人画布渠道鉴权冻结入现有加密信封，不持久化秘密 JSON。"""

import json

from pydantic import ValidationError

from short_drama.ai.canvas_credentials import (
    CANVAS_AUTH_ADAPTERS,
    CANVAS_AUTH_SCENES,
    CanvasCredentials,
)
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AIGenerationRecord


def freeze_canvas_credentials(record: AIGenerationRecord, secrets: dict, cipher: KeyCipher) -> None:
    scene = (record.request_data.get("source") or {}).get("scene")
    if scene not in CANVAS_AUTH_SCENES or record.adapter not in CANVAS_AUTH_ADAPTERS:
        raise WorkflowError("canvas_generation_credentials_invalid", "画布鉴权范围无效", 422)
    try:
        values = CanvasCredentials.model_validate(
            {
                "version": 1,
                "apiKey": secrets.get("apiKey", ""),
                "headers": [
                    {"name": name, "value": value}
                    for name, value in secrets.get("headers", {}).items()
                ],
            }
        )
    except (ValueError, TypeError, ValidationError):
        raise WorkflowError(
            "canvas_generation_credentials_invalid", "画布渠道鉴权参数无效", 422
        ) from None
    plaintext = {
        "version": 1,
        "apiKey": values.api_key.get_secret_value(),
        "headers": [
            {"name": item.name, "value": item.value.get_secret_value()} for item in values.headers
        ],
    }
    record.credential_cipher = cipher.encrypt(
        json.dumps(plaintext, ensure_ascii=False, separators=(",", ":"))
    )
    record.config_snapshot = {
        **record.config_snapshot,
        "canvas_auth_version": 1,
        "canvas_auth_scene": scene,
    }
