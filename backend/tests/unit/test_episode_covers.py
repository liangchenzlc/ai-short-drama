from datetime import datetime

import pytest
from generation_fixtures import generation_session
from sqlalchemy import event

from short_drama.core.exceptions import NotFound
from short_drama.db.access import scope_of
from short_drama.domain import MediaFile
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService
from short_drama.service.shot_image_service import ShotImageService
from short_drama.service.shot_script_service import ShotScriptService


class DisplayStorage:
    def __init__(self):
        self.calls = []

    def download_url(self, locator):
        self.calls.append(locator)
        return f"https://display.example.test/{locator}"


def add_image(session, project_id, media_id):
    with session.begin():
        media = MediaFile(
            id=media_id,
            project_id=project_id,
            format_code="image/png",
            storage_locator=f"minio://images/{media_id}.png",
            original_name="cover.png",
            created_at=datetime(2026, 1, 1),
        )
        session.add(media)
        assert scope_of(session, media) == (None, project_id)


def adopt(session, episode_id, shot_id, media_id):
    return ShotImageService(session).create(
        {"episode_id": episode_id, "shot_id": shot_id, "media_id": media_id, "aspect": "16:9"}
    )


def test_cover_uses_only_first_active_shots_adopted_image_and_reflects_replacement():
    with generation_session() as session:
        project = ProjectService(session).create_project({"name": "story", "aspect": "16:9"})
        storage = DisplayStorage()
        episodes = EpisodeService(session, storage)
        episode = episodes.create_for_project(project.id, {"title": "first"})
        assert episodes.get_for_project(project.id, episode.id).cover_url is None
        shots = ShotScriptService(session)
        first = shots.create({"episode_id": episode.id, "position": 1, "script": "first"})
        second = shots.create({"episode_id": episode.id, "position": 2, "script": "second"})
        add_image(session, project.id, 1001)
        add_image(session, project.id, 1002)
        second_image = adopt(session, episode.id, second.id, 1002)
        assert episodes.list_for_project(project.id).items[0].cover_url is None
        current = adopt(session, episode.id, first.id, 1001)
        assert episodes.get_for_project(project.id, episode.id).cover_url.endswith("1001.png")
        ShotImageService(session).update(current.id, {"media_id": 1002})
        assert episodes.list_for_project(project.id).items[0].cover_url.endswith("1002.png")
        shots.delete(first.id)
        assert episodes.get_for_project(project.id, episode.id).cover_url.endswith("1002.png")
        ShotImageService(session).delete(second_image.id)
        assert episodes.get_for_project(project.id, episode.id).cover_url is None


def test_episode_page_batches_cover_query_and_excludes_other_project_media():
    with generation_session() as session:
        projects = ProjectService(session)
        project = projects.create_project({"name": "story", "aspect": "16:9"})
        other = projects.create_project({"name": "other", "aspect": "16:9"})
        storage = DisplayStorage()
        episodes = EpisodeService(session, storage)
        for index in range(3):
            episode = episodes.create_for_project(project.id, {"title": f"episode {index}"})
            shot = ShotScriptService(session).create(
                {"episode_id": episode.id, "position": 1, "script": "first"}
            )
            add_image(session, other.id if index == 2 else project.id, 1001 + index)
            adopt(session, episode.id, shot.id, 1001 + index)
        selects = []

        def capture(_connection, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(session.get_bind(), "before_cursor_execute", capture)
        try:
            page = episodes.list_for_project(project.id)
        finally:
            event.remove(session.get_bind(), "before_cursor_execute", capture)
        assert page.total == 3
        assert [bool(item.cover_url) for item in page.items] == [True, True, False]
        assert len([statement for statement in selects if "JOIN shot_images" in statement]) == 1
        assert len(storage.calls) == 2
        with pytest.raises(NotFound):
            episodes.get_for_project(other.id, episode.id)
