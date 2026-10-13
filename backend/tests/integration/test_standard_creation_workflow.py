from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import inspect, select

from scripts.standard_creation_ddl import apply_ddl
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.db.session import session_factory
from short_drama.domain import AIModelConfig, UserCreationDraft
from short_drama.domain.collaboration import ProjectMember, User
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.base import utcnow
from short_drama.service.episode_service import EpisodeService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.episode_writing_service import EpisodeWritingService
from short_drama.service.project_service import ProjectService
from short_drama.service.user_creation_draft_service import UserCreationDraftService

pytestmark = pytest.mark.integration


def scope(session):
    project = ProjectService(session).create_project({"name": "创作合同", "aspect": "16:9"})
    episode = EpisodeService(session).create_for_project(project.id, {"title": "第一集"})
    return project.id, episode.id


def identity(session, project, user_id=1):
    session.info["actor"] = ActorContext(
        user_id, "tester", "test@example.test", True, 1, "", "creation"
    )
    session.info["request_project"] = project
    session.info["resource_scope"] = (None, project)


def test_mysql_title_delta_and_legacy_format_clear_persist(db_session):
    project, episode = scope(db_session)
    storyboard = EpisodeStoryboardService(db_session)
    created = storyboard.create(
        project,
        episode,
        {
            "storyboard_version": "1",
            "title": "夜雨",
            "shot_size": "close_up",
            "script": "人物抬头。",
        },
        "mysql-name",
    )["shot"]
    writing = EpisodeWritingService(db_session)
    delta = {"ops": [{"insert": "第一场", "attributes": {"bold": True}}, {"insert": "\n\n\n"}]}
    saved = writing.save_script(
        project,
        episode,
        {
            "content_version": "1",
            "script_id": None,
            "content": "第一场\n\n",
            "editor_document": delta,
        },
    )
    db_session.expunge_all()
    assert storyboard.get(project, episode, created["id"])["shot"]["title"] == "夜雨"
    assert storyboard.get(project, episode, created["id"])["shot"]["shot_size"] == "close_up"
    assert writing.get(project, episode)["editing_script"]["editor_document"] == delta
    modified = writing.save_script(
        project,
        episode,
        {
            "content_version": saved["content_version"],
            "script_id": saved["script"]["id"],
            "content": "第一场\n\n",
            "editor_document": {"ops": [{"insert": "第一场\n\n\n"}]},
        },
    )
    assert int(modified["content_version"]) == int(saved["content_version"]) + 1
    legacy = writing.save_script(
        project,
        episode,
        {
            "content_version": modified["content_version"],
            "script_id": saved["script"]["id"],
            "content": "第一场\n\n",
        },
    )
    assert legacy["script"]["editor_document"] is None


def test_mysql_private_draft_conflict_serializes_two_editors_and_hides_other_owner(db_session):
    project, episode = scope(db_session)
    shot = EpisodeStoryboardService(db_session).create(
        project,
        episode,
        {
            "storyboard_version": "1",
            "title": "首镜",
            "script": "人物抬头。",
        },
        "mysql-draft",
    )["shot"]
    now = utcnow()
    with db_session.begin():
        db_session.add(
            User(
                id=2,
                username="draft_other",
                display_name="Other",
                email="o@example.test",
                password_hash="test-only",
                status="active",
                created_at=now,
            )
        )
        db_session.flush()
        db_session.add(
            ProjectMember(id=2, project_id=project, user_id=2, status="active", joined_at=now)
        )
        db_session.add(
            AIModelConfig(
                id=10,
                service_type="image",
                name="Test image",
                model_key="test",
                provider="test",
                base_url="https://invalid.test",
                apikey="test-only",
                enabled=1,
                is_default=0,
                is_deleted=0,
                row_version=1,
            )
        )
    identity(db_session, project)
    draft = UserCreationDraftService(db_session).create_for_scope(
        project,
        episode,
        {
            "object_id": shot["id"],
            "purpose": "storyboard_image",
            "model_config_id": "10",
            "input_text": "初稿",
        },
    )
    factory = session_factory(db_session.get_bind())

    def save(value):
        with factory() as session:
            identity(session, project)
            try:
                return UserCreationDraftService(session).update_for_scope(
                    project,
                    episode,
                    shot["id"],
                    "storyboard_image",
                    {"row_version": draft["row_version"], "input_text": value},
                )["input_text"]
            except WorkflowError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ("窗口A", "窗口B")))
    assert results.count("draft_version_conflict") == 1
    identity(db_session, project, 2)
    assert UserCreationDraftService(db_session).list_for_episode(project, episode) == []
    with db_session.begin():
        assert (
            db_session.scalar(
                select(UserCreationDraft).where(UserCreationDraft.id == int(draft["id"]))
            )
            is None
        )


def test_mysql_additive_upgrade_and_reentry_match_full_schema(migration_mysql_engine):
    with migration_mysql_engine.begin() as connection:
        assert connection.engine.url.database.endswith("_test")
        connection.exec_driver_sql("DROP TABLE user_creation_drafts")
        connection.exec_driver_sql(
            "ALTER TABLE shot_scripts DROP CHECK ck_shot_scripts_shot_size, "
            "DROP COLUMN shot_size, DROP COLUMN title"
        )
        connection.exec_driver_sql(
            "ALTER TABLE episode_scripts DROP CHECK ck_scripts_editor_document, "
            "DROP COLUMN editor_document"
        )
        statements = apply_ddl(connection)
        assert len(statements) == 8
        assert apply_ddl(connection) == []
        tables = inspect(connection)
        columns = {c["name"]: c for c in tables.get_columns("shot_scripts")}
        assert not columns["title"]["nullable"] and columns["shot_size"]["nullable"]
        assert str(columns["title"]["type"]) == "VARCHAR(255)"
        assert "editor_document" in {c["name"] for c in tables.get_columns("episode_scripts")}
        assert {i["name"] for i in tables.get_indexes("user_creation_drafts")} >= {
            "idx_user_creation_drafts_user_project",
            "idx_user_creation_drafts_user_episode",
        }
        connection.exec_driver_sql(
            "ALTER TABLE user_creation_drafts DROP INDEX idx_user_creation_drafts_user_episode"
        )
        assert len(apply_ddl(connection)) == 1
        assert apply_ddl(connection) == []


def test_mysql_kind_change_preserves_links_versions_and_character_history(db_session):
    project, episode = scope(db_session)
    assets = AssetLibraryService(db_session)
    asset, _ = assets.create(
        "episode", episode, project, {"kind": "prop", "name": "木偶"}, "mysql-kind"
    )
    storyboard = EpisodeStoryboardService(db_session)
    shot = storyboard.create(
        project,
        episode,
        {"storyboard_version": "1", "script": "木偶回头。", "asset_ids": [str(asset.id)]},
        "mysql-kind-shot",
    )["shot"]
    changed = assets.patch(
        asset.id,
        {"row_version": "1", "kind": "scene", "scene_time": "夜晚", "confirm_shared": True},
    )
    assert (changed.kind, changed.row_version) == ("scene", 2)
    assert storyboard.get(project, episode, shot["id"])["shot"]["asset_ids"] == [str(asset.id)]
    changed = assets.patch(
        asset.id,
        {"row_version": "2", "kind": "character", "scene_time": "", "confirm_shared": True},
    )
    assert changed.row_version == 3
    with pytest.raises(WorkflowError) as history:
        assets.patch(asset.id, {"row_version": "3", "kind": "prop"})
    assert history.value.code == "asset_kind_incompatible"
    with pytest.raises(WorkflowError) as stale:
        assets.patch(asset.id, {"row_version": "2", "kind": "character"})
    assert stale.value.code == "asset_version_conflict"
    identity(db_session, project, 2)
    with pytest.raises(NotFound):
        assets.patch(asset.id, {"row_version": "3", "kind": "prop"})


def test_mysql_draft_save_and_generation_freeze_share_lock_order(db_session):
    project, episode = scope(db_session)
    shot = EpisodeStoryboardService(db_session).create(
        project,
        episode,
        {"storyboard_version": "1", "title": "首镜", "script": "人物抬头。"},
        "mysql-freeze-shot",
    )["shot"]
    with db_session.begin():
        db_session.add(
            AIModelConfig(
                id=10,
                service_type="image",
                name="Test image",
                model_key="test",
                provider="test",
                base_url="https://invalid.test",
                apikey="test-only",
                enabled=1,
                is_default=0,
                is_deleted=0,
                row_version=1,
            )
        )
    identity(db_session, project)
    draft = UserCreationDraftService(db_session).create_for_scope(
        project,
        episode,
        {
            "object_id": shot["id"],
            "purpose": "storyboard_image",
            "model_config_id": "10",
            "input_text": "初稿",
        },
    )
    saver_locked, freezer_read = Event(), Event()
    factory = session_factory(db_session.get_bind())

    class Saver(UserCreationDraftService):
        def _scope(self, *args, **kwargs):
            result = super()._scope(*args, **kwargs)
            saver_locked.set()
            assert freezer_read.wait(10)
            return result

    class Freezer(UserCreationDraftService):
        def _scope(self, *args, **kwargs):
            freezer_read.set()
            return super()._scope(*args, **kwargs)

    def save():
        with factory() as session:
            identity(session, project)
            return Saver(session).update_for_scope(
                project,
                episode,
                shot["id"],
                "storyboard_image",
                {"row_version": "1", "input_text": "新输入"},
            )["row_version"]

    def freeze():
        assert saver_locked.wait(10)
        with factory() as session:
            identity(session, project)
            # 生成入口没有项目路由，尚未建立 request_project 隐式锁。
            session.info.pop("request_project")
            try:
                with session.begin():
                    Freezer(session).freeze_for_generation(
                        {
                            "source": {
                                "scene": "shot_image",
                                "shot_id": shot["id"],
                                "draft_id": draft["id"],
                                "draft_version": "1",
                            },
                            "parameters": {},
                        }
                    )
                return "accepted"
            except WorkflowError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved, frozen = pool.submit(save), pool.submit(freeze)
        assert saved.result(timeout=20) == "2"
        assert frozen.result(timeout=20) == "draft_version_conflict"
