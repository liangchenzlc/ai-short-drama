"""离线切换保留业务与 typed 图边界；Worker 计数使用明确的受控探针。"""

import hashlib
import re
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import sessionmaker

from short_drama.agent.core import ENGINE_VERSION
from short_drama.core.config import Settings
from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.graph import AgentGraphCheckpoint
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentTask
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    new_task,
    task_service,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    task_workspace as task_workspace,
)
from tests.support.assistant_tools import scope_graph

pytestmark = pytest.mark.integration


def offline_settings():
    return Settings(_env_file=None, auth_enabled=True, agent_enabled=False)


@pytest.fixture
def legacy_runtime_engine():
    from tests.integration.conftest import isolated_mysql_database

    path = Path(__file__).resolve().parents[3] / "fixtures/agent_runtime_legacy/schema.mysql8.sql"
    source = path.read_bytes()
    assert hashlib.sha256(source).hexdigest() == (
        "cfdd7e9b05276b7858313ba914c30d8db0f2befe6823df9568d9c738efad233c"
    )
    assert len(re.findall(rb"(?m)^CREATE TABLE", source)) == 119
    with closing(isolated_mysql_database(schema_path=path)) as databases:
        yield next(databases)


@pytest.mark.parametrize(
    "human_edit,previously_submitted", [(False, True), (True, True), (False, False)]
)
def test_real_legacy_cutover_preserves_media_settlement_and_historical_undo(
    legacy_runtime_engine, human_edit, previously_submitted
):
    from scripts.agent_runtime_migration import AUDIT_KEY, apply
    from short_drama.agent.coordinator.media_runtime import collect_media_results
    from short_drama.core.time import utcnow
    from short_drama.domain import AIGenerationRecord, Asset, AsyncTask, ImageGroup, MediaAsset
    from short_drama.domain.assistant.execution.models import AgentToolCall
    from short_drama.domain.assistant.operations.media import AgentMediaRequest
    from short_drama.domain.assistant.tasks.task import AgentOperation
    from short_drama.schemas.assistant.operations.media import AssistantImageRequest
    from short_drama.schemas.assistant.operations.standard import StandardPatch
    from short_drama.service.assistant.operations.media import (
        admit_media_locked,
        prepare_image_locked,
    )
    from short_drama.service.assistant.operations.standard import AssistantStandardService
    from short_drama.service.generations.execution.execution import GenerationExecutionService
    from tests.integration.standard.assets.test_asset_image_generation import (
        ImageProvider,
        MemoryStorage,
        drain,
    )
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    engine, settings = legacy_runtime_engine, offline_settings()
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with engine.begin() as connection:
        for sql in (
            "INSERT users (id,username,display_name,email,password_hash,status,"
            "email_verified_at,created_at) VALUES(1,'legacy','Legacy','legacy@example.test',"
            "'fixture','active',NOW(6),NOW(6))",
            "INSERT projects (id,owner_user_id,name) VALUES(2,1,'旧媒体真实迁移')",
            "INSERT ai_model_configs (id,owner_user_id,service_type,name,model_key,provider) "
            "VALUES(3,1,'text','Legacy text','fixture','compatible')",
            "INSERT ai_model_configs (id,owner_user_id,service_type,name,model_key,provider,"
            "base_url) VALUES(8,1,'image','Legacy image','fixture','ark',"
            "'https://ark.cn-beijing.volces.com/api/v3')",
            "INSERT assets (id,project_id,kind,name,position) "
            "VALUES(9,2,'character','迁移前已选角色',1)",
            "INSERT agent_conversations (id,owner_user_id,scope_version,title,next_message_seq,"
            "next_item_seq) VALUES(4,1,3,'旧任务',2,2)",
            "INSERT agent_tasks (id,conversation_id,goal,execution_target) "
            "VALUES(5,4,'保留已受理媒体',JSON_OBJECT('mode','standard','project_id','2'))",
            "INSERT agent_messages (id,conversation_id,task_id,seq,role,content) "
            "VALUES(6,4,5,1,'user','为角色生成图片')",
            "INSERT agent_runs (id,conversation_id,task_id,trigger_message_id,initiated_by,"
            "model_config_id,status,checkpoint) VALUES(7,4,5,6,1,3,'waiting_generation',"
            "JSON_OBJECT('engine','langgraph-harness.1'))",
            "INSERT agent_turns(id,run_id,turn_no,status,finished_at) "
            "VALUES(10,7,1,'succeeded',NOW(6))",
        ):
            connection.exec_driver_sql(sql)
        names = [
            "submit_generation_batch",
            "apply_standard_patch",
            "apply_canvas_patch",
            "set_image_group_primary",
            "read_current_script",
        ]
        for index, name in enumerate(names):
            connection.execute(
                text(
                    "INSERT agent_tool_calls(id,run_id,turn_id,call_index,provider_call_id,"
                    "tool_name,arguments_hash,idempotency_key,status) "
                    "VALUES(:id,7,10,:index,:provider,:name,:hash,:key,'succeeded')"
                ),
                {
                    "id": 11 + index * 2,
                    "index": index + 1,
                    "provider": f"old-{index}",
                    "name": name,
                    "hash": "a" * 64,
                    "key": str(index) * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT agent_operations(id,task_id,run_id,tool_call_id,tool_name,"
                    "plan_revision,input_revision,payload_hash,status,finished_at) "
                    "VALUES(:id,5,7,:call,:name,1,0,:hash,'succeeded',NOW(6))"
                ),
                {"id": 12 + index * 2, "call": 11 + index * 2, "name": name, "hash": "b" * 64},
            )
    folder = (
        Path(__file__).resolve().parents[5]
        / "docs/database/migrations/2026-10-29-assistant-runtime"
    )
    for filename in (
        "001-graph-and-scopes.sql",
        "002-business-invocations.sql",
        "003-assistant-settings.sql",
        "004-durable-waits-and-questions.sql",
        "005-mcp-access-tokens.sql",
    ):
        source = re.sub(r"(?m)^\s*--.*$", "", (folder / filename).read_text(encoding="utf-8"))
        with engine.begin() as connection:
            for statement in source.split(";"):
                if statement.strip():
                    connection.exec_driver_sql(statement)
    with factory.begin() as session:
        session.info["actor"] = task_actor()
        patch = StandardPatch.model_validate(
            {
                "actions": [
                    {
                        "action": "create_asset",
                        "key": "prop",
                        "values": {"kind": "prop", "name": "旧批次保存的道具"},
                    }
                ]
            }
        )
        result = AssistantStandardService(session, settings).apply_locked(
            2, patch, owner_user_id=1, operation_id=14
        )
        history = session.get(AgentOperation, 14)
        history.result, history.changes = result, result["changes"]
        values = AssistantImageRequest.model_validate(
            {
                "target_kind": "asset",
                "target_id": "9",
                "row_version": "1",
                "model_config_id": "8",
                "model_version": "1",
                "prompt": "已明确授权的雨夜角色",
            }
        )
        request, target = prepare_image_locked(session, 1, 2, values, 1)
        intent = SimpleNamespace(
            id=30,
            task_id=5,
            operation_id=12,
            item_index=1,
            media_kind="image",
            requested_count=1,
            frozen_target=target,
            frozen_request=request,
            reservation_state="reserved",
            status="reserved",
        )
        admit_media_locked(session, 1, intent, settings)
        # Import frozen data; the current ORM correctly rejects new old-name intents.
        session.connection().execute(AgentMediaRequest.__table__.insert().values(**vars(intent)))
        native_id = intent.generation_task_id
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == native_id)
        )
        if previously_submitted:
            # Frozen legacy evidence: a paid request was accepted before the offline cutover.
            native = session.get(AsyncTask, native_id)
            native.status, native.next_action, native.started_at = "running", "poll", utcnow()
            record.status, record.provider_task_id = "sent", "legacy-accepted-request"
            record.started_at, record.adapter = native.started_at, "ark_images.v1"
        operation = session.get(AgentOperation, 12)
        operation.status = operation.phase = "waiting_media"
        operation.finished_at = None
        operation.result = {"old_receipt": "preserved"}
        call = session.get(AgentToolCall, 11)
        call.status, call.generation_task_id = "waiting_generation", native_id
        session.get(AgentTask, 5).media_usage = {
            "images": {"reserved": 1, "submitted": 0, "succeeded": 0},
            "videos": {"reserved": 0, "submitted": 0, "succeeded": 0},
            "audio": {"reserved": 0, "submitted": 0, "succeeded": 0},
        }
    assert apply(engine, settings, worker_probe=lambda _: 0)["status"] == "ready"
    assert apply(engine, settings, worker_probe=lambda _: 0)["status"] == "ready"
    with factory() as session:
        for index, name in enumerate(names[:4]):
            operation = session.get(AgentOperation, 12 + index * 2)
            call = session.get(AgentToolCall, 11 + index * 2)
            assert operation.tool_name == call.tool_name == "workbench__" + name
            assert operation.result[AUDIT_KEY]["tool_name"] == name
            assert call.result[AUDIT_KEY]["tool_name"] == name
        assert session.get(AgentOperation, 20).tool_name == "read_current_script"
        assert session.get(AgentRun, 7).status == "cancelled"
        assert session.get(AsyncTask, native_id).next_action == (
            "poll" if previously_submitted else "submit"
        )
        assert session.get(AIGenerationRecord, record.id).provider_task_id == (
            "legacy-accepted-request" if previously_submitted else None
        )
    assert collect_media_results(factory, settings) == 0
    with factory() as session:
        assert session.get(AgentOperation, 12).result[AUDIT_KEY]["tool_name"] == (
            "submit_generation_batch"
        )
    if human_edit:
        with factory.begin() as session:
            asset = session.get(Asset, 9)
            asset.description, asset.row_version = "人工在生成期间修改", asset.row_version + 1
    output = ImageProvider().submit({}, request)

    class AcceptedImageProvider:
        def __init__(self):
            self.calls, self.submissions = [], 0

        def submit(self, *_args, **_kwargs):
            self.submissions += 1
            pytest.fail("An accepted legacy request must never be submitted again")

        def poll(self, _snapshot, identifier, *_args, **_kwargs):
            self.calls.append(identifier)
            assert identifier == "legacy-accepted-request"
            return output

    provider, storage = AcceptedImageProvider(), MemoryStorage()
    worker = GenerationExecutionService(factory, settings, provider, storage)
    status = drain(SimpleNamespace(factory=factory, executor=worker), native_id)
    assert status == ("succeeded" if previously_submitted else "cancelled")
    assert collect_media_results(factory, settings) == 1
    assert collect_media_results(factory, settings) == 0
    assert provider.submissions == 0
    if not previously_submitted:
        assert not provider.calls
        with factory() as session:
            assert session.get(AsyncTask, native_id).error["code"] == "access_revoked"
            assert session.get(AgentMediaRequest, 30).reservation_state == "released"
            assert session.get(AgentTask, 5).media_usage["images"] == {
                "reserved": 0,
                "submitted": 0,
                "succeeded": 0,
            }
            assert session.get(AgentOperation, 12).result[AUDIT_KEY]["tool_name"] == (
                "submit_generation_batch"
            )
            assert session.get(AgentRun, 7).status == "cancelled"
            assert not session.scalar(select(MediaAsset.id))
        return
    with factory() as session:
        operation = session.get(AgentOperation, 12)
        assert operation.status == ("conflict" if human_edit else "succeeded")
        assert operation.result["settled"] and operation.result_refs[0]["generated_count"] == 1
        assert operation.result[AUDIT_KEY]["tool_name"] == "submit_generation_batch"
        assert session.get(AgentToolCall, 11).result[AUDIT_KEY]["tool_name"] == (
            "submit_generation_batch"
        )
        assert session.get(AgentTask, 5).media_usage["images"] == {
            "reserved": 0,
            "submitted": 1,
            "succeeded": 1,
        }
        assert session.get(AgentMediaRequest, 30).reservation_state == "committed"
        assert session.get(AsyncTask, native_id).status == "succeeded"
        assert session.scalar(select(MediaAsset.id))
        assert (
            session.get(AgentRun, 7).status == "cancelled"
            and session.get(AgentRun, 7).lease_token is None
        )
        group = session.scalar(select(ImageGroup).where(ImageGroup.task_id == native_id))
        asset = session.get(Asset, 9)
        # 生成完成即写入作品，与生成过程中是否有人工编辑无关。
        assert asset.media_id
        assert group.primary_index == 1
    with factory() as session:
        service = task_service(session, settings.model_copy(update={"agent_enabled": True}))
        for identifier in [14] if human_edit else [12, 14]:
            preview = service.undo_preview(identifier)
            assert preview.items and all(item.allowed for item in preview.items), preview
            body = {
                "preview_hash": preview.preview_hash,
                "selected_keys": [item.key for item in preview.items],
            }
            receipt = service.undo_batch(identifier, body, f"legacy-undo-{identifier}")
            assert (
                service.undo_batch(identifier, body, f"legacy-undo-{identifier}").id == receipt.id
            )
    assert len(provider.calls) == 1


def test_offline_cutover_keeps_messages_and_current_typed_graph_and_does_not_replay(task_workspace):
    from scripts.agent_runtime_migration import apply, precheck

    factory, _settings, _model = task_workspace
    _conversation, old = new_task(task_workspace)
    context, _transport, operations = scope_graph(
        task_workspace, old, [("read", "runtime__get_generation_policy", {})]
    )
    with factory.begin() as session:
        run = session.get(AgentRun, old.run.id)
        run.checkpoint = {**run.checkpoint, "engine": "langgraph-harness.1"}
        run.lease_token = run.lease_until = None
        task = session.get(AgentTask, old.task.id)
        task.effective_state = {**task.effective_state, "engine": "langgraph-harness.1"}
        old_graphs = session.scalar(
            select(func.count())
            .select_from(AgentGraphCheckpoint)
            .where(AgentGraphCheckpoint.run_id == run.id)
        )
    assert old_graphs > 0 and operations[0].status == "succeeded"
    with factory() as session:
        service = task_service(session, _settings)
        conversation = service.create_global_conversation({}, "current-conversation")
        current = service.send_task_message(
            conversation.id,
            {"content": "当前引擎的独立任务", "model_config_id": _model},
            "current-message",
        )
    scope_graph(task_workspace, current, [("current", "runtime__get_generation_policy", {})])
    with factory.begin() as session:
        run = session.get(AgentRun, current.run.id)
        run.lease_token = run.lease_until = None
        messages = [(row.id, row.content) for row in session.scalars(select(AgentMessage))]
        current_graphs = session.scalar(
            select(func.count())
            .select_from(AgentGraphCheckpoint)
            .where(AgentGraphCheckpoint.run_id == current.run.id)
        )
    engine = factory.kw["bind"]
    checked = precheck(engine, offline_settings(), worker_probe=lambda _settings: 0)
    assert checked["blockers"] == [] and checked["old_active_runs"] == 1, checked
    result = apply(engine, offline_settings(), worker_probe=lambda _settings: 0)
    assert result["status"] == "ready"
    assert apply(engine, offline_settings(), worker_probe=lambda _settings: 0)["status"] == "ready"
    with factory() as session:
        old_run = session.get(AgentRun, old.run.id)
        assert old_run.status == "cancelled" and old_run.next_run_at is None
        assert old_run.error == {"code": "assistant_engine_updated"}
        assert session.get(AgentTask, old.task.id).effective_state["engine"] == ENGINE_VERSION
        original_ids = {item[0] for item in messages}
        all_messages = list(session.scalars(select(AgentMessage)))
        assert [(row.id, row.content) for row in all_messages if row.id in original_ids] == messages
        notices = [row for row in all_messages if row.id not in original_ids]
        assert len(notices) == 1 and notices[0].kind == "notice"
        assert notices[0].content == "执行引擎已更新，请重新发送继续"
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentGraphCheckpoint)
                .where(AgentGraphCheckpoint.run_id == old_run.id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentGraphCheckpoint)
                .where(AgentGraphCheckpoint.run_id == current.run.id)
            )
            == current_graphs
        )
        assert session.get(AgentRun, current.run.id).checkpoint["engine"] == ENGINE_VERSION


@pytest.mark.parametrize("blocker", ["workers", "lease", "schema", "enabled"])
def test_offline_cutover_refuses_observed_workers_live_lease_unknown_schema_and_enabled_agent(
    task_workspace, blocker
):
    from scripts.agent_runtime_migration import apply, precheck

    factory, _settings, _model = task_workspace
    _conversation, accepted = new_task(task_workspace)
    engine = factory.kw["bind"]
    settings = offline_settings()
    if blocker == "enabled":
        settings.agent_enabled = True
    if blocker == "lease":
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE agent_runs SET lease_token=REPEAT('a',32), "
                    "lease_until=DATE_ADD(UTC_TIMESTAMP(6),INTERVAL 10 MINUTE) WHERE id=:id"
                ),
                {"id": accepted.run.id},
            )
    if blocker == "schema":
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE agent_tasks ADD unexpected_runtime_column INT")
    try:

        def probe(_settings):
            return int(blocker == "workers")

        state = precheck(engine, settings, worker_probe=probe)
        assert state["blockers"]
        with pytest.raises(RuntimeError, match="Offline cutover refused"):
            apply(engine, settings, worker_probe=probe)
        with factory() as session:
            assert session.get(AgentRun, accepted.run.id).status == "queued"
            assert session.get(AgentMessage, accepted.message.id).content == "讨论镜头节奏"
    finally:
        if blocker == "schema":
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE agent_tasks DROP COLUMN unexpected_runtime_column"
                )
