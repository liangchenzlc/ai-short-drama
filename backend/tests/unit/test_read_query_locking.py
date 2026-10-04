"""Read SQL must avoid write mutexes; mutations retain their lock/version contract.

SQLite executes the business paths; MySQL compilation checks requested lock SQL.
Physical MySQL concurrency is exercised separately by integration tests.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from generation_fixtures import generation_session
from sqlalchemy import event, select
from sqlalchemy.dialects import mysql
from test_asset_extraction import extraction
from test_episode_assembly import body, export_body, flow
from test_episode_writing import setup

from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict
from short_drama.db import access as _access  # noqa: F401
from short_drama.domain import EpisodeRenderJob, EpisodeScript, MediaFile
from short_drama.domain.episode_sound import EpisodeSound
from short_drama.schemas.episode_sound import SoundDocument
from short_drama.service.base import utcnow
from short_drama.service.episode_service import EpisodeService
from short_drama.service.episode_sound_service import EpisodeSoundService


@contextmanager
def capture_sql(session):
    statements = []

    def observe(state):
        statements.append(str(state.statement.compile(dialect=mysql.dialect())))

    event.listen(session, "do_orm_execute", observe)
    try:
        yield statements
    finally:
        event.remove(session, "do_orm_execute", observe)


def actor_scope(session, project_id):
    session.info["actor"] = SimpleNamespace(user_id=1, request_id="read-lock-regression")
    session.info["request_project"] = int(project_id)


def test_writing_and_episode_reads_avoid_write_locks_but_save_keeps_version_mutex():
    with generation_session() as session:
        p, e, writing = setup(session)
        saved = writing.save_script(
            p, e, {"script_id": None, "content": "script", "content_version": "1"}
        )
        confirmed = writing.confirm(p, e, saved["script"]["id"], {"content_version": "2"})
        with session.begin():
            session.get(EpisodeScript, int(saved["script"]["id"])).created_by = 1
        actor_scope(session, p)
        with capture_sql(session) as reads:
            assert writing.get(p, e) == confirmed
            assert writing.candidates(p, e)["items"][0]["is_confirmed"]
            assert EpisodeService(session).get_for_project(p, e).id == e
            assert EpisodeService(session).list_for_project(p).total == 1
        assert reads and all("FOR UPDATE" not in sql for sql in reads)
        with capture_sql(session) as writes:
            writing.save_novel(p, e, {"content": "new", "content_version": "3"})
        locks = [sql for sql in writes if "FOR UPDATE" in sql]
        assert any("FROM projects" in sql for sql in locks)
        assert any("FROM episodes" in sql for sql in locks)
        with pytest.raises(Conflict):
            writing.save_novel(p, e, {"content": "stale", "content_version": "3"})
        assert writing.get(p, e)["novel"]["content"] == "new"


@pytest.mark.parametrize("with_audio", [False, True])
def test_assembly_sound_and_job_reads_avoid_locks_but_edit_keeps_assembly_mutex(with_audio):
    with generation_session() as session:
        f, assembly = flow(session)
        state = assembly.initialize(f.project, f.episode)
        job = assembly.export(f.project, f.episode, export_body(state), "lock-test-export")
        settings = Settings(_env_file=None, audio_production_enabled=True)
        # Production media references share the project's scope. This legacy fixture
        # predates actor enforcement, so align its seed before enabling the actor.
        with session.begin():
            for media in session.scalars(select(MediaFile)):
                media.scope_user_id, media.project_id = None, int(f.project)
                media.created_by, media.published_at = 1, utcnow()
            session.get(EpisodeRenderJob, int(job["id"])).initiated_by = 1
            if with_audio:
                session.add(
                    MediaFile(
                        id=900,
                        project_id=int(f.project),
                        format_code="audio/wav",
                        storage_locator=f"minio://{settings.minio_audio_bucket}/music.wav",
                        duration_ms=1000,
                        original_name="music.wav",
                        created_by=1,
                        published_at=utcnow(),
                    )
                )
                session.add(
                    EpisodeSound(
                        assembly_id=int(state["assembly"]["id"]),
                        row_version=1,
                        document=SoundDocument(
                            music={"media_id": "900", "trim_out_ms": 1000}
                        ).model_dump(mode="json"),
                        updated_at=utcnow(),
                    )
                )
        assembly.settings = settings
        actor_scope(session, f.project)
        storage = SimpleNamespace(presigned_get=lambda *_: "https://media.example.test/music.wav")
        sound = EpisodeSoundService(session, settings, storage)
        with capture_sql(session) as reads:
            assert assembly.get(f.project, f.episode)["clips"] == state["clips"]
            assert assembly.jobs(f.project, f.episode)["items"][0]["id"] == job["id"]
            assert assembly.job_action(f.project, f.episode, job["id"], "get")["id"] == job["id"]
            assert sound.get(f.project, f.episode)["row_version"] == int(with_audio)
        assert reads and all("FOR UPDATE" not in sql for sql in reads)
        payload = body(state)
        payload["clips"][0]["muted"] = True
        with capture_sql(session) as writes:
            assembly.edit(f.project, f.episode, payload)
        locks = [sql for sql in writes if "FOR UPDATE" in sql]
        assert any("FROM episodes" in sql for sql in locks)
        assert any("FROM episode_assemblies" in sql for sql in locks)


def test_extraction_read_is_unlocked_and_does_not_mutate_candidate_versions():
    with extraction() as (session, p, e, _writing, _sid, tid, service):
        actor_scope(session, p)
        with capture_sql(session) as reads:
            first = service.get(p, e, tid)
            second = service.get(p, e, tid)
        assert first == second and not first["stale"]
        assert reads and all("FOR UPDATE" not in sql for sql in reads)
