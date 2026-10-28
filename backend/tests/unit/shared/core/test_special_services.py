import base64

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import BusinessError


def test_model_key_is_encrypted_once_and_rejects_oversize():
    from short_drama.service.ai_config.management.models import AIModelConfigService

    cipher = KeyCipher(base64.b64encode(b"k" * 32).decode())
    service = AIModelConfigService(Session(), cipher=cipher)
    encrypted = service._encrypt_key(SecretStr("private-api-key"))
    assert cipher.decrypt(encrypted) == "private-api-key"
    assert "private-api-key" not in encrypted
    assert service._encrypt_key(None) is None
    with pytest.raises(BusinessError, match="too long") as caught:
        service._encrypt_key(SecretStr("s" * 65535))
    assert "ssss" not in str(caught.value)


def test_generation_records_reject_generic_update():
    from short_drama.service.creation.storyboard.management.records import ScriptShotRecordService

    for cls in (ScriptShotRecordService,):
        with pytest.raises(BusinessError):
            cls(Session()).update(1, {})


def test_generation_rejects_empty_output_without_touching_database():
    from short_drama.service.generations.results.generation import GenerationService

    with pytest.raises(BusinessError, match="at least one"):
        GenerationService(Session()).generate_shots(1, [])
