import threading

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from short_drama.core.exceptions import WorkflowError
from short_drama.service.episode_service import EpisodeService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.project_service import ProjectService

pytestmark = pytest.mark.integration


def seed(session):
    project = ProjectService(session).create({"name": "board", "aspect": "16:9"})
    episode = EpisodeService(session).create(
        {"project_id": project.id, "position": 1, "title": "one", "aspect": "16:9"}
    )
    result = EpisodeStoryboardService(session).create(
        project.id,
        episode.id,
        {"storyboard_version": "1", "script": "original"},
        "integration-shot",
    )
    return project.id, episode.id, result


def test_canonical_schema_contains_storyboard_and_asset_migration_contract(mysql_engine):
    inspector = inspect(mysql_engine)
    episode_columns = {column["name"] for column in inspector.get_columns("episodes")}
    shot_columns = {column["name"] for column in inspector.get_columns("shot_scripts")}
    image_columns = {column["name"] for column in inspector.get_columns("shot_images")}
    asset_columns = {column["name"] for column in inspector.get_columns("assets")}
    assert "storyboard_version" in episode_columns
    assert {
        "row_version",
        "image_settings",
        "deleted_at",
        "active_position",
        "creation_key",
        "creation_hash",
    } <= shot_columns
    assert "context_hash" in image_columns
    assert {
        "row_version",
        "state",
        "tags",
        "scene_time",
        "creation_key",
        "creation_hash",
    } <= asset_columns
    assert inspector.has_table("asset_image_candidates")
    indexes = {index["name"]: index for index in inspector.get_indexes("shot_scripts")}
    assert indexes["uk_shots_episode_active_position"]["unique"] is True
    assert "uk_shots_episode_position" not in indexes


def test_concurrent_same_row_version_has_one_winner(mysql_engine, db_session):
    project_id, episode_id, created = seed(db_session)
    shot_id = int(created["shot"]["id"])
    barrier = threading.Barrier(2)
    outcomes = []

    def update(value):
        with Session(mysql_engine, expire_on_commit=False) as session:
            barrier.wait()
            try:
                result = EpisodeStoryboardService(session).update(
                    project_id,
                    episode_id,
                    shot_id,
                    {"row_version": "1", "script": value},
                )
                outcomes.append(("saved", result["shot"]["row_version"]))
            except WorkflowError as error:
                outcomes.append((error.code, error.details["current_version"]))

    threads = [threading.Thread(target=update, args=(value,)) for value in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(name for name, _version in outcomes) == ["saved", "shot_version_conflict"]
    assert {str(version) for _name, version in outcomes} == {"2"}
    reloaded = EpisodeStoryboardService(db_session).get(project_id, episode_id, shot_id)
    assert reloaded["shot"]["row_version"] == "2"
    assert reloaded["storyboard_version"] == "3"
