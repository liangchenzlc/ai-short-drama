from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.support.integration.identity import actor as fixture_actor

pytestmark = pytest.mark.integration


def test_concurrent_script_saves_keep_one_confirmed_current_version(db_session, mysql_engine):
    from short_drama.core.exceptions import Conflict
    from short_drama.domain import ProjectScript
    from short_drama.service.creation.writing.script.documents import ProjectWritingService
    from short_drama.service.projects.management.project import ProjectService

    project = ProjectService(db_session).create_project({"name": "Concurrent"})
    initial = ProjectWritingService(db_session).save_script(
        project.id, {"script_id": None, "content": "Original", "content_version": "1"}
    )
    barrier = Barrier(2)

    def save(content):
        with Session(
            mysql_engine, expire_on_commit=False, autoflush=False, info={"actor": fixture_actor()}
        ) as session:
            barrier.wait(timeout=10)
            try:
                ProjectWritingService(session).save_script(
                    project.id,
                    {
                        "script_id": initial["script"]["id"],
                        "content": content,
                        "content_version": initial["content_version"],
                    },
                )
                return "saved"
            except Conflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(save, ["A", "B"]))
    assert sorted(results) == ["conflict", "saved"]
    current = ProjectWritingService(db_session).get(project.id)
    assert current["content_version"] == "3"
    assert current["editing_script"]["content"] in {"A", "B"}
    assert current["confirmed_script_id"] == initial["script"]["id"]
    with db_session.begin():
        assert (
            db_session.scalar(
                select(func.count())
                .select_from(ProjectScript)
                .where(
                    ProjectScript.project_id == project.id,
                    ProjectScript.state == "confirmed",
                )
            )
            == 1
        )


def test_concurrent_first_media_create_keeps_one_slot(db_session, mysql_engine):
    from short_drama.core.exceptions import Conflict
    from short_drama.service.creation.storyboard.images.image import ShotImageService
    from short_drama.service.creation.storyboard.management.script import ShotScriptService
    from short_drama.service.media.files.files import MediaFileService
    from short_drama.service.projects.management.project import ProjectService

    project = ProjectService(db_session).create_project({"name": "Concurrent media"})
    episode = project
    shot = ShotScriptService(db_session).create({"project_id": episode.id, "position": 1})
    media_service = MediaFileService(db_session)
    db_session.info["resource_scope"] = (None, episode.id)
    media = [
        media_service.create({"format_code": "image/png", "storage_locator": f"race/{i}"})
        for i in range(2)
    ]
    db_session.info.pop("resource_scope")
    barrier = Barrier(2)

    def create(identifier):
        with Session(
            mysql_engine, expire_on_commit=False, autoflush=False, info={"actor": fixture_actor()}
        ) as session:
            barrier.wait(timeout=10)
            try:
                ShotImageService(session).create(
                    {
                        "project_id": episode.id,
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
