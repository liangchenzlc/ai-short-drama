import threading

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from short_drama.core.exceptions import WorkflowError
from short_drama.service.project_service import ProjectService
from short_drama.service.project_storyboard_service import ProjectStoryboardService

pytestmark = pytest.mark.integration


def seed(session):
    project = ProjectService(session).create_project({"name": "board"})
    episode = ProjectService(session).get(project.id)
    result = ProjectStoryboardService(session).create(
        episode.id,
        {"storyboard_version": "1", "script": "original"},
        "integration-shot",
    )
    return episode.id, result


def test_canonical_schema_contains_storyboard_and_asset_migration_contract(mysql_engine):
    inspector = inspect(mysql_engine)
    episode_columns = {column["name"] for column in inspector.get_columns("projects")}
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
    assert indexes["uk_shots_creation_active_position"]["unique"] is True
    assert "uk_shots_project_position" not in indexes


def test_concurrent_same_row_version_has_one_winner(mysql_engine, db_session):
    project_id, created = seed(db_session)
    shot_id = int(created["shot"]["id"])
    barrier = threading.Barrier(2)
    outcomes = []

    def update(value):
        with Session(mysql_engine, expire_on_commit=False) as session:
            barrier.wait()
            try:
                result = ProjectStoryboardService(session).update(
                    project_id,
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
    reloaded = ProjectStoryboardService(db_session).get(project_id, shot_id)
    assert reloaded["shot"]["row_version"] == "2"
    assert reloaded["storyboard_version"] == "3"


def test_concurrent_delete_and_edit_have_one_winner(mysql_engine, db_session):
    project_id, created = seed(db_session)
    shot_id = created["shot"]["id"]
    barrier = threading.Barrier(2)
    outcomes = []

    def change(operation):
        with Session(mysql_engine, expire_on_commit=False) as session:
            barrier.wait(timeout=10)
            service = ProjectStoryboardService(session)
            try:
                if operation == "delete":
                    service.delete(
                        project_id,
                        shot_id,
                        {"row_version": "1", "storyboard_version": created["storyboard_version"]},
                    )
                else:
                    service.update(project_id, shot_id, {"row_version": "1", "script": "edited"})
                outcomes.append(operation)
            except WorkflowError as error:
                outcomes.append(error.code)

    threads = [
        threading.Thread(target=change, args=(operation,)) for operation in ("edit", "delete")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)
        assert not thread.is_alive()
    assert len(outcomes) == 2
    assert sum(outcome in {"edit", "delete"} for outcome in outcomes) == 1
    assert set(outcomes) <= {"edit", "delete", "shot_archived", "storyboard_version_conflict"}
    current = ProjectStoryboardService(db_session).get(project_id, shot_id)
    assert current["shot"]["row_version"] == "2" and current["storyboard_version"] == "3"
    assert (current["shot"]["deleted_at"] is not None) == ("delete" in outcomes)


@pytest.mark.parametrize("deleted_index", [0, 1, 3], ids=["first", "middle", "last"])
def test_delete_keeps_continuous_navigation_and_existing_image(db_session, deleted_index):
    from short_drama.domain import MediaFile
    from short_drama.service.shot_image_service import ShotImageService

    project_id, first = seed(db_session)
    service = ProjectStoryboardService(db_session)
    created = [first["shot"]]
    version = first["storyboard_version"]
    for index in range(1, 4):
        result = service.create(
            project_id, {"storyboard_version": version, "script": f"shot-{index}"}, f"shot-{index}"
        )
        created.append(result["shot"])
        version = result["storyboard_version"]
    # Display order intentionally differs from primary-key order, exercising MySQL's unique index.
    service.reorder(
        project_id,
        {"storyboard_version": version, "shot_ids": [shot["id"] for shot in reversed(created)]},
    )
    before = service.list(project_id)
    adopted = before["items"][(deleted_index + 1) % 4]
    with db_session.begin():
        db_session.add(
            MediaFile(
                id=9001,
                project_id=project_id,
                format_code="image/png",
                storage_locator="minio://images/keep.png",
                original_name="keep.png",
            )
        )
    ShotImageService(db_session).create(
        {"project_id": project_id, "shot_id": adopted["id"], "media_id": "9001", "aspect": "16:9"}
    )
    before = service.list(project_id)
    deleted = before["items"][deleted_index]
    result = service.delete(
        project_id,
        deleted["id"],
        {"row_version": deleted["row_version"], "storyboard_version": before["storyboard_version"]},
    )
    after = service.list(project_id)
    assert after["storyboard_version"] == str(int(before["storyboard_version"]) + 1)
    assert result == {
        "deleted_id": deleted["id"],
        "storyboard_version": after["storyboard_version"],
    }
    assert [shot["position"] for shot in after["items"]] == [1, 2, 3]
    assert after["total"] == 3
    old = {shot["id"]: shot for shot in before["items"]}
    for shot in after["items"]:
        previous = old[shot["id"]]
        assert shot["context_hash"] == previous["context_hash"]
        assert shot["video_context_hash"] == previous["video_context_hash"]
        assert int(shot["row_version"]) == int(previous["row_version"]) + (
            previous["position"] != shot["position"]
        )
        if shot["position"] < after["total"]:
            assert any(item["position"] == shot["position"] + 1 for item in after["items"])
        if shot["position"] > 1:
            assert any(item["position"] == shot["position"] - 1 for item in after["items"])
    kept = next(shot for shot in after["items"] if shot["id"] == adopted["id"])
    assert kept["image"]["media_id"] == "9001" and kept["image"]["is_stale"] is False
    appended = service.create(
        project_id, {"storyboard_version": after["storyboard_version"]}, "append-after-delete"
    )
    assert appended["shot"]["position"] == 4


def test_delete_rolls_back_after_temporary_positions_are_flushed(db_session):
    from sqlalchemy import event, select

    from short_drama.domain import ShotScript

    project_id, first = seed(db_session)
    service = ProjectStoryboardService(db_session)
    service.create(project_id, {"storyboard_version": "2", "script": "next"}, "rollback-next")
    before = service.list(project_id)

    def fail_temporary_flush(session, _context):
        if any(
            isinstance(row, ShotScript) and row.position > 2
            for row in session.identity_map.values()
        ):
            raise RuntimeError("injected temporary-position flush failure")

    event.listen(db_session, "after_flush", fail_temporary_flush)
    try:
        with pytest.raises(RuntimeError, match="injected temporary-position"):
            service.delete(
                project_id,
                first["shot"]["id"],
                {"row_version": "1", "storyboard_version": before["storyboard_version"]},
            )
    finally:
        event.remove(db_session, "after_flush", fail_temporary_flush)
    after = service.list(project_id)
    assert after["storyboard_version"] == before["storyboard_version"]
    assert [(row["id"], row["position"], row["row_version"]) for row in after["items"]] == [
        (row["id"], row["position"], row["row_version"]) for row in before["items"]
    ]
    with db_session.begin():
        assert all(row.deleted_at is None for row in db_session.scalars(select(ShotScript)))
