import pytest
from pydantic import ValidationError

from short_drama.core.config import Settings
from short_drama.schemas.agent import ConversationCreate, ConversationPatch


def test_agent_defaults_on_and_allows_explicit_opt_out(monkeypatch):
    monkeypatch.delenv("AGENT_ENABLED", raising=False)
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    settings = Settings(_env_file=None)
    assert settings.auth_enabled is True
    assert settings.agent_enabled is True
    assert Settings(_env_file=None, agent_enabled=False, auth_enabled=False).agent_enabled is False


def test_agent_requires_authentication(monkeypatch):
    monkeypatch.delenv("AGENT_ENABLED", raising=False)
    with pytest.raises(ValidationError, match="requires AUTH_ENABLED"):
        Settings(_env_file=None, auth_enabled=False)
    with pytest.raises(ValidationError, match="requires AUTH_ENABLED"):
        Settings(_env_file=None, agent_enabled=True, auth_enabled=False)


def test_conversation_identifiers_are_strings_and_private_fields_are_forbidden():
    dto = ConversationCreate(project_id="18446744073709551615", episode_id="1", title=" 标题 ")
    assert dto.title == "标题"
    assert dto.model_dump(mode="json")["project_id"] == "18446744073709551615"
    with pytest.raises(ValidationError):
        ConversationCreate(project_id="1", episode_id="2", owner_user_id="3")


@pytest.mark.parametrize("changes", [{}, {"title": None}, {"title": "  "}, {"archived": None}])
def test_conversation_patch_rejects_ambiguous_changes(changes):
    with pytest.raises(ValidationError):
        ConversationPatch(row_version=1, **changes)


def test_patch_preserves_omitted_fields():
    assert ConversationPatch(row_version=1, archived=False).model_dump(exclude_unset=True) == {
        "row_version": 1,
        "archived": False,
    }
