"""真实 Actor/ORM 的 SQLite 行为验证；不是 MySQL 并发或对象存储验收。"""

from contextlib import nullcontext
from copy import deepcopy
from datetime import timedelta

import pytest
from catalog_fixtures import ASSET, CANVAS, KEY, MEDIA, OWNING, SOURCE, actor
from pydantic import ValidationError
from sqlalchemy import event, func, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AsyncTask,
    CanvasResourceCopySource,
    CanvasResourceUpload,
    CanvasTaskBinding,
    MediaFile,
    Project,
    ProjectCanvas,
)
from short_drama.domain.canvas import (
    CanvasMediaReference,
    CanvasNode,
    CanvasNodeUserState,
    CanvasRevision,
    CanvasUserState,
    CanvasWriteReceipt,
)
from short_drama.domain.collaboration import ProjectMember
from short_drama.schemas.canvas import CanvasCreateRequest, CanvasDocument
from short_drama.service.asset_version_service import AssetVersionService
from short_drama.service.base import utcnow
from short_drama.service.canvas_character_catalog_service import CanvasCharacterCatalogService
from short_drama.service.canvas_service import CanvasService

COPY = MEDIA + 20


@pytest.fixture(autouse=True)
def sqlite_mutex_only(monkeypatch):
    monkeypatch.setattr(
        "short_drama.service.canvas_service.canvas_folder_write_lock", lambda session: nullcontext()
    )
    monkeypatch.setattr(CanvasService, "_global_order_lock", lambda self: nullcontext())


def seed_copy(baseline, *, source=MEDIA, ready=True, project_id=OWNING, creator=1, identifier=COPY):
    now = utcnow()
    with baseline.begin():
        baseline.add(
            MediaFile(
                id=identifier,
                project_id=project_id,
                format_code="image/png",
                storage_locator=f"minio://fixture-only/copy-{identifier}.png",
                original_name="copy.png",
                byte_size=12,
                width=2,
                height=3,
                created_by=creator,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.add(
            CanvasResourceUpload(
                id=identifier + 1,
                user_id=creator,
                project_id=project_id,
                canvas_id=CANVAS if project_id == OWNING else None,
                idempotency_hash=str(identifier).zfill(64),
                request_hash="b" * 64,
                mode="copy",
                status="ready" if ready else "pending",
                declared_json={},
                reserved_resource_id=identifier,
                storage_locator=f"minio://fixture-only/copy-{identifier}.png",
                byte_size=12,
                expires_at=now + timedelta(hours=1),
                media_id=identifier if ready else None,
                created_by=creator,
                updated_by=creator,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.flush()
        baseline.add(
            CanvasResourceCopySource(
                id=identifier + 2,
                upload_id=identifier + 1,
                original_resource_id=source,
                source_media_id=None if ready else source,
                snapshot_json={"resource_ancestors": []},
                released_at=now if ready else None,
                created_at=now,
            )
        )


def document(session, *, cover=COPY, canvas_key=KEY, owning=OWNING, source_id=SOURCE):
    item = CanvasCharacterCatalogService(session).get(canvas_key, ASSET).item
    return CanvasDocument.model_validate(
        {
            "id": canvas_key,
            "workspaceProjectId": str(owning),
            "projectId": str(source_id),
            "title": "作品",
            "nodes": [
                {
                    "id": "character-one",
                    "type": "text",
                    "title": item.name,
                    "position": {"x": 0, "y": 0},
                    "width": 320,
                    "height": 260,
                    "metadata": {
                        "workflowKind": "character",
                        "characterAssetId": str(ASSET),
                        "characterVersionId": str(item.current_version_id),
                        "characterVersionPolicy": "current",
                        "characterName": item.name,
                        "characterAliases": [],
                        "characterDefinition": item.definition,
                        "characterCoverUrl": (
                            f"/api/v1/canvas-runtime/resources/{cover}/file" if cover else ""
                        ),
                        "characterPrompt": "author-only-parameters",
                        "characterVoiceName": "unverified-voice",
                        "characterVoiceProfile": {"provider": "private-voice"},
                        "taskId": "100",
                    },
                }
            ],
            "connections": [],
        }
    )


def commit(service, source, *, expected=7, key="character-test"):
    return service.commit(
        OWNING, CANVAS, {"expected_row_version": str(expected), "source_document": source}, key
    )


def state(baseline):
    with baseline.begin():
        return {
            "version": baseline.get(ProjectCanvas, CANVAS).row_version,
            "published": baseline.get(MediaFile, COPY).published_at,
            "rows": [
                baseline.scalar(select(func.count()).select_from(model))
                for model in (
                    CanvasNode,
                    CanvasMediaReference,
                    CanvasRevision,
                    CanvasWriteReceipt,
                    CanvasUserState,
                    CanvasNodeUserState,
                )
            ],
        }


def source_character_workflow(*, kind="image", three_view=False):
    metadata = {
        "content": "",
        "composerContent": "source-parameters",
        "prompt": "source-parameters",
        "workflowKind": "character",
        "workflowTitle": "源角色工作流",
        "status": "idle",
        "generationMode": "image",
        "model": "fixture-only",
    }
    if three_view:
        metadata["characterView"] = "multi"
    return {
        "id": KEY,
        "workspaceProjectId": str(OWNING),
        "projectId": str(SOURCE),
        "title": "源角色工作流",
        "nodes": [
            {
                "id": "source-character-workflow",
                "type": kind,
                "title": "源角色工作流",
                "position": {"x": 80, "y": 80},
                "width": 560,
                "height": 380,
                "metadata": metadata,
            }
        ],
        "connections": [],
    }


@pytest.mark.parametrize("three_view", [False, True], ids=["character_cards", "three_view"])
def test_source_image_character_workflow_without_identity_can_save(catalog_session, three_view):
    session, baseline = catalog_session
    source = source_character_workflow(three_view=three_view)
    result = commit(CanvasService(session), source)
    assert result["row_version"] == "8"
    with session.begin():
        service = CanvasService(session)
        canvas = session.get(ProjectCanvas, CANVAS)
        shared = service._document(canvas, private=False)
        authored = service._document(canvas, private=True)
    node = shared["nodes"][0]
    assert node["type"] == "image"
    assert (node["width"], node["height"]) == (560, 380)
    assert node["metadata"]["workflowKind"] == "character"
    if three_view:
        assert node["metadata"]["characterView"] == "multi"
    assert not {"prompt", "model", "composerContent"}.intersection(node["metadata"])
    assert authored["nodes"][0]["metadata"] == source["nodes"][0]["metadata"]
    with baseline.begin():
        assert baseline.scalar(select(CanvasNode)).kind == "image"


def test_text_character_workflow_without_identity_rejects_and_rolls_back(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    before = state(baseline)
    with pytest.raises(WorkflowError) as caught:
        commit(CanvasService(session), source_character_workflow(kind="text"))
    assert caught.value.code == "canvas_character_invalid"
    assert caught.value.status_code == 422
    assert state(baseline) == before


@pytest.mark.parametrize("field", ["characterAssetId", "characterVersionId", "characterCoverUrl"])
@pytest.mark.parametrize("value", [None, "", "9200000000000001"])
def test_image_with_any_character_identity_key_still_rejects(catalog_session, field, value):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = source_character_workflow()
    if field == "characterCoverUrl" and value:
        value = f"/api/v1/canvas-runtime/resources/{COPY}/file"
    source["nodes"][0]["metadata"][field] = value
    before = state(baseline)
    with pytest.raises(WorkflowError) as caught:
        commit(CanvasService(session), source)
    assert caught.value.code == "canvas_character_invalid"
    assert caught.value.status_code == 422
    assert state(baseline) == before


def test_image_with_complete_character_identity_still_requires_source_text_card(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session).model_dump(mode="json", by_alias=True)
    source["nodes"][0]["type"] = "image"
    before = state(baseline)
    with pytest.raises(WorkflowError) as caught:
        commit(CanvasService(session), source)
    assert caught.value.code == "canvas_character_invalid"
    assert caught.value.status_code == 422
    assert state(baseline) == before


def test_real_copy_publishes_work_and_actor_private_fields_are_not_shared(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session)
    result = commit(CanvasService(session), source)
    assert result["row_version"] == "8"
    with session.begin():
        shared = CanvasService(session)._document(session.get(ProjectCanvas, CANVAS), private=False)
        private = CanvasService(session)._document(session.get(ProjectCanvas, CANVAS), private=True)
    metadata = shared["nodes"][0]["metadata"]
    assert metadata["characterAssetId"] == str(ASSET)
    assert metadata["characterVersionId"] == source.nodes[0].metadata["characterVersionId"]
    assert metadata["characterCoverUrl"].endswith(f"/{COPY}/file")
    assert "characterPrompt" not in metadata and "characterVoiceName" not in metadata
    assert "characterVoiceProfile" not in metadata and "taskId" not in metadata
    assert private["nodes"][0]["metadata"]["characterPrompt"] == "author-only-parameters"
    with baseline.begin():
        assert baseline.get(MediaFile, COPY).published_at is not None
        refs = list(baseline.scalars(select(CanvasMediaReference)))
        assert len(refs) == 1 and refs[0].media_id == COPY


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("characterVersionId", "999999", "canvas_character_version_changed"),
        ("characterAssetId", "999999", None),
        ("characterName", "forged", "canvas_character_work_changed"),
        ("characterDefinition", {"description": "forged"}, "canvas_character_work_changed"),
        ("characterAliases", ["forged"], "canvas_character_work_changed"),
        ("characterVersionPolicy", "pinned", "canvas_character_version_policy_unsupported"),
        ("characterAssetId", ASSET, "canvas_character_invalid"),
        ("characterCoverUrl", "blob:preview", "canvas_character_cover_not_persisted"),
        (
            "characterCoverUrl",
            "https://provider.example/signed",
            "canvas_character_cover_not_persisted",
        ),
    ],
)
def test_forged_work_identity_or_cover_never_commits(catalog_session, field, value, code):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session).model_dump(mode="json", by_alias=True)
    source["nodes"][0]["metadata"][field] = value
    before = state(baseline)
    with pytest.raises((NotFound, WorkflowError)) as caught:
        commit(CanvasService(session), source)
    if code:
        assert caught.value.code == code
    assert state(baseline) == before


@pytest.mark.parametrize("project_id", [OWNING, SOURCE])
def test_current_actor_rechecks_both_project_memberships_before_dml(catalog_session, project_id):
    session, baseline = catalog_session
    seed_copy(baseline, creator=2)
    source = document(session)
    service = CanvasService(session)
    session.info["actor"] = actor(2)
    with baseline.begin():
        member = baseline.scalar(
            select(ProjectMember).where(ProjectMember.project_id == project_id)
        )
        member.status = "removed"
    before = state(baseline)
    with pytest.raises(NotFound):
        commit(service, source)
    assert state(baseline) == before


@pytest.mark.parametrize(
    "source_id,ready,project_id",
    [(MEDIA + 99, True, OWNING), (MEDIA, False, OWNING), (MEDIA, True, SOURCE)],
)
def test_cover_must_be_ready_copy_of_actual_version_slot_in_owning_scope(
    catalog_session, source_id, ready, project_id
):
    session, baseline = catalog_session
    seed_copy(baseline, source=source_id, ready=ready, project_id=project_id)
    source = document(session)
    before = state(baseline)
    with pytest.raises((NotFound, WorkflowError)):
        commit(CanvasService(session), source)
    assert state(baseline) == before


def test_native_source_cover_requires_normalize_first(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session, cover=MEDIA)
    before = state(baseline)
    with pytest.raises(NotFound):
        commit(CanvasService(session), source)
    assert state(baseline) == before


def test_collaborator_can_save_published_work_without_authors_private_copy_receipt(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session)
    service = CanvasService(session)
    commit(service, source)
    session.info["actor"] = actor(2)
    from short_drama.dao.canvas_resource_dao import CanvasResourceDAO

    with session.begin():
        assert CanvasResourceDAO(session).copy_ancestors({COPY}) == {}
        shared = service._document(session.get(ProjectCanvas, CANVAS), private=False)
    assert "characterPrompt" not in shared["nodes"][0]["metadata"]
    shared["nodes"][0]["position"]["x"] = 24
    commit(service, shared, expected=8, key="member-save")
    with session.begin():
        own = service._document(session.get(ProjectCanvas, CANVAS), private=True)
    assert own["nodes"][0]["position"]["x"] == 24
    assert "characterPrompt" not in own["nodes"][0]["metadata"]


def test_source_version_change_blocks_previously_published_cover(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session)
    service = CanvasService(session)
    commit(service, source)
    with baseline.begin():
        asset = baseline.get(Asset, ASSET)
        asset.description = "newly adopted work"
        AssetVersionService(baseline).append_locked(asset)
    before = state(baseline)
    with pytest.raises(WorkflowError) as caught:
        commit(service, source, expected=8, key="outdated-save")
    assert caught.value.code == "canvas_character_version_changed"
    assert state(baseline) == before


def test_failure_after_real_flushed_write_rolls_back_graph_private_refs_and_receipt(
    catalog_session, monkeypatch
):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session)
    service = CanvasService(session)
    before = state(baseline)
    actual = service.record_write

    def fail_after_flush(**kwargs):
        actual(**kwargs)
        raise WorkflowError("fixture_write_failed", "fixture failed after receipt flush", 409)

    monkeypatch.setattr(service, "record_write", fail_after_flush)
    with pytest.raises(WorkflowError, match="fixture failed"):
        commit(service, source)
    assert state(baseline) == before
    monkeypatch.setattr(service, "record_write", actual)
    commit(service, source)
    assert state(baseline)["version"] == 8


def test_nested_private_definition_parameters_do_not_become_shared_work(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session).model_dump(mode="json", by_alias=True)
    source["nodes"][0]["metadata"]["characterDefinition"]["extensions"]["model"] = "private"
    commit(CanvasService(session), source)
    with session.begin():
        shared = CanvasService(session)._document(session.get(ProjectCanvas, CANVAS), private=False)
    extensions = shared["nodes"][0]["metadata"]["characterDefinition"]["extensions"]
    assert extensions == {"nested": [{}, 4]}


def test_credentials_in_character_definition_are_rejected_before_any_save(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session).model_dump(mode="json", by_alias=True)
    source["nodes"][0]["metadata"]["characterDefinition"]["api_key"] = "fixture-only"
    before = state(baseline)
    with pytest.raises(ValidationError, match="credentials must never"):
        commit(CanvasService(session), source)
    assert state(baseline) == before


def test_direct_graph_write_cannot_bypass_character_publication_gate(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session).model_dump(mode="json", by_alias=True)
    source["nodes"][0]["metadata"]["characterVersionId"] = "999999"
    before = state(baseline)
    with pytest.raises(WorkflowError), session.begin():
        CanvasService(session)._replace_graph(session.get(ProjectCanvas, CANVAS), deepcopy(source))
    assert state(baseline) == before


REVERSE_OWNING = SOURCE + 100


def reverse_owning(baseline):
    now = utcnow()
    with baseline.begin():
        baseline.add(
            Project(
                id=REVERSE_OWNING,
                owner_user_id=1,
                name="reverse-owning",
                aspect="16:9",
                synopsis="",
                style="",
                workspace_mode="infinite_canvas",
                row_version=1,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.flush()
        baseline.get(ProjectCanvas, CANVAS).project_id = REVERSE_OWNING


def reverse_document(session):
    source = document(session).model_dump(mode="json", by_alias=True)
    source["workspaceProjectId"] = str(REVERSE_OWNING)
    return source


def reverse_commit(service, source, expected=7, key="reverse-save"):
    return service.commit(
        REVERSE_OWNING,
        CANVAS,
        {
            "expected_row_version": str(expected),
            "source_document": source,
        },
        key,
    )


def trace_project_locks(session):
    locks = []

    def inspect_lock(execute_state):
        statement = execute_state.statement
        if getattr(statement, "_for_update_arg", None) is None:
            return
        descriptions = getattr(statement, "column_descriptions", [])
        if not any(item.get("entity") is Project for item in descriptions):
            return
        for value in statement.compile().params.values():
            if value in (SOURCE, REVERSE_OWNING):
                locks.append(value)

    event.listen(session, "do_orm_execute", inspect_lock)
    return locks


def assert_sorted_first_locks(locks):
    first = list(dict.fromkeys(locks))
    assert first[:2] == [SOURCE, REVERSE_OWNING], locks


def seed_task(baseline, *, task_id=100, canvas_id=CANVAS, owning=REVERSE_OWNING):
    now = utcnow()
    with baseline.begin():
        baseline.add(
            AsyncTask(
                id=task_id,
                project_id=owning,
                initiated_by=1,
                service_type="text",
                status="succeeded",
                idempotency_key=f"private-task-fixture-{task_id}",
                request_hash="c" * 64,
                message_status="idle",
                started_at=now,
                finished_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.flush()
        baseline.add(
            CanvasTaskBinding(
                id=task_id + 1,
                project_id=owning,
                canvas_id=canvas_id,
                initiated_by=1,
                async_task_id=task_id,
                node_key="character-one",
                source_node_key="character-one",
                client_operation_id=f"private-op-fixture-{task_id}",
                request_hash="c" * 64,
                source_snapshot={},
                context_hash="d" * 64,
                created_by=1,
                updated_by=1,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.add(
            AIGenerationRecord(
                id=task_id + 2,
                task_id=task_id,
                call_no=1,
                config_id=999,
                config_snapshot={"provider": "test"},
                request_data={
                    "canvas_request": {
                        "type": "canvas_text",
                        "operation": "generate",
                        "prompt": "fixture",
                        "input": {
                            "mode": "text",
                            "prompt": "fixture",
                            "metadata": {
                                "nodeId": "character-one",
                                "clientOperationId": "private-op-fixture",
                            },
                        },
                    }
                },
                status="succeeded",
                text_content="真实 ORM 测试作品",
                response_data={},
                created_at=now,
                updated_at=now,
            )
        )


def test_commit_locks_reverse_id_scopes_before_owning_project(catalog_session):
    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    source = reverse_document(session)
    session.info["request_project"] = REVERSE_OWNING
    locks = trace_project_locks(session)
    reverse_commit(CanvasService(session), source)
    assert_sorted_first_locks(locks)


def test_create_locks_inherited_reverse_id_scopes_before_new_canvas(catalog_session):
    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    source = reverse_document(session)
    source["id"] = "created-character-fixture"
    request = CanvasCreateRequest.model_validate(
        {
            "title": "new",
            "source_key": source["id"],
            "source_document": source,
            "source_canvas_key": KEY,
            "source_canvas_row_version": "7",
        }
    )
    session.info["request_project"] = REVERSE_OWNING
    locks = trace_project_locks(session)
    result = CanvasService(session).create_for_project(REVERSE_OWNING, request, "create-character")
    assert result["source_project_id"] == str(SOURCE)
    assert_sorted_first_locks(locks)


def test_restore_historical_character_locks_scopes_when_current_graph_has_none(catalog_session):
    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    source = reverse_document(session)
    service = CanvasService(session)
    reverse_commit(service, source)
    with baseline.begin():
        for revision in baseline.scalars(select(CanvasRevision)):
            revision.created_at -= timedelta(minutes=6)
    empty = deepcopy(source)
    empty["nodes"] = []
    reverse_commit(service, empty, expected=8, key="remove-character")
    with baseline.begin():
        revision_id = baseline.scalar(
            select(CanvasRevision.id).where(
                CanvasRevision.canvas_row_version == 8,
            )
        )
    assert revision_id is not None
    session.info["request_project"] = REVERSE_OWNING
    locks = trace_project_locks(session)
    result = service.restore(REVERSE_OWNING, CANVAS, revision_id, 9, "restore-character")
    assert result["row_version"] == "10"
    assert_sorted_first_locks(locks)


@pytest.mark.parametrize("entry", ["detail", "list", "text_replay"])
def test_real_task_materialization_entry_locks_reverse_scopes(catalog_session, entry):
    from types import SimpleNamespace

    from short_drama.service.canvas_generation_service import CanvasGenerationService

    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    reverse_commit(CanvasService(session), reverse_document(session))
    seed_task(baseline)
    session.info["request_project"] = REVERSE_OWNING
    locks = trace_project_locks(session)
    generation = CanvasGenerationService(session, SimpleNamespace())
    result = generation.list(project_id=KEY) if entry == "list" else getattr(generation, entry)(100)
    assert result
    assert_sorted_first_locks(locks)


def test_real_task_bind_entry_locks_reverse_scopes_before_result_publication(catalog_session):
    from short_drama.schemas.canvas_generation import CanvasTaskBindOperation
    from short_drama.service.canvas_task_service import CanvasTaskService

    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    reverse_commit(CanvasService(session), reverse_document(session))
    seed_task(baseline)
    operation = CanvasTaskBindOperation.model_validate(
        {
            "opId": "bind-character-fixture",
            "params": {
                "canvasId": KEY,
                "taskId": "100",
                "nodeId": "character-one",
                "outputIndex": 0,
            },
        }
    )
    session.info["request_project"] = REVERSE_OWNING
    locks = trace_project_locks(session)
    result = CanvasTaskService(session).bind(operation)
    assert result
    assert_sorted_first_locks(locks)


def test_direct_valid_character_graph_cannot_publish_private_author_parameters(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    source = document(session).model_dump(mode="json", by_alias=True)
    before = state(baseline)
    with pytest.raises(WorkflowError) as caught, session.begin():
        CanvasService(session)._replace_graph(session.get(ProjectCanvas, CANVAS), source)
    assert caught.value.code == "canvas_character_private_publication"
    assert state(baseline) == before


def test_generation_create_admission_locks_scopes_before_rejecting_missing_model(catalog_session):
    from types import SimpleNamespace

    from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate
    from short_drama.service.canvas_generation_service import CanvasGenerationService

    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    reverse_commit(CanvasService(session), reverse_document(session))
    payload = CanvasRuntimeTaskCreate.model_validate(
        {
            "projectId": KEY,
            "type": "canvas_text",
            "operation": "generate",
            "prompt": "fixture",
            "logicalModelId": "999",
            "input": {
                "mode": "text",
                "prompt": "fixture",
                "metadata": {
                    "nodeId": "character-one",
                    "clientOperationId": "scope-admission-fixture",
                },
            },
        }
    )
    session.info["request_project"] = REVERSE_OWNING
    locks = trace_project_locks(session)
    with pytest.raises((NotFound, WorkflowError)):
        CanvasGenerationService(session, SimpleNamespace()).create(payload)
    assert_sorted_first_locks(locks)


def test_tool_execute_entry_locks_scopes_before_rejecting_missing_submission(catalog_session):
    from types import SimpleNamespace

    from short_drama.domain import CanvasToolRun
    from short_drama.schemas.canvas_tool_runs import CanvasToolExecute
    from short_drama.service.canvas_tool_run_service import CanvasToolRunService

    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    reverse_commit(CanvasService(session), reverse_document(session))
    now = utcnow()
    with baseline.begin():
        baseline.add(
            CanvasToolRun(
                id=301,
                user_id=1,
                project_id=REVERSE_OWNING,
                canvas_id=CANVAS,
                client_key="fixture-tool",
                create_hash="a" * 64,
                state_json={},
                source_snapshot={},
                source_hash="b" * 64,
                model_config_id=999,
                config_hash="c" * 64,
                row_version=1,
                execution_epoch=1,
                execution_owner="fixture-owner",
                lease_expires_at=now + timedelta(minutes=5),
                created_by=1,
                updated_by=1,
                created_at=now,
                updated_at=now,
            )
        )
    command = CanvasToolExecute.model_validate(
        {
            "executionEpoch": "1",
            "owner": "fixture-owner",
            "submissionId": "999",
        }
    )
    session.info["request_project"] = REVERSE_OWNING
    locks = trace_project_locks(session)
    with pytest.raises(NotFound):
        CanvasToolRunService(session, SimpleNamespace()).execute(301, command)
    assert_sorted_first_locks(locks)


def test_list_prelocks_all_source_and_owning_projects_before_any_materialization(catalog_session):
    from types import SimpleNamespace

    from short_drama.domain import ProjectAsset
    from short_drama.service.canvas_generation_service import CanvasGenerationService

    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    reverse_commit(CanvasService(session), reverse_document(session))
    seed_task(baseline)
    low_source, second_owning, second_canvas = SOURCE - 100, REVERSE_OWNING + 100, CANVAS + 100
    now = utcnow()
    with baseline.begin():
        for identifier, mode in [(low_source, "standard"), (second_owning, "infinite_canvas")]:
            baseline.add(
                Project(
                    id=identifier,
                    owner_user_id=1,
                    name=mode,
                    aspect="16:9",
                    synopsis="",
                    style="",
                    workspace_mode=mode,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        baseline.flush()
        baseline.add(
            ProjectCanvas(
                id=second_canvas,
                project_id=second_owning,
                source_project_id=low_source,
                source_key="second-character-canvas",
                title="second",
                position=1,
                schema_version=1,
                row_version=7,
                properties_json={},
                created_by=1,
                updated_by=1,
                created_at=now,
                updated_at=now,
            )
        )
        baseline.add(
            ProjectAsset(
                id=ASSET + 100,
                project_id=low_source,
                asset_id=ASSET,
                position=1,
            )
        )
    second_copy = COPY + 50
    seed_copy(baseline, project_id=second_owning, identifier=second_copy)
    source = document(
        session,
        cover=second_copy,
        canvas_key="second-character-canvas",
        owning=second_owning,
        source_id=low_source,
    )
    CanvasService(session).commit(
        second_owning,
        second_canvas,
        {
            "expected_row_version": "7",
            "source_document": source,
        },
        "second-character-save",
    )
    seed_task(baseline, task_id=200, canvas_id=second_canvas, owning=second_owning)
    locks = []

    def inspect_lock(execute_state):
        statement = execute_state.statement
        if getattr(statement, "_for_update_arg", None) is None:
            return
        if any(
            item.get("entity") is Project for item in getattr(statement, "column_descriptions", [])
        ):
            locks.extend(
                value
                for value in statement.compile().params.values()
                if value in {low_source, SOURCE, REVERSE_OWNING, second_owning}
            )

    event.listen(session, "do_orm_execute", inspect_lock)
    session.info["request_project"] = REVERSE_OWNING
    result = CanvasGenerationService(session, SimpleNamespace()).list()
    assert len(result) == 2
    assert list(dict.fromkeys(locks)) == [low_source, SOURCE, REVERSE_OWNING, second_owning]


def test_role_without_cover_publishes_real_identity_and_no_media_reference(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    commit(CanvasService(session), document(session, cover=None))
    with baseline.begin():
        assert baseline.scalar(select(func.count()).select_from(CanvasMediaReference)) == 0
        assert baseline.get(MediaFile, COPY).published_at is None
        node = baseline.scalar(select(CanvasNode))
        assert node.content_json["metadata"]["characterAssetId"] == str(ASSET)


def test_shared_cover_cannot_bypass_source_revoke_after_actor_switch(catalog_session):
    session, baseline = catalog_session
    seed_copy(baseline)
    service = CanvasService(session)
    commit(service, document(session))
    session.info["actor"] = actor(2)
    with session.begin():
        shared = service._document(session.get(ProjectCanvas, CANVAS), private=False)
    with baseline.begin():
        membership = baseline.scalar(
            select(ProjectMember).where(ProjectMember.project_id == SOURCE)
        )
        membership.status = "removed"
    before = state(baseline)
    with pytest.raises(NotFound):
        commit(service, shared, expected=8, key="revoked-shared-save")
    assert state(baseline) == before


@pytest.mark.parametrize("field,value", [("row_version", 8), ("source_project_id", None)])
def test_batch_lock_rejects_stale_preflight_canvas_and_binding(catalog_session, field, value):
    from short_drama.service.canvas_character_publication_service import (
        CanvasCharacterPublicationService,
    )

    session, baseline = catalog_session
    service = CanvasService(session)
    with session.begin():
        original = service.canvas_dao.canvas(OWNING, CANVAS)
    with baseline.begin():
        setattr(baseline.get(ProjectCanvas, CANVAS), field, value)
    with pytest.raises(WorkflowError) as caught, session.begin():
        CanvasCharacterPublicationService(service).lock_existing_canvases([original])
    assert caught.value.code == "canvas_binding_changed"


def test_generation_action_entry_rechecks_sorted_scopes_before_invalid_action(catalog_session):
    from types import SimpleNamespace

    from short_drama.core.exceptions import Conflict
    from short_drama.service.canvas_generation_service import CanvasGenerationService

    session, baseline = catalog_session
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    reverse_commit(CanvasService(session), reverse_document(session))
    seed_task(baseline)
    session.info["request_project"] = REVERSE_OWNING
    locks = trace_project_locks(session)
    with pytest.raises(Conflict):
        CanvasGenerationService(session, SimpleNamespace()).action(100, "unsupported")
    assert_sorted_first_locks(locks)


def test_batch_lock_requires_current_trusted_actor_even_when_no_canvas_selected(catalog_session):
    from short_drama.service.canvas_character_publication_service import (
        CanvasCharacterPublicationService,
    )

    session, _baseline = catalog_session
    session.info.pop("actor")
    with pytest.raises(WorkflowError) as caught, session.begin():
        CanvasCharacterPublicationService(CanvasService(session)).lock_existing_canvases([])
    assert caught.value.code == "authentication_required"


def retry_payload(task_id, *, node_id="character-one", operation="retry-boundary-fixture"):
    from short_drama.schemas.canvas_task_runtime import CanvasRuntimeTaskCreate

    return CanvasRuntimeTaskCreate.model_validate(
        {
            "projectId": KEY,
            "type": "canvas_text",
            "operation": "generate",
            "prompt": "fixture",
            "logicalModelId": "999",
            "input": {
                "mode": "text",
                "prompt": "fixture",
                "metadata": {
                    "nodeId": node_id,
                    "clientOperationId": operation,
                    "retryOf": str(task_id),
                },
            },
        }
    )


def retry_lock_trace(session):
    projects, tasks = [], []

    def inspect_lock(execute_state):
        statement = execute_state.statement
        if getattr(statement, "_for_update_arg", None) is None:
            return
        entities = {item.get("entity") for item in getattr(statement, "column_descriptions", [])}
        values = statement.compile().params.values()
        if Project in entities:
            projects.extend(value for value in values if value in {OWNING, SOURCE, REVERSE_OWNING})
        if AsyncTask in entities:
            tasks.extend(value for value in values if value in {100, 200})

    event.listen(session, "do_orm_execute", inspect_lock)
    return projects, tasks


def prepare_retry_canvas(session, baseline, *, extra_node=False):
    reverse_owning(baseline)
    seed_copy(baseline, project_id=REVERSE_OWNING)
    source = reverse_document(session)
    if extra_node:
        extra = deepcopy(source["nodes"][0])
        extra["id"] = "character-two"
        source["nodes"].append(extra)
    reverse_commit(CanvasService(session), source)
    session.info["request_project"] = REVERSE_OWNING


def test_cross_canvas_retry_rejection_does_not_acquire_projects_out_of_global_order(
    catalog_session,
):
    from types import SimpleNamespace

    from short_drama.service.canvas_generation_service import CanvasGenerationService

    session, baseline = catalog_session
    prepare_retry_canvas(session, baseline)
    now = utcnow()
    old_canvas = CANVAS + 200
    with baseline.begin():
        baseline.add(
            ProjectCanvas(
                id=old_canvas,
                project_id=OWNING,
                source_project_id=None,
                source_key="older-task-canvas",
                title="older",
                position=1,
                schema_version=1,
                row_version=1,
                properties_json={},
                created_by=1,
                updated_by=1,
                created_at=now,
                updated_at=now,
            )
        )
    seed_task(baseline, task_id=200, canvas_id=old_canvas, owning=OWNING)
    projects, tasks = retry_lock_trace(session)
    before = state(baseline)
    with pytest.raises(WorkflowError) as caught:
        CanvasGenerationService(session, SimpleNamespace()).create(retry_payload(200))
    assert caught.value.code == "canvas_task_retry_not_allowed"
    assert list(dict.fromkeys(projects)) == [SOURCE, REVERSE_OWNING]
    assert tasks == []
    assert state(baseline) == before


def test_same_canvas_wrong_node_retry_is_rejected_before_task_lock(catalog_session):
    from types import SimpleNamespace

    from short_drama.service.canvas_generation_service import CanvasGenerationService

    session, baseline = catalog_session
    prepare_retry_canvas(session, baseline, extra_node=True)
    seed_task(baseline)
    projects, tasks = retry_lock_trace(session)
    before = state(baseline)
    with pytest.raises(WorkflowError) as caught:
        CanvasGenerationService(session, SimpleNamespace()).create(
            retry_payload(100, node_id="character-two")
        )
    assert caught.value.code == "canvas_task_retry_not_allowed"
    assert list(dict.fromkeys(projects)) == [SOURCE, REVERSE_OWNING]
    assert tasks == []
    assert state(baseline) == before


@pytest.mark.parametrize("requester", [1, 2])
def test_retry_missing_or_other_author_binding_keeps_private_task_inaccessible(
    catalog_session, requester
):
    from types import SimpleNamespace

    from short_drama.service.canvas_generation_service import CanvasGenerationService

    session, baseline = catalog_session
    prepare_retry_canvas(session, baseline)
    if requester == 2:
        seed_task(baseline, task_id=200)
        with baseline.begin():
            baseline.add(
                ProjectMember(
                    id=REVERSE_OWNING + 10,
                    project_id=REVERSE_OWNING,
                    user_id=2,
                    status="active",
                    joined_at=utcnow(),
                )
            )
        session.info["actor"] = actor(2)
    projects, tasks = retry_lock_trace(session)
    before = state(baseline)
    with pytest.raises(NotFound):
        CanvasGenerationService(session, SimpleNamespace()).create(retry_payload(200))
    assert list(dict.fromkeys(projects)) == [SOURCE, REVERSE_OWNING]
    assert tasks == []
    assert state(baseline) == before


@pytest.mark.parametrize(
    "task_status,record_status,allowed",
    [
        ("failed", "failed", True),
        ("cancelled", "failed", True),
        ("succeeded", "succeeded", False),
        ("failed", "unknown", False),
    ],
)
def test_same_canvas_retry_preserves_real_permission_and_unknown_boundary(
    catalog_session, monkeypatch, task_status, record_status, allowed
):
    from types import SimpleNamespace

    from short_drama.service.canvas_generation_service import CanvasGenerationService

    session, baseline = catalog_session
    prepare_retry_canvas(session, baseline)
    seed_task(baseline)
    with baseline.begin():
        baseline.get(AsyncTask, 100).status = task_status
        record = baseline.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == 100)
        )
        record.status = record_status
    generation = CanvasGenerationService(session, SimpleNamespace())
    configuration = []

    def missing_config(kind, identifier):
        configuration.append((kind, identifier))
        raise NotFound("Fixture model is absent after retry permission check")

    monkeypatch.setattr(generation.generation, "_config", missing_config)
    projects, tasks = retry_lock_trace(session)
    before = state(baseline)
    with pytest.raises(NotFound if allowed else WorkflowError) as caught:
        generation.create(retry_payload(100))
    if allowed:
        assert configuration == [("text", 999)]
    else:
        assert caught.value.code == "canvas_task_retry_not_allowed"
        assert configuration == []
    assert list(dict.fromkeys(projects)) == [SOURCE, REVERSE_OWNING]
    assert tasks and set(tasks) == {100}
    assert state(baseline) == before
