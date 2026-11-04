"""Real MySQL business receipts share account keys without creating assistant runs."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib import import_module, util
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.domain import Asset
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.service.projects.management.project import ProjectService
from tests.integration.standard.assets.test_asset_image_generation import flow as flow

pytestmark = pytest.mark.integration


def invocation_api():
    assert util.find_spec("short_drama.service.assistant.operations.invocation") is not None, (
        "Task-independent business invocation boundary is missing"
    )
    return import_module("short_drama.service.assistant.operations.invocation")


def business_identity(
    api,
    factory,
    actor,
    project_id,
    request_id="create-asset",
    token_id=None,
    policy="project",
    allowed=None,
):
    return api.BusinessInvocation(
        actor=actor,
        source="mcp",
        request_id=request_id,
        token_id=token_id if token_id is not None else issued_mcp_token(factory, actor),
        scope=api.InvocationScope(
            project_ids=frozenset({project_id}),
            tool_names=frozenset(
                {"workbench__apply_standard_patch", "workbench__read_current_script"}
            ),
            subject_policy=policy,
            allowed_subjects=allowed or {},
        ),
    )


def issued_mcp_token(factory, actor):
    from short_drama.agent.tools.registry import TOOL_SPECS
    from short_drama.schemas.identity.accounts.mcp_token import McpTokenCreate
    from short_drama.service.identity.accounts.mcp_token import McpTokenService

    with factory() as session:
        session.info["actor"] = actor
        return (
            McpTokenService(session, tool_specs=TOOL_SPECS)
            .create(
                McpTokenCreate(
                    name="业务幂等令牌",
                    workbench_enabled=True,
                    media_generation_enabled=True,
                ),
                uuid4().hex,
            )
            .id
        )


def test_same_account_receipt_replays_across_tokens_and_changed_arguments_conflict(db_session):
    api = invocation_api()
    actor = db_session.info["actor"]
    project = ProjectService(db_session).create_project({"name": "Business receipt"})
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    first_token = issued_mcp_token(factory, actor)
    second_token = issued_mcp_token(factory, actor)
    schema = import_module("short_drama.schemas.assistant.operations.standard")
    handler = import_module(
        "short_drama.service.assistant.operations.standard"
    ).apply_standard_patch_locked
    payload = schema.ApplyStandardPatch(
        project_id=str(project.id),
        patch={
            "actions": [
                {
                    "action": "create_asset",
                    "key": "hero",
                    "values": {"name": "主角", "kind": "character"},
                }
            ]
        },
    )
    with factory() as session:
        result = api.BusinessInvocationService(session).execute(
            business_identity(api, factory, actor, project.id, token_id=first_token),
            "workbench__apply_standard_patch",
            payload,
            handler,
            mutating=True,
        )
    with factory() as session:
        replay = api.BusinessInvocationService(session).execute(
            business_identity(api, factory, actor, project.id, token_id=second_token),
            "workbench__apply_standard_patch",
            payload,
            handler,
            mutating=True,
        )
        assert replay == result
    changed = payload.model_copy(
        update={
            "patch": schema.StandardPatch(
                actions=[
                    {
                        "action": "create_asset",
                        "key": "hero",
                        "values": {"name": "另一个主角", "kind": "character"},
                    }
                ]
            )
        }
    )
    with factory() as session, pytest.raises(BusinessError) as raised:
        api.BusinessInvocationService(session).execute(
            business_identity(api, factory, actor, project.id, token_id=first_token),
            "workbench__apply_standard_patch",
            changed,
            handler,
            mutating=True,
        )
    assert raised.value.status_code == 409
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        for model in (AgentConversation, AgentTask, AgentRun, AgentOperation):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_scope_rejects_unselected_subjects_and_other_projects(db_session):
    api = invocation_api()
    actor = db_session.info["actor"]
    first = ProjectService(db_session).create_project({"name": "Allowed"})
    other = ProjectService(db_session).create_project({"name": "Outside"})
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    schema = import_module("short_drama.schemas.assistant.operations.standard")
    handler = import_module(
        "short_drama.service.assistant.operations.standard"
    ).apply_standard_patch_locked
    for project_id in (other.id, first.id):
        payload = schema.ApplyStandardPatch(
            project_id=str(project_id),
            patch={
                "actions": [
                    {
                        "action": "update_asset",
                        "asset": "100",
                        "patch": {"name": "越界", "row_version": "1"},
                    }
                ]
            },
        )
        with factory() as session, pytest.raises(WorkflowError) as raised:
            api.BusinessInvocationService(session).execute(
                business_identity(api, factory, actor, first.id, policy="selected"),
                "workbench__apply_standard_patch",
                payload,
                handler,
                mutating=True,
            )
        assert raised.value.status_code in {403, 422}


def test_concurrent_tokens_share_one_real_write_and_failed_patch_rolls_back(db_session):
    from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
    from short_drama.schemas.assistant.operations.standard import ApplyStandardPatch
    from short_drama.service.assistant.operations.standard import apply_standard_patch_locked

    api = invocation_api()
    actor = db_session.info["actor"]
    project = ProjectService(db_session).create_project({"name": "Concurrent"})
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    values = ApplyStandardPatch(
        project_id=project.id,
        patch={
            "actions": [
                {
                    "action": "create_asset",
                    "key": "one",
                    "values": {"name": "仅一个", "kind": "character"},
                }
            ]
        },
    )
    barrier = Barrier(2)

    def invoke(token):
        barrier.wait(timeout=10)
        with factory() as session:
            return api.BusinessInvocationService(session).execute(
                business_identity(api, factory, actor, project.id, token_id=token),
                "workbench__apply_standard_patch",
                values,
                apply_standard_patch_locked,
                mutating=True,
            )

    tokens = [issued_mcp_token(factory, actor) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, tokens))
    assert results[0] == results[1]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        assert session.scalar(select(func.count()).select_from(BusinessToolInvocation)) == 1
    invalid = ApplyStandardPatch(
        project_id=project.id,
        patch={
            "actions": [
                {
                    "action": "create_asset",
                    "key": "rollback",
                    "values": {"name": "须回滚", "kind": "character"},
                },
                {
                    "action": "update_asset",
                    "asset": "99999",
                    "patch": {"name": "无权限", "row_version": "1"},
                },
            ]
        },
    )
    with factory() as session, pytest.raises(BusinessError):
        api.BusinessInvocationService(session).execute(
            business_identity(api, factory, actor, project.id, request_id="rollback"),
            "workbench__apply_standard_patch",
            invalid,
            apply_standard_patch_locked,
            mutating=True,
        )
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        assert session.scalar(select(func.count()).select_from(BusinessToolInvocation)) == 1


def test_different_accounts_concurrent_first_receipts_do_not_lock_empty_index_gap(
    db_session, monkeypatch
):
    from short_drama.core.time import utcnow
    from short_drama.dao.assistant.operations.invocation import BusinessInvocationDAO
    from short_drama.domain import User
    from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
    from short_drama.schemas.assistant.operations.standard import ApplyStandardPatch
    from short_drama.service.assistant.operations.standard import apply_standard_patch_locked
    from tests.support.integration.identity import actor

    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    first = ProjectService(db_session).create_project({"name": "账号一"})
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="other-writer",
                email="other-writer@example.test",
                display_name="Other",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    with factory() as session:
        session.info["actor"] = actor(2)
        second = ProjectService(session).create_project({"name": "账号二"})
    barrier = Barrier(2)
    original = BusinessInvocationDAO.receipt

    def overlap(dao, *args):
        receipt = original(dao, *args)
        barrier.wait(timeout=10)
        return receipt

    monkeypatch.setattr(BusinessInvocationDAO, "receipt", overlap)
    api = invocation_api()

    def write(pair):
        owner, project = pair
        with factory() as session:
            return api.BusinessInvocationService(session).execute(
                business_identity(
                    api, factory, actor(owner), project.id, request_id="same-request"
                ),
                "workbench__apply_standard_patch",
                ApplyStandardPatch(
                    project_id=project.id,
                    patch={
                        "actions": [
                            {
                                "action": "create_asset",
                                "key": "hero",
                                "values": {"name": "独立主角", "kind": "character"},
                            }
                        ]
                    },
                ),
                apply_standard_patch_locked,
                mutating=True,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [(1, first), (2, second)]))
    assert len({result["business_invocation_id"] for result in results}) == 2
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 2
        assert session.scalar(select(func.count()).select_from(BusinessToolInvocation)) == 2


def test_locked_invocation_replay_uses_current_receipt_after_an_earlier_consistent_read(db_session):
    from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
    from short_drama.schemas.assistant.operations.standard import ApplyStandardPatch
    from short_drama.service.assistant.operations.standard import apply_standard_patch_locked

    api = invocation_api()
    identity_actor = db_session.info["actor"]
    project = ProjectService(db_session).create_project({"name": "已有读取视图"})
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    values = ApplyStandardPatch(
        project_id=project.id,
        patch={
            "actions": [
                {"action": "create_asset", "key": "one", "values": {"name": "唯一", "kind": "prop"}}
            ]
        },
    )
    identity = business_identity(
        api, factory, identity_actor, project.id, request_id="current-read-replay"
    )
    with factory.begin() as second:
        # 正式 invoke_business_tool 在锁账号前已通过 execution_locked 读取 Task/Run。
        assert second.scalar(select(func.count()).select_from(BusinessToolInvocation)) == 0
        with factory() as first:
            original = api.BusinessInvocationService(first).execute(
                identity,
                "workbench__apply_standard_patch",
                values,
                apply_standard_patch_locked,
                mutating=True,
            )
        replay = api.BusinessInvocationService(second).execute_locked(
            identity,
            "workbench__apply_standard_patch",
            values,
            apply_standard_patch_locked,
            mutating=True,
        )
        assert replay == original
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1


def test_read_scope_filters_project_rows_and_counts_and_refreshes_account_state(db_session):
    from short_drama.domain import Project
    from short_drama.domain.identity.accounts.user import User
    from short_drama.schemas.assistant.operations.script import ReadCurrentScript
    from short_drama.service.assistant.operations.script_read import read_current_script_locked

    api = invocation_api()
    actor = db_session.info["actor"]
    project = ProjectService(db_session).create_project({"name": "Allowed read"})
    ProjectService(db_session).create_project({"name": "Other read"})
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    values = ReadCurrentScript(project_id=project.id, script_id=None, content_version=1)
    identity = business_identity(api, factory, actor, project.id)

    def list_projects(session, identity, values, receipt_id, settings):
        return {
            "ids": list(session.scalars(select(Project.id))),
            "total": session.scalar(select(func.count()).select_from(Project)),
        }

    with factory() as session:
        result = api.BusinessInvocationService(session).execute(
            identity,
            "workbench__read_current_script",
            values,
            list_projects,
            mutating=False,
        )
    assert result == {"ids": [project.id], "total": 1}
    with factory() as session:
        result = api.BusinessInvocationService(session).execute(
            identity,
            "workbench__read_current_script",
            values,
            read_current_script_locked,
            mutating=False,
        )
    assert result["content"] == ""
    with factory.begin() as session:
        user = session.get(User, actor.user_id)
        user.email_verified_at = None
    with factory() as session, pytest.raises(WorkflowError) as raised:
        api.BusinessInvocationService(session).execute(
            identity,
            "workbench__read_current_script",
            values,
            read_current_script_locked,
            mutating=False,
        )
    assert raised.value.status_code == 401


def test_existing_unselected_and_foreign_account_objects_are_rejected(db_session):
    from short_drama.core.identity import ActorContext
    from short_drama.core.time import utcnow
    from short_drama.domain.identity.accounts.user import User
    from short_drama.schemas.assistant.operations.standard import ApplyStandardPatch
    from short_drama.service.assistant.operations.standard import apply_standard_patch_locked

    api = invocation_api()
    actor = db_session.info["actor"]
    project = ProjectService(db_session).create_project({"name": "Existing selected scope"})
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    create = ApplyStandardPatch(
        project_id=project.id,
        patch={
            "actions": [
                {
                    "action": "create_asset",
                    "key": "one",
                    "values": {"name": "未选素材", "kind": "character"},
                }
            ]
        },
    )
    with factory() as session:
        result = api.BusinessInvocationService(session).execute(
            business_identity(api, factory, actor, project.id),
            "workbench__apply_standard_patch",
            create,
            apply_standard_patch_locked,
            mutating=True,
        )
    asset_id = next(ref["id"] for ref in result["result_refs"] if ref["kind"] == "asset")
    update = ApplyStandardPatch(
        project_id=project.id,
        patch={
            "actions": [
                {
                    "action": "update_asset",
                    "asset": asset_id,
                    "patch": {"name": "不得修改", "row_version": "1"},
                }
            ]
        },
    )
    with factory() as session, pytest.raises(BusinessError):
        api.BusinessInvocationService(session).execute(
            business_identity(
                api, factory, actor, project.id, request_id="unselected", policy="selected"
            ),
            "workbench__apply_standard_patch",
            update,
            apply_standard_patch_locked,
            mutating=True,
        )
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="foreign",
                display_name="Foreign",
                email="foreign@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    foreign = ActorContext(
        user_id=2,
        username="foreign",
        email="foreign@example.test",
        verified=True,
        request_id="foreign",
    )
    with factory() as session, pytest.raises(BusinessError):
        api.BusinessInvocationService(session).execute(
            replace(business_identity(api, factory, actor, project.id), actor=foreign),
            "workbench__apply_standard_patch",
            create,
            apply_standard_patch_locked,
            mutating=True,
        )


def test_selected_image_slot_reads_real_bytes_and_adopts_only_its_frozen_slot(flow):
    import json
    from contextlib import contextmanager
    from io import BytesIO
    from types import SimpleNamespace

    from short_drama.agent.context.business_scope import selected_scope_subjects
    from short_drama.agent.tools.registry import TOOL_SPECS
    from short_drama.dao.assistant.context.context import AssistantContextDAO
    from short_drama.schemas.assistant.operations.image import (
        ReadImageContent,
        ReadImageGroup,
        SetImageGroupPrimary,
    )
    from short_drama.schemas.assistant.tasks.task import TaskSubjectReference
    from short_drama.service.assistant.context.subject import freeze_subject
    from short_drama.service.assistant.operations.image import (
        read_business_image_bytes,
        read_image_content_business_locked,
        read_image_group_business_locked,
        set_image_group_primary_business_locked,
    )
    from short_drama.service.media.image_groups.groups import ImageGroupService
    from short_drama.storage.models import ObjectLocation
    from tests.integration.standard.assets.test_asset_image_generation import (
        create_asset,
        drain,
        submit,
    )

    api = invocation_api()
    actor = flow.session.info["actor"]
    asset = create_asset(flow)
    assert drain(flow, submit(flow, asset, 2)) == "succeeded"
    current = ImageGroupService(flow.session).current("asset", asset.id)
    slot = current["items"][1]
    with flow.factory.begin() as session:
        session.info["actor"] = actor
        subject = freeze_subject(
            AssistantContextDAO(session),
            actor.user_id,
            TaskSubjectReference(
                project_id=flow.project.id,
                source_kind="project",
                source_id=str(flow.project.id),
                kind="image_slot",
                id=slot["id"],
                revision=slot["item_version"],
            ),
        )
    allowed, catalogs, snapshots = selected_scope_subjects([subject], str(flow.project.id), [])
    assert not allowed["asset"]
    identity = api.BusinessInvocation(
        actor=actor,
        source="mcp",
        request_id="adopt-slot",
        token_id=issued_mcp_token(flow.factory, actor),
        scope=api.InvocationScope(
            frozenset({flow.project.id}),
            frozenset(spec.name for spec in TOOL_SPECS if spec.expose_mcp),
            "selected",
            allowed,
            image_catalogs=catalogs,
            subject_snapshots=snapshots,
        ),
    )
    target = {
        "project_id": str(flow.project.id),
        "target_kind": "asset",
        "target_id": str(asset.id),
    }
    with flow.factory() as session:
        result = api.BusinessInvocationService(session).execute(
            identity,
            "workbench__read_image_group",
            ReadImageGroup(**target),
            read_image_group_business_locked,
            mutating=False,
        )
    visible = json.loads(result["content"])["groups"]
    assert len(visible) == 1 and [item["id"] for item in visible[0]["items"]] == [slot["id"]]
    content = ReadImageContent(
        **target,
        group_id=current["id"],
        item_id=slot["id"],
        media_id=slot["media_id"],
        purpose="核对第二图位",
    )
    unselected = current["items"][0]
    with flow.factory() as session, pytest.raises(BusinessError):
        api.BusinessInvocationService(session).execute(
            identity,
            "workbench__read_image_content",
            ReadImageContent(
                **target,
                group_id=current["id"],
                item_id=unselected["id"],
                media_id=unselected["media_id"],
                purpose="越界读取",
            ),
            read_image_content_business_locked,
            mutating=False,
        )

    @contextmanager
    def open_image(bucket, key):
        assert not flow.session.in_transaction()
        yield BytesIO(flow.storage.objects[bucket, key][0])

    storage = SimpleNamespace(settings=flow.settings, open=open_image)
    data, mime = read_business_image_bytes(flow.factory, identity, content, storage)
    assert mime == "image/png" and data.startswith(b"\x89PNG")
    with flow.factory() as session:
        result = api.BusinessInvocationService(session).execute(
            identity,
            "workbench__set_image_group_primary",
            SetImageGroupPrimary(
                **target,
                group_id=current["id"],
                item_id=slot["id"],
                expected_primary_version=current["primary_version"],
                expected_item_version=slot["item_version"],
                expected_target_version=current["target_version"],
            ),
            set_image_group_primary_business_locked,
            mutating=True,
        )
    assert result["adopted"] is True
    assert ImageGroupService(flow.session).current("asset", asset.id)["primary_index"] == 2
    with flow.factory() as session:
        from short_drama.domain import MediaFile

        location = ObjectLocation.parse(
            session.get(MediaFile, int(slot["media_id"])).storage_locator,
            {flow.settings.minio_image_bucket},
        )
    flow.storage.objects[location.bucket, location.object_name] = (b"corrupt", mime)
    with pytest.raises(BusinessError):
        read_business_image_bytes(flow.factory, identity, content, storage)


def test_business_media_admission_creates_native_tasks_and_status_without_assistant_records(flow):
    from short_drama.agent.tools.registry import TOOL_SPECS
    from short_drama.domain import AsyncTask
    from short_drama.domain.assistant.operations.media import AgentMediaRequest
    from short_drama.schemas.assistant.operations.media import (
        GetGenerationStatus,
        SubmitGenerationBatch,
    )
    from short_drama.service.assistant.operations.media import (
        get_generation_status_locked,
        submit_generation_batch_locked,
    )
    from tests.integration.standard.assets.test_asset_image_generation import create_asset, drain

    api = invocation_api()
    actor = flow.session.info["actor"]
    asset = create_asset(flow)
    identity = replace(
        business_identity(api, flow.factory, actor, flow.project.id),
        scope=api.InvocationScope(
            frozenset({flow.project.id}),
            frozenset(spec.name for spec in TOOL_SPECS if spec.expose_mcp),
            "project",
            {},
        ),
    )
    values = SubmitGenerationBatch(
        project_id=flow.project.id,
        requests=[
            {
                "target_kind": "asset",
                "target_id": str(asset.id),
                "row_version": str(asset.row_version),
                "model_config_id": str(flow.config.id),
                "model_version": str(flow.config.row_version),
                "prompt": "明确的模型提示词",
                "count": 2,
            }
        ],
    )
    with flow.factory() as session:
        result = api.BusinessInvocationService(session, flow.settings).execute(
            identity,
            "workbench__submit_generation_batch",
            values,
            submit_generation_batch_locked,
            mutating=True,
        )
    assert result["admitted"] and not result["settled"] and not flow.provider.calls
    generation_id = int(result["items"][0]["generation_id"])
    with flow.factory() as session:
        replay = api.BusinessInvocationService(session, flow.settings).execute(
            replace(
                identity,
                source="mcp",
                token_id=issued_mcp_token(flow.factory, actor),
            ),
            "workbench__submit_generation_batch",
            values,
            submit_generation_batch_locked,
            mutating=True,
        )
        assert replay == result
    status = GetGenerationStatus(project_id=flow.project.id, generation_ids=[generation_id])
    with flow.factory() as session:
        pending = api.BusinessInvocationService(session).execute(
            identity,
            "workbench__get_generation_status",
            status,
            get_generation_status_locked,
            mutating=False,
        )
    assert not pending["settled"]
    assert drain(flow, generation_id) == "succeeded"
    with flow.factory() as session:
        ready = api.BusinessInvocationService(session).execute(
            identity,
            "workbench__get_generation_status",
            status,
            get_generation_status_locked,
            mutating=False,
        )
        assert session.scalar(select(func.count()).select_from(AsyncTask)) == 1
        for model in (AgentConversation, AgentTask, AgentRun, AgentOperation, AgentMediaRequest):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert ready["settled"] and len(ready["items"][0]["result_refs"]) == 2
    assert len(flow.provider.calls) == 1


def test_project_media_without_references_does_not_scan_all_subject_image_catalogs(
    flow, monkeypatch
):
    from short_drama.schemas.assistant.operations.media import SubmitGenerationBatch
    from short_drama.service.assistant.operations import image
    from short_drama.service.assistant.operations.media import submit_generation_batch_locked
    from tests.integration.standard.assets.test_asset_image_generation import create_asset

    asset = create_asset(flow)
    create_asset(flow, kind="scene")
    calls = []
    original = image.freeze_image_catalog

    def observed(*args, **kwargs):
        calls.append(args[3:5])
        return original(*args, **kwargs)

    monkeypatch.setattr(image, "freeze_image_catalog", observed)
    api = invocation_api()
    identity = replace(
        business_identity(api, flow.factory, flow.session.info["actor"], flow.project.id),
        scope=api.InvocationScope(
            frozenset({flow.project.id}),
            frozenset({"workbench__submit_generation_batch"}),
            "project",
            {},
        ),
    )
    values = SubmitGenerationBatch(
        project_id=flow.project.id,
        requests=[
            {
                "target_kind": "asset",
                "target_id": str(asset.id),
                "row_version": str(asset.row_version),
                "model_config_id": str(flow.config.id),
                "model_version": str(flow.config.row_version),
                "prompt": "only target",
                "count": 1,
            }
        ],
    )
    with flow.factory() as session:
        result = api.BusinessInvocationService(session, flow.settings).execute(
            identity,
            "workbench__submit_generation_batch",
            values,
            submit_generation_batch_locked,
            mutating=True,
        )
    assert result["admitted"] and len(result["items"]) == 1 and not flow.provider.calls
    assert calls == [], "project-scoped admission enumerated unrelated image catalogs"


def scope_operation(factory, accepted, scope_id, name, arguments=None, *, input_revision=1):
    """Persist the graph's call registration boundary without invoking any model."""
    from short_drama.domain.assistant.execution.models import AgentToolCall, AgentTurn
    from short_drama.utils.snowflake import next_id

    with factory.begin() as session:
        turn = AgentTurn(
            id=next_id(),
            run_id=accepted.run.id,
            scope_id=scope_id,
            turn_no=session.scalar(select(func.count()).select_from(AgentTurn)) + 1,
            request_messages=[],
            usage={},
        )
        session.add(turn)
        session.flush()
        call = AgentToolCall(
            id=next_id(),
            run_id=accepted.run.id,
            scope_id=scope_id,
            turn_id=turn.id,
            call_index=1,
            provider_call_id=f"call-{next_id()}",
            tool_name=name,
            arguments=arguments or {},
            arguments_hash="a" * 64,
            idempotency_key=f"{next_id():064d}",
        )
        session.add(call)
        session.flush()
        operation = AgentOperation(
            id=next_id(),
            task_id=accepted.task.id,
            run_id=accepted.run.id,
            scope_id=scope_id,
            tool_call_id=call.id,
            tool_name=name,
            plan_revision=1,
            input_revision=input_revision,
            payload_hash="a" * 64,
            arguments=arguments or {},
            result_refs=[],
            changes=[],
        )
        session.add(operation)
        return operation.id


def test_generated_images_follow_scope_lineage_without_sibling_or_later_media_access(flow):
    import json
    from uuid import uuid4

    from short_drama.agent.context.business_scope import assistant_invocation_locked
    from short_drama.agent.task.state import apply_inputs_locked
    from short_drama.agent.tools.resources.describe_creation_target import (
        invoke_locked as describe_target,
    )
    from short_drama.agent.tools.resources.read_operation_receipt import (
        invoke_locked as read_receipt,
    )
    from short_drama.agent.tools.results import read_results_locked
    from short_drama.domain import AgentToolCall, ImageGroupItem
    from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
    from short_drama.schemas.assistant.operations.image import ReadImageContent, ReadImageGroup
    from short_drama.schemas.assistant.operations.media import (
        GetGenerationStatus,
        SubmitGenerationBatch,
    )
    from short_drama.schemas.assistant.operations.standard import ApplyStandardPatch
    from short_drama.schemas.assistant.operations.tool_inputs import (
        DescribeTarget,
        ReadReceipt,
        ReadTaskResults,
    )
    from short_drama.service.assistant.execution.scope import AgentExecutionScopeService
    from short_drama.service.assistant.operations.image import (
        read_image_content_business_locked,
        read_image_group_business_locked,
    )
    from short_drama.service.assistant.operations.media import (
        get_generation_status_locked,
        submit_generation_batch_locked,
    )
    from short_drama.service.assistant.operations.standard import apply_standard_patch_locked
    from tests.integration.standard.assets.test_asset_image_generation import create_asset, drain
    from tests.integration.standard.assistant.test_global_assistant_tasks import (
        _workspace,
        task_service,
    )
    from tests.integration.standard.assistant.test_graph_checkpointer import bound

    api = invocation_api()
    asset = create_asset(flow)
    workspace = _workspace(flow.session)
    factory, settings, model = workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "为已选素材生成图片并核对成果",
                "model_config_id": model,
                "execution_target": {"mode": "standard", "project_id": str(flow.project.id)},
                "subject_selection": {
                    "expected_input_revision": "0",
                    "subjects": [
                        {
                            "project_id": str(flow.project.id),
                            "source_kind": "project",
                            "source_id": str(flow.project.id),
                            "kind": "asset",
                            "id": str(asset.id),
                            "revision": str(asset.row_version),
                        }
                    ],
                },
            },
            uuid4().hex,
        )
    with factory.begin() as session:
        apply_inputs_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
            session.get(AgentRun, accepted.run.id),
        )
    graph = bound(workspace, accepted)
    scopes = AgentExecutionScopeService(graph)
    root = scopes.root()

    def identity(session, operation_id):
        return assistant_invocation_locked(
            session,
            session.get(AgentConversation, conversation.id),
            session.get(AgentTask, accepted.task.id),
            session.get(AgentRun, accepted.run.id),
            session.get(AgentOperation, operation_id),
        )

    submitted = []
    to_submit = [root]
    for scope in to_submit:
        operation_id = scope_operation(
            factory, accepted, scope.id, "workbench__submit_generation_batch"
        )
        with factory.begin() as session:
            invocation = identity(session, operation_id)
            values = SubmitGenerationBatch(
                project_id=flow.project.id,
                requests=[
                    {
                        "target_kind": "asset",
                        "target_id": str(asset.id),
                        "row_version": invocation.scope.subject_snapshots[f"asset:{asset.id}"][
                            "revision"
                        ],
                        "model_config_id": str(flow.config.id),
                        "model_version": str(flow.config.row_version),
                        "prompt": "明确的模型提示词",
                        "count": 1,
                    }
                ],
            )
            result = api.BusinessInvocationService(session, flow.settings).execute_locked(
                invocation,
                "workbench__submit_generation_batch",
                values,
                submit_generation_batch_locked,
                mutating=True,
            )
            assert "invocation_scope" not in session.info
            operation = session.get(AgentOperation, operation_id)
            operation.result, operation.status = result, "succeeded"
            operation.business_invocation_id = int(result["business_invocation_id"])
            receipt = session.get(BusinessToolInvocation, operation.business_invocation_id)
            operation.changes, operation.result_refs = receipt.changes, receipt.result_refs
            from short_drama.core.time import utcnow

            operation.finished_at = utcnow()
        generation_id = int(result["items"][0]["generation_id"])
        assert drain(flow, generation_id) == "succeeded"
        submitted.append(generation_id)
        if scope.id == root.id:

            def delegated_scope(name, parent_generation_id=generation_id):
                handoff = {
                    "subjects": [f"asset:{asset.id}"],
                    "generation_ids": [str(parent_generation_id)],
                }
                delegated = scope_operation(
                    factory,
                    accepted,
                    root.id,
                    "task",
                    {
                        "parsed": {
                            "description": name
                            + "\n```workbench-inputs\n"
                            + json.dumps(handoff)
                            + "\n```",
                            "subagent_type": "workbench-task",
                        }
                    },
                )
                with factory() as session:
                    operation = session.get(AgentOperation, delegated)
                    provider_id = session.get(
                        AgentToolCall, operation.tool_call_id
                    ).provider_call_id
                return scopes.get(
                    scopes.for_delegation(
                        accepted.run.id, root.id, provider_id, fence=graph.fence, memory_epoch=0
                    )
                )

            left, right = delegated_scope("left"), delegated_scope("right")
            to_submit.append(left)

    target = {
        "project_id": str(flow.project.id),
        "target_kind": "asset",
        "target_id": str(asset.id),
    }
    readers = {}
    for scope, expected in ((root, submitted[:1]), (left, submitted), (right, submitted[:1])):
        operation_id = scope_operation(factory, accepted, scope.id, "workbench__read_image_group")
        with factory.begin() as session:
            invocation = identity(session, operation_id)
            assert invocation.scope.generation_ids == frozenset(expected)
            result = api.BusinessInvocationService(session).execute_locked(
                invocation,
                "workbench__read_image_group",
                ReadImageGroup(**target, view="history"),
                read_image_group_business_locked,
                mutating=False,
            )
            readers[scope.id] = invocation
        groups = json.loads(result["content"])["groups"]
        assert len(groups) == len(expected)
        if scope.id == left.id:
            produced = groups
    with factory() as session, pytest.raises(BusinessError):
        api.BusinessInvocationService(session).execute(
            replace(
                readers[right.id],
                scope=replace(
                    readers[right.id].scope,
                    tool_names=frozenset({"workbench__get_generation_status"}),
                ),
            ),
            "workbench__get_generation_status",
            GetGenerationStatus(project_id=flow.project.id, generation_ids=[submitted[1]]),
            get_generation_status_locked,
            mutating=False,
        )
    first, second = sorted(produced, key=lambda item: int(item["id"]))
    first_item, second_item = first["items"][0], second["items"][0]
    writes, saved_operations = [], []
    for name in ("本范围第一次保存", "本范围第二次保存"):
        editor = scope_operation(factory, accepted, left.id, "workbench__apply_standard_patch")
        with factory.begin() as session:
            invocation = identity(session, editor)
            version = invocation.scope.subject_snapshots[f"asset:{asset.id}"]["revision"]
            values = ApplyStandardPatch(
                project_id=flow.project.id,
                patch={
                    "actions": [
                        {
                            "action": "update_asset",
                            "asset": str(asset.id),
                            "patch": {"name": name, "row_version": version},
                        }
                    ]
                },
            )
            result = api.BusinessInvocationService(session).execute_locked(
                invocation,
                "workbench__apply_standard_patch",
                values,
                apply_standard_patch_locked,
                mutating=True,
            )
            operation = session.get(AgentOperation, editor)
            receipt = session.get(BusinessToolInvocation, int(result["business_invocation_id"]))
            operation.business_invocation_id = receipt.id
            operation.changes, operation.result = receipt.changes, result
            operation.result_refs = receipt.result_refs
            operation.status, operation.finished_at = "succeeded", utcnow()
            writes.append((invocation, values, result))
            saved_operations.append(editor)
    assert (
        int(writes[1][1].patch.actions[0].patch.row_version)
        == int(writes[0][1].patch.actions[0].patch.row_version) + 1
    )
    with factory() as session:
        replay = api.BusinessInvocationService(session).execute(
            writes[0][0],
            "workbench__apply_standard_patch",
            writes[0][1],
            apply_standard_patch_locked,
            mutating=True,
        )
        assert replay == writes[0][2]
    for scope, visible in ((left, True), (right, False)):
        reader = scope_operation(factory, accepted, scope.id, "runtime__read_operation_receipt")
        with factory.begin() as session:
            conversation_row = session.get(AgentConversation, conversation.id)
            task = session.get(AgentTask, accepted.task.id)
            operation = session.get(AgentOperation, reader)
            args = session, conversation_row, task, operation
            receipt_values = ReadReceipt(operation_id=saved_operations[0])
            result_values = ReadTaskResults(operation_id=saved_operations[0], ids=[str(asset.id)])
            if visible:
                assert read_receipt(*args, receipt_values, settings)["receipt"]["id"] == str(
                    saved_operations[0]
                )
                assert read_results_locked(*args, result_values, settings)["results"][0][
                    "id"
                ] == str(asset.id)
            else:
                with pytest.raises(BusinessError):
                    read_receipt(*args, receipt_values, settings)
                with pytest.raises(BusinessError):
                    read_results_locked(*args, result_values, settings)
            sources = describe_target(*args, DescribeTarget(), settings)["result_sources"]["items"]
            assert {int(item["operation_id"]) for item in sources} == (
                set(saved_operations) if visible else set()
            )
    with factory.begin() as session:
        row = session.get(ImageGroupItem, int(first_item["id"]))
        row.media_id, row.origin = (
            int(second_item["media_id"]),
            {"history": [first_item["media_id"]]},
        )
        row.item_version += 1
        row = session.get(Asset, asset.id)
        row.name, row.row_version = "用户后续编辑", row.row_version + 1
        latest = row.row_version
    reader = scope_operation(factory, accepted, right.id, "workbench__read_image_content")
    with factory.begin() as session:
        invocation = identity(session, reader)
        result = api.BusinessInvocationService(session).execute_locked(
            invocation,
            "workbench__read_image_content",
            ReadImageContent(
                **target,
                group_id=first["id"],
                item_id=first_item["id"],
                media_id=first_item["media_id"],
                purpose="读取本范围原生成图片",
            ),
            read_image_content_business_locked,
            mutating=False,
        )
        assert result["media_id"] == first_item["media_id"]
        with pytest.raises(BusinessError):
            api.BusinessInvocationService(session).execute_locked(
                invocation,
                "workbench__read_image_content",
                ReadImageContent(
                    **target,
                    group_id=first["id"],
                    item_id=first_item["id"],
                    media_id=second_item["media_id"],
                    purpose="不能读取后来换入的 sibling 图片",
                ),
                read_image_content_business_locked,
                mutating=False,
            )
    editor = scope_operation(factory, accepted, left.id, "workbench__apply_standard_patch")
    with factory() as session, pytest.raises(BusinessError):
        with session.begin():
            api.BusinessInvocationService(session).execute_locked(
                identity(session, editor),
                "workbench__apply_standard_patch",
                ApplyStandardPatch(
                    project_id=flow.project.id,
                    patch={
                        "actions": [
                            {
                                "action": "update_asset",
                                "asset": str(asset.id),
                                "patch": {
                                    "name": "猜中新版本也不能覆盖用户修改",
                                    "row_version": str(latest),
                                },
                            }
                        ]
                    },
                ),
                apply_standard_patch_locked,
                mutating=True,
            )


def test_selected_script_reference_limits_real_saved_text_and_locked_scope_is_restored(db_session):
    from short_drama.schemas.assistant.operations.script import ReadCurrentScript
    from short_drama.service.assistant.operations.script_read import read_current_script_locked
    from short_drama.service.creation.writing.script.documents import ProjectWritingService

    api = invocation_api()
    actor = db_session.info["actor"]
    project = ProjectService(db_session).create_project({"name": "Frozen script"})
    saved = ProjectWritingService(db_session).save_script(
        project.id,
        {
            "content_version": "1",
            "script_id": None,
            "content": "雨夜车站里，主角发现了一封信。",
        },
    )
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    reference = {
        "project_id": str(project.id),
        "script_id": saved["script"]["id"],
        "content_version": saved["content_version"],
        "start": 2,
        "end": 8,
    }
    invocation = business_identity(api, factory, actor, project.id, policy="selected")
    invocation = replace(invocation, scope=replace(invocation.scope, script_reference=reference))
    with factory.begin() as session:
        session.info["actor"], session.info["invocation_scope"] = actor, "caller-scope"
        result = api.BusinessInvocationService(session).execute_locked(
            invocation,
            "workbench__read_current_script",
            ReadCurrentScript(**reference),
            read_current_script_locked,
            mutating=False,
        )
        assert result["content"] == "车站里，主角"
        assert session.info["actor"] is actor and session.info["invocation_scope"] == "caller-scope"
    for change in ({"start": 0}, {"start": None, "end": None}):
        with factory() as session, pytest.raises(WorkflowError) as raised:
            api.BusinessInvocationService(session).execute(
                invocation,
                "workbench__read_current_script",
                ReadCurrentScript(**{**reference, **change}),
                read_current_script_locked,
                mutating=False,
            )
        assert raised.value.status_code == 403
    with factory() as session, pytest.raises(WorkflowError):
        api.BusinessInvocationService(session).execute(
            replace(invocation, scope=replace(invocation.scope, script_reference=None)),
            "workbench__read_current_script",
            ReadCurrentScript(**reference),
            read_current_script_locked,
            mutating=False,
        )


def test_canvas_images_use_real_node_media_and_frozen_versions_without_canvas_wide_access(
    db_session,
):
    import hashlib
    from contextlib import contextmanager
    from io import BytesIO
    from types import SimpleNamespace

    from PIL import Image

    from short_drama.agent.context.business_scope import selected_scope_subjects
    from short_drama.core.config import Settings
    from short_drama.core.time import utcnow
    from short_drama.dao.assistant.context.context import AssistantContextDAO
    from short_drama.domain import MediaFile
    from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
    from short_drama.schemas.assistant.operations.canvas import ApplyCanvasPatch
    from short_drama.schemas.assistant.operations.image import ReadImageContent
    from short_drama.schemas.assistant.tasks.task import TaskSubjectReference
    from short_drama.service.assistant.context.subject import freeze_subject
    from short_drama.service.assistant.operations.canvas import apply_canvas_patch_locked
    from short_drama.service.assistant.operations.image import (
        read_business_image_bytes,
        read_image_content_business_locked,
    )
    from short_drama.service.canvas.document.canvas import CanvasService
    from short_drama.utils.snowflake import next_id
    from tests.integration.canvas.document.test_canvas_workspace import create, node

    api = invocation_api()
    actor = db_session.info["actor"]
    project_id, canvas_id = create(db_session)
    service = CanvasService(db_session)
    document = service.read(project_id, canvas_id, private=True)["source_document"]
    configured = Settings(_env_file=None)
    media_ids, objects = [], {}
    with db_session.begin():
        for index in range(2):
            stream = BytesIO()
            Image.new("RGB", (4, 4), (index * 60, 80, 140)).save(stream, "PNG")
            data, identifier = stream.getvalue(), next_id()
            key = f"canvas/fixture/{identifier}.png"
            objects[configured.minio_image_bucket, key] = data
            db_session.add(
                MediaFile(
                    id=identifier,
                    project_id=project_id,
                    scope_user_id=None,
                    created_by=actor.user_id,
                    updated_by=actor.user_id,
                    format_code="image/png",
                    storage_locator=f"minio://{configured.minio_image_bucket}/{key}",
                    original_name="canvas fixture.png",
                    width=4,
                    height=4,
                    byte_size=len(data),
                    checksum_sha256=hashlib.sha256(data).hexdigest(),
                    published_at=utcnow(),
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
            )
            media_ids.append(str(identifier))
    document["nodes"] = []
    for key, identifier in zip(("image-a", "image-b"), media_ids, strict=True):
        value = node(key)
        value["type"], value["metadata"] = "image", {"storageKey": f"resource:{identifier}"}
        document["nodes"].append(value)
    service.commit(
        project_id,
        canvas_id,
        {"expected_row_version": document["revision"], "source_document": document},
        "canvas-images",
    )
    factory = sessionmaker(db_session.get_bind(), expire_on_commit=False, autoflush=False)
    with factory.begin() as session:
        session.info["actor"] = actor
        dao = AssistantContextDAO(session)
        selected = freeze_subject(
            dao,
            actor.user_id,
            TaskSubjectReference(
                project_id=project_id,
                source_kind="canvas",
                source_id=document["id"],
                kind="node",
                id="image-a",
                revision=str(dao.canvas_node(canvas_id, "image-a").row_version),
            ),
        )
    assert [item["media_id"] for item in selected["media"]] == media_ids[:1]

    def invocation(prior=()):
        allowed, catalogs, snapshots = selected_scope_subjects([selected], str(project_id), prior)
        return api.BusinessInvocation(
            actor,
            "assistant",
            "canvas-read",
            api.InvocationScope(
                frozenset({project_id}),
                frozenset({"workbench__read_image_content", "workbench__apply_canvas_patch"}),
                "selected",
                allowed,
                image_catalogs=catalogs,
                subject_snapshots=snapshots,
            ),
        )

    target = {
        "project_id": str(project_id),
        "target_kind": "canvas_node",
        "target_id": "image-a",
        "canvas_id": document["id"],
        "media_id": media_ids[0],
        "purpose": "核对已选节点的图片",
    }

    @contextmanager
    def open_image(bucket, key):
        assert factory.kw["bind"].pool.checkedout() == 0
        yield BytesIO(objects[bucket, key])

    storage = SimpleNamespace(settings=configured, open=open_image)
    data, mime = read_business_image_bytes(
        factory, invocation(), ReadImageContent(**target), storage
    )
    assert data.startswith(b"\x89PNG") and mime == "image/png"
    for change in (
        {"target_id": "image-b", "media_id": media_ids[1]},
        {"media_id": media_ids[1]},
        {"canvas_id": "unselected-canvas"},
    ):
        with factory() as session, pytest.raises(BusinessError):
            api.BusinessInvocationService(session).execute(
                invocation(),
                "workbench__read_image_content",
                ReadImageContent(**{**target, **change}),
                read_image_content_business_locked,
                mutating=False,
            )
    project_identity = replace(
        invocation(), scope=replace(invocation().scope, subject_policy="project")
    )
    with factory() as session:
        result = api.BusinessInvocationService(session).execute(
            project_identity,
            "workbench__read_image_content",
            ReadImageContent(**target),
            read_image_content_business_locked,
            mutating=False,
        )
        assert result["media_id"] == media_ids[0]
    with factory() as session, pytest.raises(BusinessError):
        api.BusinessInvocationService(session).execute(
            project_identity,
            "workbench__read_image_content",
            ReadImageContent(**{**target, "media_id": media_ids[1]}),
            read_image_content_business_locked,
            mutating=False,
        )
    prior = []
    for index in range(2):
        identity = replace(invocation(prior), request_id=f"canvas-update-{index}")
        current = service.read(project_id, canvas_id, private=True)["source_document"]
        version = identity.scope.subject_snapshots["canvas_node:image-a"]["write_versions"]
        with factory() as session:
            result = api.BusinessInvocationService(session).execute(
                identity,
                "workbench__apply_canvas_patch",
                ApplyCanvasPatch(
                    project_id=project_id,
                    canvas_id=document["id"],
                    patch={
                        "expected_revision": current["revision"],
                        "actions": [
                            {
                                "action": "update_node",
                                "node": "image-a",
                                **version,
                                "patch": {"prompt": f"本范围第{index}次私人参数修改"},
                            }
                        ],
                    },
                ),
                apply_canvas_patch_locked,
                mutating=True,
            )
            receipt = session.get(BusinessToolInvocation, int(result["business_invocation_id"]))
            prior.append(SimpleNamespace(result=result, changes=receipt.changes))
    assert (
        invocation(prior).scope.subject_snapshots["canvas_node:image-a"]["write_versions"][
            "private_revision"
        ]
        == "2"
    )
    current = service.read(project_id, canvas_id, private=True)["source_document"]
    current["nodes"][0]["title"] = "用户后续修改"
    service.commit(
        project_id,
        canvas_id,
        {"expected_row_version": current["revision"], "source_document": current},
        "external-canvas-edit",
    )
    current = service.read(project_id, canvas_id, private=True)["source_document"]
    with factory() as session, pytest.raises(BusinessError):
        api.BusinessInvocationService(session).execute(
            replace(invocation(prior), request_id="stale-canvas"),
            "workbench__apply_canvas_patch",
            ApplyCanvasPatch(
                project_id=project_id,
                canvas_id=document["id"],
                patch={
                    "expected_revision": current["revision"],
                    "actions": [
                        {
                            "action": "update_node",
                            "node": "image-a",
                            "row_version": "2",
                            "private_revision": "2",
                            "patch": {"title": "猜中最新版本也不能覆盖用户修改"},
                        }
                    ],
                },
            ),
            apply_canvas_patch_locked,
            mutating=True,
        )
