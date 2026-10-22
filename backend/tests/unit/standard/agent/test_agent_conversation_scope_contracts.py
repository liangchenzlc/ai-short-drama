"""创作会话使用稳定对象范围，不能通过标题或部分字段推断归属。"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.agent.legacy.authorization import scoped_task
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.agent import ConversationCreate, ConversationScope


def test_scoped_conversation_keeps_decimal_subject_identifier():
    payload = ConversationCreate(
        project_id="1",
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
        {"stage": "source", "subject_type": "project", "subject_id": "3", "task_type": "writing"},
    ],
)
def test_creation_rejects_partial_or_invalid_scope(scope):
    with pytest.raises(ValidationError):
        ConversationCreate(project_id="1", **scope)


def test_legacy_creation_requires_no_scope_fields_and_no_client_scope_version():
    legacy = ConversationCreate(
        project_id="1",
    )
    assert legacy.stage is None and legacy.subject_id is None
    with pytest.raises(ValidationError):
        ConversationCreate(project_id="1", scope_version=1)


def test_expected_scope_can_select_one_objects_tasks_but_never_partial_subject():
    scope = ConversationScope(stage="assets", subject_type="asset", subject_id="3")
    assert scope.task_type is None
    with pytest.raises(ValidationError):
        ConversationScope(stage="assets", subject_type="asset")


def test_project_writing_task_is_bound_to_the_conversation_work():
    conversation = SimpleNamespace(
        scope_version=1,
        stage="source",
        subject_type="project",
        subject_id=17,
        task_type="writing",
    )
    spec = scoped_task(conversation, {"kind": "script", "instructions": "write scene"})
    assert spec.target_id == 17
    for payload in (
        {"kind": "script", "target_id": "18", "instructions": "outside work"},
        {"kind": "image", "instructions": "wrong stage"},
    ):
        with pytest.raises(WorkflowError) as rejected:
            scoped_task(conversation, payload)
        assert rejected.value.code == "agent_scope_mismatch"
