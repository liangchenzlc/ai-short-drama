"""Credential replacement remains possible after a master-key change or corrupt envelope."""

import base64

import pytest
from fastapi.testclient import TestClient
from legacy_identity import session_factory

from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.core.crypto import KeyCipher
from short_drama.domain import AIModelConfig
from short_drama.main import create_app
from short_drama.service.ai_model_config_service import AIModelConfigService

pytestmark = pytest.mark.integration
OLD_KEY = base64.b64encode(b"a" * 32).decode()
CURRENT_KEY = base64.b64encode(b"b" * 32).decode()


@pytest.mark.parametrize(
    "stored,changes,current_key,status,expected_key,version",
    [
        ("old", {"apikey": "replacement"}, CURRENT_KEY, 200, "replacement", "2"),
        ("corrupt", {"apikey": "replacement"}, CURRENT_KEY, 200, "replacement", "2"),
        ("old", {"apikey": "original"}, CURRENT_KEY, 200, "original", "2"),
        ("old", {"apikey": None}, CURRENT_KEY, 200, None, "2"),
        ("old", {"apikey": ""}, CURRENT_KEY, 200, None, "2"),
        ("old", {"apikey": None}, "invalid", 200, None, "2"),
        ("old", {"apikey": "replacement"}, "invalid", 503, "unchanged", "1"),
        ("old", {"name": "renamed"}, CURRENT_KEY, 200, "unchanged", "2"),
        ("old", {"apikey": "replacement", "row_version": "2"}, CURRENT_KEY, 409, "unchanged", "1"),
        ("current", {"apikey": "original"}, CURRENT_KEY, 200, "unchanged", "1"),
    ],
)
def test_http_credential_repair(
    mysql_engine, db_session, stored, changes, current_key, status, expected_key, version
):
    original_cipher = KeyCipher(CURRENT_KEY if stored == "current" else OLD_KEY)
    item = AIModelConfigService(db_session, cipher=original_cipher).create(
        {
            "name": "credential repair",
            "service_type": "text",
            "provider": "test",
            "model_key": "test",
            "apikey": "original",
        }
    )
    with db_session.begin():
        row = db_session.get(AIModelConfig, item.id)
        if stored == "corrupt":
            row.apikey = "invalid-envelope"
        original_envelope = row.apikey

    factory = session_factory(mysql_engine)

    def test_session():
        with factory() as session:
            yield session

    app = create_app(Settings(_env_file=None, encryption_key=current_key))
    app.dependency_overrides[get_session] = test_session
    with TestClient(app) as client:
        response = client.patch(
            f"/api/v1/ai-model-configs/{item.id}",
            json={"row_version": "1", **changes},
        )
        assert response.status_code == status, response.text
        assert "apikey" not in response.text
        assert "replacement" not in response.text
        assert "original" not in response.text
        assert original_envelope not in response.text
        if status == 200:
            assert response.json()["row_version"] == version
            assert response.json()["has_api_key"] is (expected_key is not None)
        if status == 503:
            assert response.json()["error"]["code"] == "configuration_error"

    with db_session.begin():
        db_session.expire_all()
        persisted = db_session.get(AIModelConfig, item.id)
        assert str(persisted.row_version) == version
        if expected_key == "unchanged":
            assert persisted.apikey == original_envelope
        elif expected_key is None:
            assert persisted.apikey is None
        else:
            assert KeyCipher(CURRENT_KEY).decrypt(persisted.apikey) == expected_key
            assert persisted.apikey != original_envelope
