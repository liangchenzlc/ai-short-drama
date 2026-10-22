"""Project assistant validation fails before any database or provider call."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.core.config import Settings
from short_drama.core.identity import ActorContext
from short_drama.main import create_app


@pytest.fixture
def client():
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))

    def session():
        with Session() as value:
            value.info["actor"] = ActorContext(1, "test", "test@example.test", True, 1, "", "test")
            yield value

    app.dependency_overrides[get_session] = session
    with TestClient(app) as client:
        yield client


def test_assistant_status_and_disabled_creation_never_invokes_a_model(client):
    assert client.get("/api/v1/assistant/status").json() == {
        "enabled": False,
        "schema_ready": False,
    }
    response = client.post("/api/v1/assistant/conversations/resolve", json={"project_id": "1"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_disabled"


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "stop", "message_id": "2"},
        {"kind": "redirect", "content": "改方向", "message_id": "2"},
        {"kind": "resume", "expected_control_seq": "1", "message_id": "0"},
        {"kind": "resume", "expected_control_seq": "1", "message_id": "2", "content": "重复"},
    ],
)
def test_resume_message_reuse_rejects_ambiguous_or_invalid_sources(client, payload):
    response = client.post(
        "/api/v1/assistant/tasks/1/control",
        headers={"Idempotency-Key": "reuse-source"},
        json=payload,
    )
    assert response.status_code == 422


def test_resume_message_reuse_forwards_exact_decimal_identities(client):
    from short_drama.api.v1.assistant import task_service
    from short_drama.core.exceptions import Conflict

    class Tasks:
        def control_task(self, identifier, payload, key):
            assert identifier == 9007199254740993
            assert payload.message_id == 9007199254740995
            assert payload.expected_control_seq == 9007199254740997
            assert payload.kind == "resume" and payload.content is None and key == "reuse-source"
            raise Conflict("停止状态已变化")

    client.app.dependency_overrides[task_service] = Tasks
    response = client.post(
        "/api/v1/assistant/tasks/9007199254740993/control",
        headers={"Idempotency-Key": "reuse-source"},
        json={
            "kind": "resume",
            "expected_control_seq": "9007199254740997",
            "message_id": "9007199254740995",
        },
    )
    assert response.status_code == 409


def test_project_task_routes_forward_project_scope_and_cannot_rebind_a_rename(client):
    from short_drama.api.v1.assistant import task_service
    from short_drama.core.exceptions import Conflict

    class Tasks:
        def list_global_conversations(self, offset, limit, *, search, project_id):
            assert (offset, limit, search, project_id) == (0, 20, "故事", 9007199254740993)
            return {"items": [], "total": 0, "offset": 0, "limit": 20}

        def historical_conversations(self, offset, limit, *, search, project_id):
            assert (offset, limit, search, project_id) == (0, 20, None, 9007199254740993)
            return {"items": [], "total": 0, "offset": 0, "limit": 20}

        def create_global_conversation(self, payload, key):
            assert payload.project_id == 9007199254740993 and key == "project-chat"
            raise Conflict("请求已经受理")

    client.app.dependency_overrides[task_service] = Tasks
    root = "/api/v1/assistant/global"
    assert (
        client.get(
            root + "/conversations", params={"project_id": "9007199254740993", "query": "故事"}
        ).status_code
        == 200
    )
    assert (
        client.get(root + "/history", params={"project_id": "9007199254740993"}).status_code == 200
    )
    assert (
        client.post(
            root + "/conversations",
            json={"project_id": "9007199254740993"},
            headers={"Idempotency-Key": "project-chat"},
        ).status_code
        == 409
    )
    assert (
        client.patch(
            root + "/conversations/1",
            json={"row_version": "1", "title": "重命名", "project_id": "2"},
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "edit", "row_version": "1", "content": "   "},
        {"kind": "edit", "row_version": "0", "content": "讨论"},
        {"kind": "edit", "row_version": "1.5", "content": "讨论"},
        {"kind": "regenerate", "row_version": "1", "content": "不能改写原文"},
        {"kind": "regenerate", "row_version": "1", "owner_user_id": "2"},
        {"kind": "regenerate", "row_version": "1", "execution_target": {}},
    ],
)
def test_discussion_revision_rejects_invalid_versions_and_execution_input(client, payload):
    response = client.post(
        "/api/v1/assistant/global/conversations/1/messages/2/revisions",
        headers={"Idempotency-Key": "original-revision"},
        json=payload,
    )
    assert response.status_code == 422


def test_discussion_revision_keeps_decimal_identity_and_original_request_key(client):
    from short_drama.api.v1.assistant import task_service
    from short_drama.core.exceptions import Conflict

    class Tasks:
        def revise_discussion(self, conversation, message, payload, key):
            assert conversation == 9007199254740993 and message == 9007199254740995
            assert payload.row_version == 9007199254740997
            assert payload.content == "修订正文" and key == "original-revision:1"
            raise Conflict("来源已有执行依赖")

        def discussion_eligibility(self, conversation, message):
            assert conversation == 9007199254740993 and message == 9007199254740995
            return {
                "allowed": False,
                "code": "assistant_history_dependency",
                "reason": "来源已有执行依赖",
                "row_version": 9007199254740997,
                "current_content": "当前正文",
            }

    client.app.dependency_overrides[task_service] = Tasks
    path = "/api/v1/assistant/global/conversations/9007199254740993/messages/9007199254740995"
    assert client.get(path + "/revision-eligibility").json() == {
        "allowed": False,
        "code": "assistant_history_dependency",
        "reason": "来源已有执行依赖",
        "row_version": "9007199254740997",
        "current_content": "当前正文",
    }
    payload = {"kind": "edit", "row_version": "9007199254740997", "content": "修订正文"}
    assert client.post(path + "/revisions", json=payload).status_code == 422
    response = client.post(
        path + "/revisions", headers={"Idempotency-Key": "original-revision:1"}, json=payload
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"preview_hash": "a" * 64, "selected_keys": []},
        {"preview_hash": "invalid", "selected_keys": ["asset:1"]},
        {"preview_hash": "a" * 64, "selected_keys": ["asset:1", "asset:1"]},
        {"preview_hash": "a" * 64, "selected_keys": ["asset:1"], "owner_user_id": "2"},
    ],
)
def test_batch_undo_rejects_unversioned_empty_duplicate_or_forged_selection(client, body):
    response = client.post(
        "/api/v1/assistant/operations/1/undo",
        headers={"Idempotency-Key": "original-undo"},
        json=body,
    )
    assert response.status_code == 422


def test_batch_undo_routes_keep_decimal_identity_and_original_request(client):
    from short_drama.api.v1.assistant import task_service

    class Tasks:
        def undo_preview(self, identifier):
            assert identifier == 9007199254740993
            return {
                "operation_id": identifier,
                "preview_hash": "a" * 64,
                "items": [],
                "receipts": [],
            }

        def undo_batch(self, identifier, body, key):
            assert identifier == 9007199254740993 and key == "original-undo"
            assert body.preview_hash == "a" * 64
            assert body.selected_keys == ["asset:9007199254740995"]
            return {
                "id": 9007199254740997,
                "operation_id": identifier,
                "selected_keys": body.selected_keys,
                "result_refs": [],
                "created_at": "2026-10-22T00:00:00Z",
            }

    client.app.dependency_overrides[task_service] = Tasks
    path = "/api/v1/assistant/operations/9007199254740993"
    assert client.get(path + "/undo-preview").json()["operation_id"] == "9007199254740993"
    body = {"preview_hash": "a" * 64, "selected_keys": ["asset:9007199254740995"]}
    assert client.post(path + "/undo", json=body).status_code == 422
    response = client.post(path + "/undo", headers={"Idempotency-Key": "original-undo"}, json=body)
    assert response.status_code == 201 and response.json()["id"] == "9007199254740997"


def test_generation_policy_routes_are_owner_only_and_keep_decimal_versions(client):
    from short_drama.api.v1.assistant import policy_service

    class Policies:
        def get_generation_policy(self):
            return {
                "media_generation_enabled": False,
                "policy_version": 9007199254740993,
                "configured_media": ["image"],
                "available_media": [],
            }

        def update_generation_policy(self, payload):
            assert payload.media_generation_enabled is True
            assert payload.policy_version == 9007199254740993
            return {
                **self.get_generation_policy(),
                "media_generation_enabled": True,
                "policy_version": 9007199254740994,
            }

    client.app.dependency_overrides[policy_service] = Policies
    response = client.get("/api/v1/assistant/generation-policy")
    assert response.status_code == 200
    assert response.json()["media_generation_enabled"] is False
    assert response.json()["policy_version"] == "9007199254740993"
    response = client.patch(
        "/api/v1/assistant/generation-policy",
        json={
            "media_generation_enabled": True,
            "policy_version": "9007199254740993",
        },
    )
    assert response.status_code == 200 and response.json()["policy_version"] == "9007199254740994"


@pytest.mark.parametrize(
    "invalid",
    [
        {"owner_user_id": "2"},
        {"media_generation_enabled": "false"},
        {"policy_version": "-1"},
        {"available_media": ["image"]},
    ],
)
def test_generation_policy_rejects_owner_and_capability_injection(client, invalid):
    response = client.patch(
        "/api/v1/assistant/generation-policy",
        json={
            "media_generation_enabled": False,
            "policy_version": "0",
            **invalid,
        },
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "extra",
    [
        {"mode": "generate"},
        {"task": {"kind": "image"}},
        {"owner_user_id": "2"},
        {"context": {"kind": "canvas", "id": "c", "revision": "1", "nodes": []}},
    ],
)
def test_chat_rejects_execution_and_untrusted_source_content(client, extra):
    response = client.post(
        "/api/v1/assistant/conversations/1/messages",
        json={"content": "讨论作品", **extra},
        headers={"Idempotency-Key": "message-1"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_global_creation_does_not_require_a_project_but_honors_disabled_status(client):
    response = client.post(
        "/api/v1/assistant/global/conversations",
        json={},
        headers={"Idempotency-Key": "global-1"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_disabled"


@pytest.mark.parametrize(
    "extra",
    [
        {"project_id": "invalid"},
        {"archived": True},
        {"owner_user_id": "2"},
    ],
)
def test_global_creation_rejects_legacy_and_untrusted_scope_fields(client, extra):
    response = client.post(
        "/api/v1/assistant/global/conversations",
        json=extra,
        headers={"Idempotency-Key": "global-1"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "extra",
    [
        {"owner_user_id": "2"},
        {"scope_type": "conversation"},
        {"scope_type": "user", "project_id": "1"},
        {"kind": "unknown"},
    ],
)
def test_memory_management_rejects_untrusted_owners_and_inconsistent_scopes(client, extra):
    response = client.post(
        "/api/v1/assistant/memory",
        headers={"Idempotency-Key": "memory-1"},
        json={
            "scope_type": "user",
            "kind": "preference",
            "memory_key": "dialogue",
            "title": "对白",
            "content": "对白要短",
            **extra,
        },
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "validation_error"


def test_memory_context_serializes_ids_and_zero_generation_without_provider_data(client):
    from short_drama.api.v1.assistant_memory import service

    class MemoryDetails:
        def conversation_context(self, identifier):
            assert identifier == 9007199254740993
            return {
                "conversation_id": identifier,
                "covers_until_seq": 0,
                "summary_generation": 0,
                "summary": None,
                "jobs": [],
            }

    client.app.dependency_overrides[service] = MemoryDetails
    response = client.get("/api/v1/assistant/memory/conversations/9007199254740993/context")
    assert response.status_code == 200
    assert response.json() == {
        "conversation_id": "9007199254740993",
        "covers_until_seq": 0,
        "summary_generation": "0",
        "summary": None,
        "jobs": [],
    }


def test_deletion_preview_serializes_versions_and_routes_pagination(client):
    from short_drama.api.v1.assistant import task_service

    class Tasks:
        def deletion_memory_preview(self, identifier, offset, limit):
            assert (identifier, offset, limit) == (9007199254740993, 30, 10)
            return {
                "conversation_id": identifier,
                "row_version": 9007199254740995,
                "items": [
                    {
                        "id": 9007199254740997,
                        "content_version": 2,
                        "scope_type": "project",
                        "project_id": 9007199254740999,
                        "title": "对白",
                        "content_preview": "保留后来修改的事实",
                        "protection_reasons": ["user_edited", "independent_sources"],
                    }
                ],
                "total": 31,
                "offset": offset,
                "limit": limit,
            }

    client.app.dependency_overrides[task_service] = Tasks
    response = client.get(
        "/api/v1/assistant/global/conversations/9007199254740993/deletion-memory?offset=30&limit=10"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["conversation_id"] == "9007199254740993"
    assert data["row_version"] == "9007199254740995"
    assert data["items"][0] == {
        "id": "9007199254740997",
        "content_version": "2",
        "scope_type": "project",
        "project_id": "9007199254740999",
        "title": "对白",
        "content_preview": "保留后来修改的事实",
        "protection_reasons": ["user_edited", "independent_sources"],
    }


@pytest.mark.parametrize(
    "fields",
    [
        {"memory_selections": [{"id": "1", "content_version": "1"}]},
        {
            "clear_derived_memory": True,
            "memory_selections": [
                {"id": "1", "content_version": "1"},
                {"id": "1", "content_version": "2"},
            ],
        },
        {"clear_derived_memory": True, "memory_selections": [{"id": "1"}]},
    ],
)
def test_deletion_rejects_unconfirmed_duplicate_or_unversioned_selections(client, fields):
    response = client.request(
        "DELETE",
        "/api/v1/assistant/global/conversations/1",
        headers={"Idempotency-Key": "delete-invalid"},
        json={"row_version": "1", **fields},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_memory_review_serializes_large_ids_and_versioned_choices(client):
    from short_drama.api.v1.assistant_memory import service

    reviewed = {
        "id": 9007199254740993,
        "conversation_id": 9007199254740995,
        "from_seq": 1,
        "to_seq": 2,
        "clarifications": [
            {
                "index": 0,
                "memory_id": 9007199254740997,
                "content_version": 2,
                "candidate_base_version": 1,
                "title": "对白",
                "current_content": "当前内容",
                "candidate_content": "候选内容",
                "source_message_ids": [9007199254740999],
                "status": "pending",
                "choice": None,
            }
        ],
    }

    class Memories:
        def review_jobs(self, identifier, offset, limit):
            assert (identifier, offset, limit) == (9007199254740995, 10, 5)
            return {"items": [reviewed], "total": 11, "offset": offset, "limit": limit}

        def resolve_clarification(self, identifier, index, payload, key):
            assert identifier == 9007199254740993 and index == 0 and key == "choice"
            assert payload.content_version == 2 and payload.choice == "keep_current"
            return reviewed

    client.app.dependency_overrides[service] = Memories
    response = client.get(
        "/api/v1/assistant/memory/conversations/9007199254740995/reviews?offset=10&limit=5"
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["id"] == "9007199254740993"
    assert item["clarifications"][0]["source_message_ids"] == ["9007199254740999"]
    response = client.post(
        "/api/v1/assistant/memory/jobs/9007199254740993/clarifications/0",
        headers={"Idempotency-Key": "choice"},
        json={"content_version": "2", "choice": "keep_current"},
    )
    assert response.status_code == 200


def test_phase_summary_serializes_parent_and_message_identities_without_provider_data(client):
    from short_drama.api.v1.assistant_memory import service

    class Memories:
        def read_summary(self, conversation_id, summary_id):
            assert (conversation_id, summary_id) == (9007199254740993, 9007199254740995)
            return {
                "id": summary_id,
                "base_summary_id": 9007199254740997,
                "from_seq": 21,
                "to_seq": 30,
                "token_estimate": 100,
                "created_at": "2026-10-21T00:00:00Z",
                "facts": [
                    {
                        "kind": "user_fact",
                        "text": "新阶段保留约束",
                        "source_message_ids": [9007199254740999],
                        "subject_keys": [],
                        "prior_fact_indexes": [0],
                        "source_summary_id": 9007199254740997,
                    }
                ],
            }

    client.app.dependency_overrides[service] = Memories
    response = client.get(
        "/api/v1/assistant/memory/conversations/9007199254740993/summaries/9007199254740995"
    )
    assert response.status_code == 200
    values = response.json()
    assert values["id"] == "9007199254740995" and values["base_summary_id"] == "9007199254740997"
    assert values["facts"][0]["source_message_ids"] == ["9007199254740999"]
    assert values["facts"][0]["source_summary_id"] == "9007199254740997"
    assert "raw_result" not in values
