import base64
import os

import pytest
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.dao.base import BaseDAO
from short_drama.domain import AIModelConfig
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.model_discovery_service import ModelDiscoveryError


@pytest.mark.parametrize("stored_url", ["", "https://old.example/v1"])
def test_no_stored_key_allows_new_address(monkeypatch, stored_url):
    row = AIModelConfig(id=1, base_url=stored_url, apikey=None, is_deleted=0)
    monkeypatch.setattr(BaseDAO, "get", lambda *args, **kwargs: row)
    with Session() as session:
        service = AIModelConfigService(session, settings=Settings(_env_file=None))
        assert service.key_for_discovery(1, "https://new.example/v1") is None


def test_stored_key_only_decrypts_for_original_address(monkeypatch):
    key = base64.b64encode(os.urandom(32)).decode()
    cipher = KeyCipher(key)
    row = AIModelConfig(
        id=1,
        base_url="https://original.example/v1/",
        apikey=cipher.encrypt("stored-key"),
        is_deleted=0,
        row_version=7,
    )
    original = row.apikey
    monkeypatch.setattr(BaseDAO, "get", lambda *args, **kwargs: row)
    with Session() as session:
        service = AIModelConfigService(
            session, settings=Settings(_env_file=None, encryption_key=key)
        )
        assert (
            service.key_for_discovery(1, "https://original.example/v1").get_secret_value()
            == "stored-key"
        )
        with pytest.raises(ModelDiscoveryError) as caught:
            service.key_for_discovery(1, "https://elsewhere.example/v1")
        assert caught.value.code == "model_discovery_key_address_changed"
    assert row.apikey == original and row.row_version == 7
