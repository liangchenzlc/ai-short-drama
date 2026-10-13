"""角色正式准入与实际媒体复制；采用binding为可信夹具，不执行供应商生成。"""

import hashlib
import json
import os
import wave
from copy import deepcopy
from io import BytesIO
from uuid import uuid4

import pytest
from sqlalchemy import select

from short_drama.ai.canvas_video_adapters import BEEFAPI_SEEDANCE
from short_drama.domain import AIGenerationRecord, AsyncTask, MediaFile
from short_drama.domain.native_voice import CharacterVoice
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id
from tests.integration.test_canvas_generation_runtime import TASKS, admit, records, seed
from tests.integration.test_canvas_resources import ROOT, png
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_source_project_binding import business, seed_binding
from tests.integration.test_canvas_video_protocols import protocol_seed
from tests.integration.test_canvas_video_protocols import video_runtime as video_runtime
from tests.integration.test_identity_collaboration import account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1",
        reason="显式启用角色正式任务/真实副本验证",
    ),
]


def read(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def role_source(app, owner, project, path, *, policy):
    source = business(owner, "正式角色来源")
    seed_binding(app, project, source)
    asset = read(
        owner.post(
            f"/api/v1/projects/{source['id']}/assets",
            headers={"Idempotency-Key": uuid4().hex},
            json={
                "kind": "character",
                "name": "阿青",
                "description": "旧版青衣",
                "prompt": "保持青衣",
            },
        ),
        201,
    )
    content = png("purple")
    candidate = read(
        owner.post(
            f"/api/v1/assets/{asset['id']}/image-candidates/upload",
            files={"file": ("role.png", content, "image/png")},
        ),
        201,
    )
    adopted = read(
        owner.post(
            f"/api/v1/assets/{asset['id']}/confirm",
            json={
                "row_version": asset["row_version"],
                "media_id": candidate["media_id"],
                "expected_media_id": None,
            },
        )
    )
    initial = read(owner.get(path + "/my-document"))
    document = initial["source_document"]
    key = document["id"]
    catalog_path = f"/api/v1/canvas-runtime/canvas-projects/{key}/character-catalog"
    item = read(owner.get(catalog_path))["items"][0]
    original = item["representations"][0]["media_id"]
    normalized = read(
        owner.post(ROOT + "/normalize", json={"canvas_key": key, "resource_ids": [original]})
    )
    copied = normalized["resource_map"][original]
    assert copied != original
    assert owner.get(f"{ROOT}/{copied}/file").content == content
    document["nodes"].append(
        {
            "id": "saved-role",
            "type": "text",
            "title": item["name"],
            "position": {"x": 100, "y": 400},
            "width": 320,
            "height": 260,
            "metadata": {
                "workflowKind": "character",
                "characterAssetId": item["asset_id"],
                "characterVersionId": item["version_id"],
                "characterVersionPolicy": policy,
                "characterName": item["name"],
                "characterDefinition": item["definition"],
                "characterCoverUrl": f"{ROOT}/{copied}/file",
                "characterVisualStatus": "ready",
            },
        }
    )
    read(
        owner.post(
            path + "/commits",
            headers={"Idempotency-Key": uuid4().hex},
            json={"expected_row_version": initial["row_version"], "source_document": document},
        )
    )
    return source, adopted, item, copied, content


def advance(owner, asset):
    next_image = read(
        owner.post(
            f"/api/v1/assets/{asset['id']}/image-candidates/upload",
            files={"file": ("new.png", png("blue"), "image/png")},
        ),
        201,
    )
    return read(
        owner.post(
            f"/api/v1/assets/{asset['id']}/confirm",
            json={
                "row_version": asset["row_version"],
                "media_id": next_image["media_id"],
                "expected_media_id": asset["media_id"],
            },
        )
    )


def references(request, item, copied):
    request["input"]["referenceImages"] = [{"storageKey": "resource:" + copied}]
    request["input"]["metadata"]["resolvedCharacterVersions"] = [
        {"assetId": item["asset_id"], "versionId": item["version_id"]}
    ]


@pytest.mark.parametrize("policy", ["current", "pinned"])
def test_formal_character_admission_freezes_real_version_and_normalized_image(resource_app, policy):
    owner, user, project, path, request = seed(resource_app, "role_task_author")
    source, adopted, item, copied, content = role_source(
        resource_app, owner, project, path, policy=policy
    )
    model_path = "/api/v1/ai-model-configs/" + request["logicalModelId"]
    current = read(owner.get(model_path))
    profile = current["runtime_profile"]
    profile["capability_config"] = {"text": {"references": {"maxImages": 2, "maxImageBytes": 1024}}}
    read(
        owner.patch(
            model_path, json={"row_version": current["row_version"], "runtime_profile": profile}
        )
    )
    references(request, item, copied)
    if policy == "pinned":
        advance(owner, adopted)
    task = admit(owner, request)
    with resource_app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        frozen = deepcopy(record.request_data)
        role = frozen["canvas_character_references"][0]
        assert role["version_id"] == item["version_id"] and role["name"] == item["name"]
        assert (
            role["definition"] == item["definition"]
            and role["representations"][0]["media_id"] == item["representations"][0]["media_id"]
        )
        assert frozen["canvas_text_references"]["references"][0]["media_id"] == copied
        media = session.get(MediaFile, int(copied))
        assert (
            media.project_id == int(project["id"])
            and media.checksum_sha256 == hashlib.sha256(content).hexdigest()
        )
    member, identity = account(resource_app, "role_task_member")
    for scope in (source, project):
        join(resource_app, owner, member, scope["id"], identity["id"])
    member.headers["X-Canvas-Actor"] = identity["id"]
    assert member.get(f"{TASKS}/{task['id']}").status_code == 404
    assert member.get(f"{ROOT}/{copied}/file").content == content
    assert owner.post(TASKS, json=request).json()["id"] == task["id"]
    before = records(resource_app, user)
    changed = deepcopy(request)
    changed["input"]["metadata"].update(
        clientOperationId="invalid-version-" + uuid4().hex,
        resolvedCharacterVersions=[{"assetId": item["asset_id"], "versionId": "1"}],
    )
    assert owner.post(TASKS, json=changed).status_code == 409
    assert records(resource_app, user) == before


def voice_binding_fixture(app, user, source, asset):
    """仅在随机测试库播种已采用binding和已归档WAV，不伪装原供应商质量。"""
    settings = app[2].model_copy(
        update={"native_video_enabled": True, "audio_production_enabled": True}
    )
    app[0].state.settings = settings
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(bytes(153600))
    content = output.getvalue()
    model = read(
        user.post(
            "/api/v1/ai-model-configs",
            json={
                "service_type": "audio",
                "name": "测试已采用样音来源",
                "provider": "openai",
                "model_key": "gpt-4o-mini-tts",
                "base_url": "https://api.openai.com/v1",
                "apikey": "fixture-voice-only",
            },
        ),
        201,
    )
    author = read(user.get("/api/v1/auth/me"))["user"]["id"]
    task_id, record_id, media_id = next_id(), next_id(), next_id()
    stored = app[0].state.storage.put(
        settings.minio_audio_bucket,
        f"sample/{media_id}.wav",
        BytesIO(content),
        len(content),
        "audio/wav",
    )
    now = utcnow()
    with app[1].begin() as session:
        session.add(
            AsyncTask(
                id=task_id,
                project_id=int(source["id"]),
                initiated_by=int(author),
                service_type="audio",
                status="succeeded",
                idempotency_key=uuid4().hex,
                request_hash="a" * 64,
                message_status="idle",
                created_at=now,
                updated_at=now,
                finished_at=now,
            )
        )
        session.flush()
        session.add(
            AIGenerationRecord(
                id=record_id,
                task_id=task_id,
                config_id=int(model["id"]),
                call_no=1,
                config_snapshot={"private": "fixture"},
                request_data={"private": "fixture"},
                response_data={},
                status="succeeded",
                created_at=now,
            )
        )
        session.add(
            MediaFile(
                id=media_id,
                project_id=int(source["id"]),
                created_by=int(author),
                format_code="audio/wav",
                storage_locator=stored.storage_locator,
                original_name="sample.wav",
                byte_size=len(content),
                duration_ms=3200,
                checksum_sha256=hashlib.sha256(content).hexdigest(),
                published_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            CharacterVoice(
                project_id=int(source["id"]),
                asset_id=int(asset["id"]),
                row_version=1,
                record_id=record_id,
                media_id=media_id,
            )
        )
    return media_id, content


def test_formal_pinned_role_voice_freezes_actual_copy_and_rejects_changed_binding(video_runtime):
    runtime = video_runtime
    owner, user, project, path, request = protocol_seed(runtime, BEEFAPI_SEEDANCE)
    source, adopted, item, image, _ = role_source(
        runtime.app, owner, project, path, policy="pinned"
    )
    sample_id, audio = voice_binding_fixture(runtime.app, owner, source, adopted)
    copied = read(
        owner.post(
            ROOT + "/normalize",
            json={"canvas_key": request["projectId"], "resource_ids": [str(sample_id)]},
        )
    )["resource_map"][str(sample_id)]
    assert copied != str(sample_id) and owner.get(f"{ROOT}/{copied}/file").content == audio
    references(request, item, image)
    request["operation"] = "reference_to_video"
    request["input"]["referenceAudios"] = [{"storageKey": "resource:" + copied}]
    request["input"]["metadata"]["resolvedCharacterVoices"] = [
        {
            "assetId": item["asset_id"],
            "versionId": item["version_id"],
            "bindingVersion": "1",
            "sampleResourceId": str(sample_id),
            "voiceKey": "",
        }
    ]
    advance(owner, adopted)
    task = admit(owner, request)
    with runtime.app[1]() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task["id"]))
        )
        frozen = record.request_data
        sample = frozen["canvas_character_references"][0]["voice_sample"]
        assert sample["media_id"] == str(sample_id) and sample["reference_media_id"] == copied
        assert (
            sample["checksum_sha256"] == hashlib.sha256(audio).hexdigest()
            and sample["binding_version"] == "1"
        )
        assert "private" not in json.dumps(frozen["canvas_character_references"])
        assert frozen["input"]["audio_reference_media_ids"] == [copied]
    with runtime.app[1].begin() as session:
        session.get(CharacterVoice, (int(source["id"]), int(adopted["id"]))).row_version = 2
    before = records(runtime.app, user)
    changed = deepcopy(request)
    changed["input"]["metadata"]["clientOperationId"] = uuid4().hex
    rejected = owner.post(TASKS, json=changed)
    assert rejected.status_code == 409 and "canvas_character_voice_changed" in rejected.text
    assert records(runtime.app, user) == before
    assert not any(call["path"] == "/v1/videos" for call in runtime.calls)
