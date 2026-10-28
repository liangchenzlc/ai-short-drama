import base64
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SAWarning

from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import BusinessError, Conflict, NotFound
from short_drama.domain import AIModelConfig

pytestmark = pytest.mark.integration


def seed(session):
    from short_drama.service.creation.writing.script.documents import ProjectWritingService
    from short_drama.service.projects.management.project import ProjectService

    project = ProjectService(session).create_project({"name": "Project"})
    saved = ProjectWritingService(session).save_script(
        project.id, {"script_id": None, "content": "Text", "content_version": "1"}
    )
    return project, SimpleNamespace(id=int(saved["script"]["id"]))


def test_model_encryption_versions_defaults_and_soft_delete(db_session):
    from short_drama.service.ai_config.management.models import AIModelConfigService

    cipher = KeyCipher(base64.b64encode(b"m" * 32).decode())
    service = AIModelConfigService(db_session, cipher=cipher)
    payload = {"service_type": "text", "name": "Model", "provider": "provider", "model_key": "m"}
    first = service.create({**payload, "apikey": "private-api-key"})
    second = service.create(payload)
    assert first.has_api_key and "apikey" not in first.model_dump()
    with db_session.begin():
        ciphertext = db_session.scalar(
            select(AIModelConfig.apikey).where(AIModelConfig.id == first.id)
        )
        assert ciphertext != "private-api-key"
        assert cipher.decrypt(ciphertext) == "private-api-key"
    first = service.set_default(first.id, first.row_version)
    assert first.is_default == 1
    second = service.set_default(second.id, second.row_version)
    assert service.get(first.id).is_default == 0
    with pytest.raises(Conflict):
        service.update(first.id, {"name": "stale", "row_version": 1})
    disabled = service.update(second.id, {"enabled": 0, "row_version": second.row_version})
    assert disabled.is_default == 0 and disabled.row_version == second.row_version + 1
    unchanged = service.update(disabled.id, {"enabled": 0, "row_version": disabled.row_version})
    assert unchanged.updated_at == disabled.updated_at
    service.delete(first.id, service.get(first.id).row_version)
    with pytest.raises(NotFound):
        service.get(first.id)
    assert service.list().total == 1


def test_script_save_keeps_identity_and_confirms_current_content(db_session):
    from short_drama.service.creation.writing.script.documents import ProjectWritingService

    project, script = seed(db_session)
    service = ProjectWritingService(db_session)
    current = service.get(project.id)
    saved = service.save_script(
        project.id,
        {"script_id": script.id, "content": "Text", "content_version": current["content_version"]},
    )
    assert saved["script"] == current["editing_script"]
    changed = service.save_script(
        project.id,
        {"script_id": script.id, "content": "text", "content_version": saved["content_version"]},
    )
    assert changed["script"]["id"] == str(script.id)
    assert changed["script"]["content"] == "text"
    assert changed["confirmed_script_id"] == str(script.id)


def test_record_batch_consistency_retries_and_same_project_rollback(db_session):
    from short_drama.service.creation.storyboard.management.records import ScriptShotRecordService
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.projects.management.project import ProjectService

    project, script = seed(db_session)
    shots = ShotScriptService(db_session)
    first = shots.create({"project_id": project.id, "position": 1})
    second = shots.create({"project_id": project.id, "position": 2})
    other_project = ProjectService(db_session).create_project({"name": "Other"})
    foreign = shots.create({"project_id": other_project.id, "position": 1})
    service = ScriptShotRecordService(db_session)
    rows = [
        {"script_id": script.id, "shot_id": output.id, "batch_id": 100}
        for output in (first, second)
    ]
    with pytest.raises(BusinessError):
        service.create_batch([rows[0], {**rows[1], "shot_id": foreign.id}])
    assert service.list().total == 0
    result = service.create_batch(rows)
    assert result[0].created_at == result[1].created_at
    assert [row.id for row in service.create_batch(rows)] == [row.id for row in result]
    with pytest.raises(Conflict):
        service.create_batch(rows[:1])
    with pytest.raises(BusinessError):
        service.update(result[0].id, {"script_id": 123})
    assert service.list().total == 2


def test_script_shot_records_keep_model_reference_after_soft_delete(db_session):
    from short_drama.service.ai_config.management.models import AIModelConfigService
    from short_drama.service.creation.storyboard.management.records import ScriptShotRecordService
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.generations.results.generation import GenerationService

    project, script = seed(db_session)
    models = AIModelConfigService(db_session)
    model = models.create({"service_type": "text", "name": "M", "model_key": "m", "provider": "p"})
    shot = ShotScriptService(db_session).create({"project_id": project.id, "position": 1})
    service = ScriptShotRecordService(db_session)
    payload = {"script_id": script.id, "shot_id": shot.id, "batch_id": 101, "model_id": model.id}
    record = service.create(payload)
    models.delete(model.id, model.row_version)
    assert service.get(record.id).model_id == model.id
    assert service.create(payload).id == record.id
    historical_shot = ShotScriptService(db_session).create(
        {"project_id": project.id, "position": 2}
    )
    historical = service.create({**payload, "shot_id": historical_shot.id, "batch_id": 102})
    assert historical.model_id == model.id
    with pytest.raises(BusinessError):
        GenerationService(db_session).generate_shots(script.id, ["new output"], model_id=model.id)


def test_generation_writes_outputs_and_records_atomically_and_replays(db_session):
    from short_drama.service.creation.storyboard.management.records import ScriptShotRecordService
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.generations.results.generation import GenerationService

    _, script = seed(db_session)
    service = GenerationService(db_session)
    batch_id = service.allocate_batch_id()
    batch = service.generate_shots(script.id, ["first", "second"], batch_id=batch_id)
    assert batch.batch_id == batch_id
    assert len(batch.outputs) == len(batch.records) == 2
    assert [output.script for output in batch.outputs] == ["first", "second"]
    assert all(output.deleted_at is None for output in batch.outputs)
    assert len({row.created_at for row in [*batch.outputs, *batch.records]}) == 1
    replay = service.generate_shots(script.id, ["first", "second"], batch_id=batch_id)
    assert [row.id for row in replay.outputs] == [row.id for row in batch.outputs]
    with pytest.raises(Conflict):
        service.generate_shots(script.id, ["changed", "second"], batch_id=batch_id)
    assert ShotScriptService(db_session).list().total == 2
    assert ScriptShotRecordService(db_session).list().total == 2


def test_generation_missing_source_rejects_without_outputs(db_session):
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.generations.results.generation import GenerationService

    seed(db_session)
    with pytest.raises(BusinessError):
        GenerationService(db_session).generate_shots(2**64 - 1, ["output"])
    assert ShotScriptService(db_session).list().total == 0


def test_generation_output_write_failure_rolls_back_entire_batch(db_session, monkeypatch):
    from short_drama.service.creation.storyboard.management.records import ScriptShotRecordService
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.generations.results.generation import GenerationService

    _, script = seed(db_session)
    # The second output collides at the database after the first output has flushed.
    monkeypatch.setattr("short_drama.dao.base.next_id", lambda: 8001)
    with pytest.warns(SAWarning, match="conflicts with persistent instance"):
        with pytest.raises(Conflict):
            GenerationService(db_session).generate_shots(script.id, ["one", "two"], batch_id=8002)
    assert ShotScriptService(db_session).list().total == 0
    assert ScriptShotRecordService(db_session).list().total == 0
