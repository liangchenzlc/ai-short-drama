"""创作会话使用稳定对象范围，不能通过标题或部分字段推断归属。"""

import pytest
from pydantic import ValidationError

from short_drama.schemas.agent import ConversationCreate, ConversationScope


def test_scoped_conversation_keeps_decimal_subject_identifier():
    payload = ConversationCreate(
        project_id="1",
        episode_id="2",
        stage="assets",
        subject_type="asset",
        subject_id="18446744073709551615",
        task_type="creation",
    )
    assert payload.model_dump(mode="json")["subject_id"] == "18446744073709551615"
    assert (
        ConversationScope.model_validate(
            payload.model_dump(include={"stage", "subject_type", "subject_id", "task_type"})
        ).subject_type
        == "asset"
    )


@pytest.mark.parametrize(
    "scope",
    [
        {"stage": "assets"},
        {"stage": "assets", "subject_type": "asset", "subject_id": "3"},
        {"stage": "source", "subject_type": "asset", "subject_id": "3", "task_type": "writing"},
        {"stage": "assets", "subject_type": "asset", "subject_id": "3", "task_type": "video"},
        {"stage": "storyboard", "subject_type": "shot", "subject_id": "3", "task_type": "writing"},
        {"stage": "source", "subject_type": "episode", "subject_id": "3", "task_type": "writing"},
    ],
)
def test_creation_rejects_partial_or_invalid_scope(scope):
    with pytest.raises(ValidationError):
        ConversationCreate(project_id="1", episode_id="2", **scope)


def test_legacy_creation_requires_no_scope_fields_and_no_client_scope_version():
    legacy = ConversationCreate(project_id="1", episode_id="2")
    assert legacy.stage is None and legacy.subject_id is None
    with pytest.raises(ValidationError):
        ConversationCreate(project_id="1", episode_id="2", scope_version=1)


def test_expected_scope_can_select_one_objects_tasks_but_never_partial_subject():
    scope = ConversationScope(stage="assets", subject_type="asset", subject_id="3")
    assert scope.task_type is None
    with pytest.raises(ValidationError):
        ConversationScope(stage="assets", subject_type="asset")
