import base64

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SAWarning

from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import BusinessError, Conflict, NotFound
from short_drama.domain import AIModelConfig

pytestmark = pytest.mark.integration


def seed(session):
    from short_drama.service.episode_novel_service import EpisodeNovelService
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.project_service import ProjectService

    project = ProjectService(session).create(
        {"name": "Project", "aspect": "16:9", "target_ms": 1000}
    )
    episode = EpisodeService(session).create(
        {"project_id": project.id, "title": "First", "position": 1, "aspect": "16:9"}
    )
    novel = EpisodeNovelService(session).create({"episode_id": episode.id, "content": "Novel"})
    return project, episode, novel


def test_model_encryption_versions_defaults_and_soft_delete(db_session):
    from short_drama.service.ai_model_config_service import AIModelConfigService

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


def test_confirm_script_content_noop_case_change_and_reorder(db_session):
    from short_drama.service.episode_script_service import EpisodeScriptService

    _, episode, _ = seed(db_session)
    service = EpisodeScriptService(db_session)
    first = service.create({"episode_id": episode.id, "position": 1, "content": "Text"})
    second = service.create({"episode_id": episode.id, "position": 2, "content": "Other"})
    confirmed = service.confirm(first.id)
    assert confirmed.state == "confirmed"
    assert service.confirm(first.id).updated_at == confirmed.updated_at
    assert service.update(first.id, {"content": "Text"}).updated_at == confirmed.updated_at
    service.reorder(episode.id, [second.id, first.id])
    assert service.get(first.id).state == "confirmed"
    assert service.update(first.id, {"content": "text"}).state == "unconfirmed"
    service.confirm(first.id)
    service.confirm(second.id)
    assert service.get(first.id).state == "unconfirmed"
    assert service.get(second.id).state == "confirmed"


def test_record_batch_consistency_retries_and_same_episode_rollback(db_session):
    from short_drama.service.episode_script_service import EpisodeScriptService
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.novel_script_record_service import NovelScriptRecordService

    project, episode, novel = seed(db_session)
    scripts = EpisodeScriptService(db_session)
    first = scripts.create({"episode_id": episode.id, "position": 1})
    second = scripts.create({"episode_id": episode.id, "position": 2})
    other_episode = EpisodeService(db_session).create(
        {"project_id": project.id, "title": "Other", "position": 2, "aspect": "16:9"}
    )
    foreign = scripts.create({"episode_id": other_episode.id, "position": 1})
    service = NovelScriptRecordService(db_session)
    rows = [
        {"novel_id": novel.id, "script_id": output.id, "batch_id": 100}
        for output in (first, second)
    ]
    with pytest.raises(BusinessError):
        service.create_batch([rows[0], {**rows[1], "script_id": foreign.id}])
    assert service.list().total == 0
    result = service.create_batch(rows)
    assert result[0].created_at == result[1].created_at
    assert [row.id for row in service.create_batch(rows)] == [row.id for row in result]
    with pytest.raises(Conflict):
        service.create_batch(rows[:1])
    with pytest.raises(BusinessError):
        service.update(result[0].id, {"novel_id": 123})
    assert service.list().total == 2


def test_script_shot_records_keep_model_reference_after_soft_delete(db_session):
    from short_drama.service.ai_model_config_service import AIModelConfigService
    from short_drama.service.episode_script_service import EpisodeScriptService
    from short_drama.service.generation_service import GenerationService
    from short_drama.service.script_shot_record_service import ScriptShotRecordService
    from short_drama.service.shot_script_service import ShotScriptService

    _, episode, _ = seed(db_session)
    models = AIModelConfigService(db_session)
    model = models.create({"service_type": "text", "name": "M", "model_key": "m", "provider": "p"})
    script = EpisodeScriptService(db_session).create({"episode_id": episode.id, "position": 1})
    shot = ShotScriptService(db_session).create({"episode_id": episode.id, "position": 1})
    service = ScriptShotRecordService(db_session)
    payload = {"script_id": script.id, "shot_id": shot.id, "batch_id": 101, "model_id": model.id}
    record = service.create(payload)
    models.delete(model.id, model.row_version)
    assert service.get(record.id).model_id == model.id
    assert service.create(payload).id == record.id
    historical_shot = ShotScriptService(db_session).create(
        {"episode_id": episode.id, "position": 2}
    )
    historical = service.create({**payload, "shot_id": historical_shot.id, "batch_id": 102})
    assert historical.model_id == model.id
    with pytest.raises(BusinessError):
        GenerationService(db_session).generate_shots(script.id, ["new output"], model_id=model.id)


def test_generation_writes_outputs_and_records_atomically_and_replays(db_session):
    from short_drama.service.episode_script_service import EpisodeScriptService
    from short_drama.service.generation_service import GenerationService
    from short_drama.service.novel_script_record_service import NovelScriptRecordService

    _, _, novel = seed(db_session)
    service = GenerationService(db_session)
    batch_id = service.allocate_batch_id()
    batch = service.generate_scripts(novel.id, ["first", "second"], batch_id=batch_id)
    assert batch.batch_id == batch_id
    assert len(batch.outputs) == len(batch.records) == 2
    assert all(output.state == "unconfirmed" for output in batch.outputs)
    assert len({row.created_at for row in [*batch.outputs, *batch.records]}) == 1
    replay = service.generate_scripts(novel.id, ["first", "second"], batch_id=batch_id)
    assert [row.id for row in replay.outputs] == [row.id for row in batch.outputs]
    with pytest.raises(Conflict):
        service.generate_scripts(novel.id, ["changed", "second"], batch_id=batch_id)
    assert EpisodeScriptService(db_session).list().total == 2
    assert NovelScriptRecordService(db_session).list().total == 2
    shots = service.generate_shots(batch.outputs[0].id, ["shot one", "shot two"])
    assert [output.script for output in shots.outputs] == ["shot one", "shot two"]
    assert len(shots.records) == 2


def test_generation_empty_source_rejects_without_outputs(db_session):
    from short_drama.service.episode_novel_service import EpisodeNovelService
    from short_drama.service.episode_script_service import EpisodeScriptService
    from short_drama.service.generation_service import GenerationService

    _, _, novel = seed(db_session)
    EpisodeNovelService(db_session).update(novel.id, {"content": "  "})
    with pytest.raises(BusinessError):
        GenerationService(db_session).generate_scripts(novel.id, ["output"])
    assert EpisodeScriptService(db_session).list().total == 0


def test_generation_output_write_failure_rolls_back_entire_batch(db_session, monkeypatch):
    from short_drama.service.episode_script_service import EpisodeScriptService
    from short_drama.service.generation_service import GenerationService
    from short_drama.service.novel_script_record_service import NovelScriptRecordService

    _, _, novel = seed(db_session)
    # The second output collides at the database after the first output has flushed.
    monkeypatch.setattr("short_drama.dao.base.next_id", lambda: 8001)
    with pytest.warns(SAWarning, match="conflicts with persistent instance"):
        with pytest.raises(Conflict):
            GenerationService(db_session).generate_scripts(novel.id, ["one", "two"], batch_id=8002)
    assert EpisodeScriptService(db_session).list().total == 0
    assert NovelScriptRecordService(db_session).list().total == 0
