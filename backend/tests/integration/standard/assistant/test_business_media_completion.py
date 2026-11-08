"""正式图受理、原生Worker和持久业务采用；模型与媒体供应商均受控。"""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from short_drama.domain import AIGenerationRecord, Asset, AsyncTask
from short_drama.domain.assistant.operations.media import AgentMediaRequest
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.domain.canvas.document.canvas import CanvasNode
from short_drama.domain.creation.sound.tracks.sound import ProjectSound, SoundMediaReference
from short_drama.domain.creation.sound.voices.voices import CharacterVoice
from short_drama.service.base import utcnow
from short_drama.service.creation.sound.tracks.sound import ProjectSoundService
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    canvas_media_flow,
    dialogue_audio_flow,
    dialogue_audio_provider,
    drain_assistant_media,
    media_tool_run,
    media_worker,
    shot_video_flow,
    shot_video_provider,
    task_service,
    voice_design_flow,
    voice_design_provider,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    task_workspace as task_workspace,
)
from tests.support.assistant_tools import scope_graph

pytestmark = pytest.mark.integration


def submitted(flow):
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded", operation.error
        assert operation.result["admitted"] and not operation.result["settled"]
        assert session.scalar(select(func.count()).select_from(AgentMediaRequest)) == 0
        return deepcopy(operation.result)


def status(flow):
    receipt = submitted(flow)
    _, _, operations = scope_graph(
        (flow.factory, flow.settings, None),
        flow.accepted,
        [
            (
                "status",
                "workbench__get_generation_status",
                {
                    "project_id": flow.reference["project_id"],
                    "generation_ids": [item["generation_id"] for item in receipt["items"]],
                },
            )
        ],
        context=flow.context,
    )
    assert operations[-1].status == "succeeded", operations[-1].error
    with flow.factory() as session:
        assert session.get(AgentOperation, flow.operation_id).result == receipt
    return operations[-1].result


@pytest.mark.parametrize("completion_order", ["reverse", "concurrent"])
def test_dialogue_batch_adopts_reverse_completions_without_staling_frozen_siblings(
    task_workspace, tmp_path, completion_order
):
    flow = dialogue_audio_flow(task_workspace, lines=3)
    receipt = submitted(flow)
    assert {item["row_version"] for item in flow.body["requests"]} == {"1"}
    provider, worker = media_worker(flow, dialogue_audio_provider(tmp_path))

    def complete(item):
        drain_assistant_media(flow, worker, generation_ids=[int(item["generation_id"])])
        with flow.factory() as session:
            native = session.get(AsyncTask, int(item["generation_id"]))
            assert native.status == "succeeded", native.error

    if completion_order == "reverse":
        for index, item in enumerate(reversed(receipt["items"])):
            complete(item)
            with flow.factory() as session:
                sound = session.get(ProjectSound, flow.assembly_id)
                assert len(sound.document["audio_clips"]) == index + 1
                assert sound.row_version == index + 2
    else:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(complete, reversed(receipt["items"])))
    assert len(provider.calls) == 3 and status(flow)["settled"]
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert len(operation.changes) == 3
        assert {change["id"] for change in operation.changes} == {"line_0", "line_1", "line_2"}
        sound = session.get(ProjectSound, flow.assembly_id)
        assert len(sound.document["audio_clips"]) == 3 and sound.row_version == 4


@pytest.mark.parametrize("kind", ["sound", "voice"])
def test_native_adoption_undo_preserves_submit_receipt_and_archived_media(
    task_workspace, tmp_path, kind
):
    from short_drama.domain import MediaAsset, MediaFile

    flow = (
        dialogue_audio_flow(task_workspace)
        if kind == "sound"
        else voice_design_flow(task_workspace)
    )
    original = submitted(flow)
    provider = (
        dialogue_audio_provider(tmp_path) if kind == "sound" else voice_design_provider(tmp_path)
    )
    _, worker = media_worker(flow, provider)
    drain_assistant_media(flow, worker)
    with flow.factory() as session:
        service = task_service(session, flow.settings)
        service.control_task(flow.accepted.task.id, {"kind": "stop"}, "stop-before-native-undo")
        preview = service.undo_preview(flow.operation_id)
        assert preview.items and all(item.allowed for item in preview.items), preview
        body = {
            "preview_hash": preview.preview_hash,
            "selected_keys": [item.key for item in preview.items],
        }
        receipt = service.undo_batch(flow.operation_id, body, "native-undo")
        assert service.undo_batch(flow.operation_id, body, "native-undo").id == receipt.id
    with flow.factory() as session:
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.status == "succeeded" and operation.result == original
        outputs = session.scalars(select(MediaAsset)).all()
        assert outputs and all(session.get(MediaFile, item.media_id) for item in outputs)
        assert session.scalar(select(func.count()).select_from(AgentMediaRequest)) == 0
        if kind == "sound":
            document = session.get(ProjectSound, flow.assembly_id).document
            assert document["audio_clips"] == [] and document["dialogue"][0]["media_id"] is None
            assert document["subtitles"][0]["text"] == "未选字幕正文"
        else:
            binding = session.get(
                CharacterVoice, (int(flow.reference["project_id"]), int(flow.reference["id"]))
            )
            assert (
                binding.record_id is None and binding.media_id is None and binding.row_version == 2
            )


def test_dialogue_replacement_keeps_clip_identity_and_both_real_candidates(
    task_workspace, tmp_path
):
    first = dialogue_audio_flow(task_workspace)
    first_receipt = submitted(first)
    provider, worker = media_worker(first, dialogue_audio_provider(tmp_path))
    drain_assistant_media(first, worker)
    with first.factory() as session:
        sound = session.get(ProjectSound, first.assembly_id)
        assert len(sound.document["audio_clips"]) == 1
        original = deepcopy(sound.document["audio_clips"][0])
        reference = {**first.reference, "revision": str(sound.row_version)}
    request = first.body["requests"][0]
    second = media_tool_run(
        task_workspace,
        media_kind="audio",
        references=[reference],
        targets=[reference],
        identity_key="native-dubbing-replacement",
        existing_model=SimpleNamespace(id=int(request["model_config_id"]), row_version=1),
        item_patch={"target_kind": "dialogue_audio", "media_kind": "audio"},
    )
    submitted(second)
    next_provider, next_worker = media_worker(
        second, dialogue_audio_provider(tmp_path), storage=worker.storage
    )
    drain_assistant_media(second, next_worker)
    assert len(provider.calls) == len(next_provider.calls) == 1
    assert status(second)["settled"]
    with second.factory() as session:
        task_service(session, second.settings)
        sound = session.get(ProjectSound, first.assembly_id)
        assert len(sound.document["audio_clips"]) == 1
        current = sound.document["audio_clips"][0]
        assert current["id"] == original["id"] and current["media_id"] != original["media_id"]
        assert session.get(SoundMediaReference, (first.assembly_id, int(original["media_id"])))
        assert session.get(AgentOperation, first.operation_id).result == first_receipt
    with second.factory() as session:
        task_service(session, second.settings)
        candidates = ProjectSoundService(session, second.settings, next_worker.storage).candidates(
            reference["project_id"], reference["id"]
        )
        assert len(candidates) == 2
        assert {
            output["media_id"] for candidate in candidates for output in candidate["outputs"]
        } == {
            original["media_id"],
            current["media_id"],
        }


@pytest.mark.parametrize("human_edit", [False, True])
def test_voice_native_archive_adopts_without_agent_loop_and_protects_human_change(
    task_workspace, tmp_path, human_edit
):
    flow = voice_design_flow(task_workspace)
    receipt = submitted(flow)

    def edit_after_send():
        if human_edit:
            with flow.factory.begin() as session:
                asset = session.get(Asset, int(flow.reference["id"]))
                asset.description = "发送后人工修改"
                asset.row_version += 1

    provider, worker = media_worker(
        flow, voice_design_provider(tmp_path, before_submit=edit_after_send)
    )
    drain_assistant_media(flow, worker)
    assert len(provider.calls) == 1 and status(flow)["settled"]
    with flow.factory() as session:
        binding = session.get(
            CharacterVoice, (int(flow.reference["project_id"]), int(flow.reference["id"]))
        )
        assert (binding is None) == human_edit
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.result == receipt and bool(operation.changes) != human_edit
        if binding:
            assert binding.row_version == 1
            assert session.get(AIGenerationRecord, binding.record_id).response_data["voice"][
                "voice_id"
            ]


@pytest.mark.parametrize(
    "kind,closed,adopted",
    [
        ("sound", "workbench", False),
        ("voice", "workbench", False),
        ("canvas", "workbench", False),
        ("video", "workbench", False),
        # 关闭媒体生成不撤销工作台写权限，生成完成后仍自动写入视频槽。
        ("video", "media_generation", True),
    ],
)
def test_sent_media_adoption_uses_current_workbench_permission(
    task_workspace, tmp_path, kind, closed, adopted
):
    from short_drama.domain import CanvasResult, CanvasTaskBinding, MediaAsset, ShotVideo
    from short_drama.service.assistant.settings.preferences import AssistantSettingsService

    if kind == "sound":
        flow = dialogue_audio_flow(task_workspace)
        provider = dialogue_audio_provider(tmp_path)
    elif kind == "voice":
        flow = voice_design_flow(task_workspace)
        provider = voice_design_provider(tmp_path)
    elif kind == "video":
        flow = shot_video_flow(task_workspace)
        provider = shot_video_provider(tmp_path)
    else:
        flow = canvas_media_flow(task_workspace)
        provider = None
    receipt = submitted(flow)
    provider, worker = media_worker(flow, provider)
    task_id = int(receipt["items"][0]["generation_id"])
    with flow.factory() as session:
        version = session.get(AsyncTask, task_id).message_version
    worker.execute(task_id, version)
    assert len(provider.calls) == 1
    with flow.factory() as session:
        task_service(session, flow.settings)
        service = AssistantSettingsService(session, flow.settings)
        current = service.get_settings()
        service.update_settings(
            {"settings_version": str(current.settings_version), f"{closed}_enabled": False}
        )
        if kind == "canvas":
            node = session.scalar(
                select(CanvasNode).where(CanvasNode.node_key == flow.reference["id"])
            )
            before_node = deepcopy(node.content_json), node.row_version
    drain_assistant_media(flow, worker)
    with flow.factory() as session:
        native = session.get(AsyncTask, task_id)
        assert native.status == "succeeded", native.error
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == task_id)
        )
        output = session.scalar(select(MediaAsset).where(MediaAsset.record_id == record.id))
        assert output.media_id and record.response_data["business_adoption"]["adopted"] is adopted
        operation = session.get(AgentOperation, flow.operation_id)
        assert operation.result == receipt and bool(operation.changes) is adopted
        if kind == "sound":
            document = session.get(ProjectSound, flow.assembly_id).document
            assert document["audio_clips"] == [] and document["dialogue"][0]["media_id"] is None
        elif kind == "voice":
            assert (
                session.get(
                    CharacterVoice, (int(flow.reference["project_id"]), int(flow.reference["id"]))
                )
                is None
            )
        elif kind == "video":
            video = session.scalar(
                select(ShotVideo).where(ShotVideo.shot_id == int(flow.reference["id"]))
            )
            assert (video is not None) is adopted
            if video:
                assert video.media_id == output.media_id
        else:
            result = session.scalar(
                select(CanvasResult)
                .join(CanvasTaskBinding, CanvasTaskBinding.id == CanvasResult.task_binding_id)
                .where(CanvasTaskBinding.async_task_id == task_id)
            )
            assert result.media_id == output.media_id and result.attachment_status == "detached"
            node = session.scalar(
                select(CanvasNode).where(CanvasNode.node_key == flow.reference["id"])
            )
            assert (node.content_json, node.row_version) == before_node


def test_generated_reference_origin_is_rechecked_before_next_native_paid_send(task_workspace):
    flow = canvas_media_flow(task_workspace, nodes=2, target_indices=[0])
    first = submitted(flow)
    _, worker = media_worker(flow)
    drain_assistant_media(flow, worker)
    saved = status(flow)
    media_id = saved["items"][0]["result_refs"][0]["media_id"]
    target = flow.references[1]
    with flow.factory() as session:
        task = session.get(AgentTask, flow.accepted.task.id)
        frozen = next(
            item for item in task.context_snapshot["subjects"] if item["id"] == target["id"]
        )
    request = {
        **flow.body["requests"][0],
        "target_id": target["id"],
        "row_version": target["revision"],
        "private_revision": frozen["write_versions"]["private_revision"],
        "reference_media_ids": [media_id],
    }
    _, _, operations = scope_graph(
        task_workspace,
        flow.accepted,
        [("followup", "workbench__submit_generation_batch", {**flow.body, "requests": [request]})],
        context=flow.context,
    )
    operation = operations[-1]
    assert operation.status == "succeeded", operation.error
    with flow.factory.begin() as session:
        origin = session.scalar(select(CanvasNode).where(CanvasNode.node_key == "original-a"))
        origin.archived_at = utcnow()
    next_flow = SimpleNamespace(**{**vars(flow), "operation_id": operation.id})
    provider, next_worker = media_worker(next_flow)
    drain_assistant_media(next_flow, next_worker)
    assert provider.calls == []
    with flow.factory() as session:
        native = session.get(AsyncTask, int(operation.result["items"][0]["generation_id"]))
        assert native.status in {"cancelled", "failed"}
        assert native.error["code"] == "assistant_generation_reference_changed"
        assert session.get(AgentOperation, flow.operation_id).result == first


def test_media_catalog_uses_owned_real_versions_and_bounded_pages(task_workspace):
    from short_drama.domain import AIModelConfig
    from short_drama.domain.identity.accounts.user import User
    from short_drama.service.ai_config.management.models import AIModelConfigService
    from tests.integration.standard.assistant.test_global_assistant_tasks import task_actor

    flow = media_tool_run(task_workspace)
    with flow.factory() as session:
        task_service(session, flow.settings)
        extra = AIModelConfigService(session).create(
            {
                "service_type": "image",
                "name": "Second image fixture",
                "model_key": "fixture",
                "provider": "ark",
                "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            }
        )
    with flow.factory.begin() as session:
        session.get(AIModelConfig, extra.id).row_version = 7
        session.get(AIModelConfig, extra.id).apikey = "test-only-private-credential"
        session.add(
            User(
                id=2,
                username="catalog-other",
                display_name="Other",
                email="other@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    with flow.factory() as session:
        session.info["actor"] = task_actor(2)
        foreign = AIModelConfigService(session).create(
            {
                "service_type": "image",
                "name": "Foreign image",
                "model_key": "foreign",
                "provider": "ark",
                "base_url": "https://model.invalid/private",
            }
        )
    _, transport, operations = scope_graph(
        task_workspace,
        flow.accepted,
        [
            ("catalog-1", "runtime__describe_creation_target", {"model_limit": 1}),
            (
                "catalog-2",
                "runtime__describe_creation_target",
                {"model_limit": 1, "model_offset": 1},
            ),
            ("catalog-limit", "runtime__describe_creation_target", {"model_limit": 21}),
        ],
        context=flow.context,
    )
    first, second, invalid = operations
    assert first.status == second.status == "succeeded"
    assert first.result["image_models"] == [
        {
            "id": flow.body["requests"][0]["model_config_id"],
            "name": "Assistant image fixture",
            "model_key": "fixture",
            "row_version": "1",
        }
    ]
    assert first.result["next_model_offset"] == 1
    assert second.result["image_models"][0]["id"] == str(extra.id)
    assert second.result["image_models"][0]["row_version"] == "7"
    assert second.result["next_model_offset"] is None and invalid.status == "failed"
    delivered = json.dumps(transport.main_requests, ensure_ascii=False)
    assert str(foreign.id) not in delivered and "test-only-private-credential" not in delivered
    assert "base_url" not in str(first.result) and "apikey" not in str(first.result)


def test_saved_sound_read_uses_only_this_task_created_lines_and_rechecks_access(task_workspace):
    from tests.integration.standard.assistant.test_global_assistant_tasks import (
        dialogue_audio_workspace,
        sound_text_revision,
        standard_tool_run,
    )

    refs, _model, assembly_id = dialogue_audio_workspace(task_workspace)
    factory, settings, _text_model = task_workspace
    _conversation, accepted, _wire, saved = standard_tool_run(
        task_workspace,
        "创建一条台词并读取实际保存成果",
        [
            {
                "sound_revision": sound_text_revision(task_workspace, refs[0]["project_id"]),
                "actions": [
                    {
                        "action": "create_sound_line",
                        "key": "opening",
                        "values": {"text": "本任务新建的台词。" * 100},
                    }
                ],
            }
        ],
        project_id=refs[0]["project_id"],
    )
    assert saved[0].status == "succeeded", saved[0].error
    arguments = {"operation_id": str(saved[0].id), "character_limit": 6000}
    context, wire, reads = scope_graph(
        task_workspace,
        accepted,
        [
            ("read-created", "runtime__read_task_results", arguments),
            ("guess-old-line", "runtime__read_task_results", {**arguments, "ids": ["line_2"]}),
        ],
    )
    assert reads[0].status == "succeeded" and reads[1].status == "failed"
    assert reads[1].error["code"] == "assistant_result_scope"
    assert reads[0].result["results"][0]["kind"] == "sound_line"
    delivered = json.dumps(wire.main_requests, ensure_ascii=False)
    assert "本任务新建的台词" in delivered
    assert "未选台词正文" not in delivered and "未选字幕正文" not in delivered
    line_id = saved[0].result_refs[0]["id"]
    with factory.begin() as session:
        sound = session.get(ProjectSound, assembly_id)
        sound.document = {
            **sound.document,
            "dialogue": [line for line in sound.document["dialogue"] if line["id"] != line_id],
        }
        sound.row_version += 1
    _, _, deleted = scope_graph(
        task_workspace,
        accepted,
        [
            ("read-deleted", "runtime__read_task_results", arguments),
        ],
        context=context,
    )
    assert deleted[0].status == "failed" and deleted[0].result.get("results") is None
