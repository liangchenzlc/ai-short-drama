from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest
from sqlalchemy import inspect, select
from test_asset_image_generation import flow as flow

from scripts.standard_creation_ddl import apply_ddl
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.db.session import session_factory
from short_drama.domain import AIModelConfig, Asset, MediaFile, UserCreationDraft
from short_drama.domain.collaboration import ProjectMember, User
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.base import utcnow
from short_drama.service.episode_service import EpisodeService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.episode_writing_service import EpisodeWritingService
from short_drama.service.project_service import ProjectService
from short_drama.service.shot_image_service import ShotImageService
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


@pytest.mark.parametrize("kind", ["novel", "script", "asset", "draft", "shot"])
def test_mysql_unchanged_save_fences_a_delayed_content_update(db_session, kind):
    project, episode = scope(db_session)
    identity(db_session, project)
    writing = EpisodeWritingService(db_session)
    if kind == "novel":
        original = writing.save_novel(project, episode, {"content_version": "1", "content": "base"})
    elif kind == "script":
        original = writing.save_script(
            project, episode, {"content_version": "1", "content": "base", "script_id": None}
        )
    elif kind == "asset":
        asset, _ = AssetLibraryService(db_session).create(
            "episode", episode, project, {"kind": "prop", "name": "base"}, "fence-asset"
        )
        original = {"row_version": str(asset.row_version), "id": str(asset.id)}
    elif kind == "draft":
        original = UserCreationDraftService(db_session).create_for_scope(
            project,
            episode,
            {"object_id": str(episode), "purpose": "novel_adaptation", "input_text": "base"},
        )
    else:
        original = EpisodeStoryboardService(db_session).create(
            project, episode, {"storyboard_version": "1", "script": "base"}, "fence-shot"
        )["shot"]
    version = original.get("content_version", original.get("row_version"))

    def save(session, content):
        if kind == "novel":
            result = EpisodeWritingService(session).save_novel(
                project, episode, {"content_version": version, "content": content}
            )
            return result["content_version"], result["novel"]["content"]
        if kind == "script":
            result = EpisodeWritingService(session).save_script(
                project,
                episode,
                {
                    "content_version": version,
                    "script_id": original["script"]["id"],
                    "content": content,
                },
            )
            return result["content_version"], result["script"]["content"]
        if kind == "asset":
            result = AssetLibraryService(session).patch(
                original["id"], {"row_version": version, "name": content}
            )
            return str(result.row_version), result.name
        if kind == "draft":
            result = UserCreationDraftService(session).update_for_scope(
                project,
                episode,
                str(episode),
                "novel_adaptation",
                {"row_version": version, "input_text": content},
            )
            return result["row_version"], result["input_text"]
        result = EpisodeStoryboardService(session).update(
            project, episode, original["id"], {"row_version": version, "script": content}
        )["shot"]
        return result["row_version"], result["script"]

    factory = session_factory(db_session.get_bind())
    prepared, release = Event(), Event()

    def delayed_save():
        with factory() as session:
            identity(session, project)
            prepared.set()
            assert release.wait(timeout=15)
            try:
                save(session, "delayed obsolete edit")
            except (Conflict, WorkflowError) as error:
                return error.status_code
            return "overwrote final input"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(delayed_save)
        try:
            assert prepared.wait(timeout=15)
            assert save(db_session, "base") == (str(int(version) + 1), "base")
        finally:
            release.set()
        assert future.result(timeout=15) == 409


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
    changed = storyboard.update(
        project,
        episode,
        created["id"],
        {"row_version": created["row_version"], "title": "雨夜归来", "shot_size": None},
    )["shot"]
    db_session.expunge_all()
    persisted = storyboard.get(project, episode, created["id"])["shot"]
    assert persisted["title"] == "雨夜归来" and persisted["shot_size"] is None
    assert int(persisted["row_version"]) == int(created["row_version"]) + 1
    assert persisted["context_hash"] == changed["context_hash"] == created["context_hash"]
    with pytest.raises(WorkflowError) as stale_shot:
        storyboard.update(
            project,
            episode,
            created["id"],
            {"row_version": created["row_version"], "title": "旧名称"},
        )
    assert stale_shot.value.code == "shot_version_conflict"
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
    def structures(connection):
        inspector = inspect(connection)
        return {
            table: {
                "columns": {
                    column["name"]: {
                        "type": str(column["type"]),
                        "nullable": column["nullable"],
                        "default": column["default"],
                        "comment": column["comment"],
                    }
                    for column in inspector.get_columns(table)
                },
                "indexes": inspector.get_indexes(table),
                "unique": inspector.get_unique_constraints(table),
                "foreign_keys": inspector.get_foreign_keys(table),
                "checks": inspector.get_check_constraints(table),
            }
            for table in ("shot_scripts", "episode_scripts", "user_creation_drafts")
        }

    with migration_mysql_engine.begin() as connection:
        assert connection.engine.url.database.endswith("_test")
        canonical = structures(connection)
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
        assert structures(connection) == canonical
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


@pytest.mark.parametrize(
    "purpose", ["novel_adaptation", "asset_image", "storyboard_image", "storyboard_video"]
)
def test_mysql_private_generation_draft_is_frozen_without_changing_shared_work(flow, purpose):
    project, episode = flow.project.id, flow.episode.id
    identity(flow.session, project)
    board = EpisodeStoryboardService(flow.session)
    writing = EpisodeWritingService(flow.session)
    model_id = flow.config.id
    request = {}
    if purpose == "novel_adaptation":
        model_id = (
            AIModelConfigService(flow.session)
            .create(
                {
                    "service_type": "text",
                    "name": "controlled private text",
                    "model_key": "fixture",
                    "provider": "ark",
                    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
                }
            )
            .id
        )
        saved = writing.save_novel(
            project, episode, {"content_version": "1", "content": "夜雨中的共享小说。"}
        )
        object_id = str(episode)
        source = {
            "scene": "novel_script",
            "project_id": str(project),
            "episode_id": str(episode),
            "content_version": saved["content_version"],
        }

        def shared_work():
            return writing.get(project, episode)

    elif purpose == "asset_image":
        asset, _ = flow.library.create(
            "episode",
            episode,
            project,
            {"kind": "prop", "name": "红伞", "description": "共享描述", "prompt": "共享蓝光"},
            "mysql-private-asset",
        )
        object_id = str(asset.id)
        source = {
            "scene": "asset_image",
            "asset_id": object_id,
            "project_id": str(project),
            "episode_id": str(episode),
            "row_version": "1",
        }

        def shared_work():
            with flow.session.begin():
                row = flow.session.get(Asset, asset.id)
                return row.name, row.description, row.prompt, row.row_version

    else:
        shot = board.create(
            project,
            episode,
            {
                "storyboard_version": "1",
                "title": "抬眼",
                "script": "人物抬头看向雨中的共享街口。",
                "duration_ms": 5000,
            },
            "mysql-private-shot",
        )["shot"]
        object_id = shot["id"]
        if purpose == "storyboard_video":
            model_id = (
                AIModelConfigService(flow.session)
                .create(
                    {
                        "service_type": "video",
                        "name": "controlled private video",
                        "provider": "modelhub",
                        "model_key": "seedance-2.0-mini",
                        "base_url": "https://api.modelhub.cc",
                    }
                )
                .id
            )
            with flow.session.begin():
                flow.session.add(
                    MediaFile(
                        id=101,
                        format_code="image/png",
                        storage_locator="minio://images/private-video-frame.png",
                        width=16,
                        height=9,
                        project_id=project,
                        published_at=utcnow(),
                    )
                )
            ShotImageService(flow.session).create(
                {
                    "episode_id": episode,
                    "shot_id": object_id,
                    "media_id": "101",
                    "layout": "single",
                    "resolution": "2K",
                    "aspect": "16:9",
                    "context_hash": shot["context_hash"],
                }
            )
            shot = board.get(project, episode, object_id)["shot"]
            source = {
                "scene": "shot_video",
                "shot_id": object_id,
                "row_version": shot["row_version"],
                "context_hash": shot["video_context_hash"],
                "reference_media_id": "101",
            }
        else:
            source = {
                "scene": "shot_image",
                "shot_id": object_id,
                "layout": "single",
                "context_mode": "saved",
                "row_version": shot["row_version"],
                "context_hash": shot["context_hash"],
            }

        def shared_work():
            return board.get(project, episode, object_id)["shot"]

    draft_body = {
        "object_id": object_id,
        "purpose": purpose,
        "model_config_id": str(model_id),
        "input_text": "只在本次生成使用的私人补充",
    }
    if purpose in {"asset_image", "storyboard_image"}:
        draft_body["image_settings"] = {"aspect": "9:16", "resolution": "2K", "count": 2}
        request.update(
            input={"prompt": ""},
            parameters={"aspect": "16:9", "resolution": "2K", "count": 1},
        )
    elif purpose == "storyboard_video":
        draft_body["video_settings"] = {"duration_ms": 7000, "resolution": "480p"}
    drafts = UserCreationDraftService(flow.session)
    draft = drafts.create_for_scope(project, episode, draft_body)
    request["source"] = {
        **source,
        "draft_id": draft["id"],
        "draft_version": draft["draft_version"],
    }
    original = deepcopy(shared_work())
    kind = (
        "text"
        if purpose == "novel_adaptation"
        else "video"
        if purpose == "storyboard_video"
        else "image"
    )
    task, created = flow.generations.create(kind, request, "mysql-private-" + purpose)
    assert created
    flow.session.expunge_all()
    frozen = flow.generations.detail(task["generation_id"])
    snapshot = frozen["source_snapshot"]["creation_draft"]
    assert snapshot["id"] == draft["id"]
    assert snapshot["purpose"] == purpose
    assert snapshot["input_text"] == draft_body["input_text"]
    assert snapshot["draft_version"] == "1"
    if purpose == "novel_adaptation":
        assert any(
            draft_body["input_text"] in message["content"]
            for message in frozen["input"]["messages"]
        )
    else:
        assert draft_body["input_text"] in frozen["input"]["prompt"]
    if purpose in {"asset_image", "storyboard_image"}:
        assert frozen["parameters"] == draft_body["image_settings"]
    elif purpose == "storyboard_video":
        assert frozen["parameters"]["duration_ms"] == 7000
        assert frozen["parameters"]["resolution"] == "480p"
        assert frozen["input"]["reference_media_ids"] == ["101"]
        assert shared_work()["duration_ms"] == 5000
    drafts.update_for_scope(
        project, episode, object_id, purpose, {"row_version": "1", "input_text": "后续私人输入"}
    )
    flow.session.expunge_all()
    assert (
        flow.generations.detail(task["generation_id"])["source_snapshot"]
        == frozen["source_snapshot"]
    )
    assert shared_work() == original
    replayed, created = flow.generations.create(kind, request, "mysql-private-" + purpose)
    assert not created and replayed["generation_id"] == task["generation_id"]
    with pytest.raises(WorkflowError) as stale:
        flow.generations.create(kind, request, "mysql-stale-private-" + purpose)
    assert stale.value.code == "draft_version_conflict"
