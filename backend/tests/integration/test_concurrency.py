from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def test_concurrent_confirmation_keeps_one_confirmed_script(db_session, mysql_engine):
    from short_drama.domain import EpisodeScript
    from short_drama.service.episode_script_service import EpisodeScriptService
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.project_service import ProjectService

    project = ProjectService(db_session).create(
        {
            "name": "Concurrent",
            "aspect": "16:9",
        }
    )
    episode = EpisodeService(db_session).create(
        {
            "project_id": project.id,
            "position": 1,
            "title": "One",
            "aspect": "16:9",
        }
    )
    scripts = EpisodeScriptService(db_session)
    first = scripts.create({"episode_id": episode.id, "position": 1, "content": "A"})
    second = scripts.create({"episode_id": episode.id, "position": 2, "content": "B"})
    barrier = Barrier(2)

    def confirm(identifier):
        with Session(mysql_engine, expire_on_commit=False, autoflush=False) as session:
            barrier.wait(timeout=10)
            return EpisodeScriptService(session).confirm(identifier)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, [first.id, second.id]))
    assert all(result.state == "confirmed" for result in results)
    with db_session.begin():
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(EpisodeScript)
                .where(
                    EpisodeScript.episode_id == episode.id,
                    EpisodeScript.state == "confirmed",
                )
            )
            == 1
        )


def test_concurrent_first_media_create_keeps_one_slot(db_session, mysql_engine):
    from short_drama.core.exceptions import Conflict
    from short_drama.service.episode_service import EpisodeService
    from short_drama.service.media_file_service import MediaFileService
    from short_drama.service.project_service import ProjectService
    from short_drama.service.shot_image_service import ShotImageService
    from short_drama.service.shot_script_service import ShotScriptService

    project = ProjectService(db_session).create(
        {
            "name": "Concurrent media",
            "aspect": "16:9",
        }
    )
    episode = EpisodeService(db_session).create(
        {
            "project_id": project.id,
            "position": 1,
            "title": "One",
            "aspect": "16:9",
        }
    )
    shot = ShotScriptService(db_session).create({"episode_id": episode.id, "position": 1})
    media_service = MediaFileService(db_session)
    media = [
        media_service.create({"format_code": "image/png", "storage_locator": f"race/{i}"})
        for i in range(2)
    ]
    barrier = Barrier(2)

    def create(identifier):
        with Session(mysql_engine, expire_on_commit=False, autoflush=False) as session:
            barrier.wait(timeout=10)
            try:
                ShotImageService(session).create(
                    {
                        "episode_id": episode.id,
                        "shot_id": shot.id,
                        "media_id": identifier,
                        "aspect": "16:9",
                    }
                )
                return "created"
            except Conflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(create, [item.id for item in media]))
    assert sorted(outcomes) == ["conflict", "created"]
    assert ShotImageService(db_session).list().total == 1
