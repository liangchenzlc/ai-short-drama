"""Real MySQL skill package contracts; object storage is an explicit in-memory substitute."""

import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from threading import Event, local
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, inspect, select, update
from sqlalchemy.exc import OperationalError

import tests.integration.standard.agent.test_agent_conversations as conversation_tests
from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.core.identity import token_hash
from short_drama.domain.agent_context import AgentSkill
from short_drama.domain.agent_skill import AgentSkillVersion
from short_drama.domain.collaboration import UserSession
from short_drama.main import create_app
from short_drama.schemas.agent_context import SkillSelection
from short_drama.service.agent_skill_service import AgentSkillService, freeze_skills
from short_drama.service.base import utcnow
from short_drama.utils.snowflake import next_id
from tests.integration.standard.agent.test_agent_conversations import actor
from tests.integration.standard.agent.test_agent_services import settings

pytestmark = pytest.mark.integration
workspace = conversation_tests.workspace


class _SkillTextStorage:
    def __init__(self, session):
        self.session = session
        self.objects = {}
        self.modified = {}
        self.removed = []
        self.fail_put = False
        self.connections = local()
        for name, delta in (("checkout", 1), ("checkin", -1)):

            def changed(*_args, delta=delta):
                self.connections.count = getattr(self.connections, "count", 0) + delta

            event.listen(session.get_bind(), name, changed)

    def stat(self, bucket, key):
        assert not self.session.in_transaction()
        assert getattr(self.connections, "count", 0) == 0
        if (bucket, key) not in self.objects:
            raise NotFound("Storage object does not exist")
        return SimpleNamespace(bucket=bucket, object_name=key)

    def put(self, bucket, key, source, length, _content_type):
        assert getattr(self.connections, "count", 0) == 0
        assert not self.session.in_transaction(), (
            "Resource I/O must precede publication transaction"
        )
        if self.fail_put:
            raise OSError("storage offline")
        data = source.read()
        assert len(data) == length
        self.objects[bucket, key] = data
        self.modified[bucket, key] = utcnow()
        return SimpleNamespace(bucket=bucket, object_name=key)

    def iter_objects(self, bucket, prefix, *, include_versions=False):
        assert include_versions
        assert getattr(self.connections, "count", 0) == 0
        for (stored_bucket, key), data in list(self.objects.items()):
            if stored_bucket == bucket and key.startswith(prefix):
                yield SimpleNamespace(
                    bucket=bucket,
                    object_name=key,
                    version_id="stored-version",
                    last_modified=self.modified[bucket, key],
                    size=len(data),
                )

    def remove(self, bucket, key, version_id=None):
        assert getattr(self.connections, "count", 0) == 0
        assert version_id == "stored-version"
        self.removed.append((bucket, key, version_id))
        self.objects.pop((bucket, key), None)

    @contextmanager
    def open(self, bucket, key):
        assert getattr(self.connections, "count", 0) == 0
        assert not self.session.in_transaction(), "Resource reads must not hold a SQL transaction"
        with io.BytesIO(self.objects[bucket, key]) as stream:
            yield stream


def _skill_package(**changes):
    return {
        "name": "分镜方法",
        "slug": "shot-method",
        "description": "只拆解主动选入的文本。",
        "instructions": "先核对必要输入，再按[模板](assets/shots.json)输出分镜。",
        "resources": [{"path": "assets/shots.json", "kind": "template", "content": '{"shots":[]}'}],
        **changes,
    }


@pytest.fixture
def builtin_workspace(workspace):
    from scripts.agent_system_migration import backfill_skill_packages

    factory, _, _ = workspace
    with factory.kw["bind"].begin() as connection:
        backfill_skill_packages(connection)
    with factory() as session:
        yield factory, settings(), _SkillTextStorage(session)


def test_builtin_package_publication_is_idempotent_and_preserves_personal_settings(
    builtin_workspace,
):
    from short_drama.agent.skill.builtin import SKILLS
    from short_drama.service.agent_builtin_skill_service import publish_builtin_skills

    factory, configured, storage = builtin_workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        AgentSkillService(session, configured, storage).patch_settings(
            "script.v1", {"row_version": "0", "enabled": False}
        )
    first = publish_builtin_skills(factory, configured, storage)
    second = publish_builtin_skills(factory, configured, storage)
    assert len(first["published"]) == 12 and first["existing"] == []
    assert second["published"] == [] and len(second["existing"]) == 12
    assert len(storage.objects) == 25
    with factory() as session:
        session.info["actor"] = actor(1)
        library = AgentSkillService(session, configured, storage)
        page = library.list(source="builtin")
        assert page.total == 12
        assert all("instructions" not in item.model_dump() for item in page.items)
        assert all("resources" not in item.model_dump() for item in page.items)
        own = library.detail("script.v1")
        assert own.content_version == 2 and not own.enabled
        session.info["actor"] = actor(2)
        other = library.detail("script.v1")
        assert other.enabled
        assert other.resource_count == 3
        content = library.resource_content("script.v1", "2", "assets/output-template.md")
        assert content.content.startswith("# 剧本标题")
        with session.begin():
            frozen = freeze_skills(
                session, 2, [SkillSelection(id="script.v1", content_version="2")]
            )
        assert frozen[0]["version_id"] and frozen[0]["instructions"] == other.instructions
        with session.begin():
            legacy = session.scalar(
                select(AgentSkillVersion).where(
                    AgentSkillVersion.skill_key == "script.v1",
                    AgentSkillVersion.content_version == 1,
                )
            )
            assert legacy.instructions == SKILLS["script.v1"] and legacy.resources == []


def test_builtin_publication_rejects_same_version_changes_and_rolls_back_batch(builtin_workspace):
    from short_drama.agent.skill.packages import builtin_skill_packages
    from short_drama.service.agent_builtin_skill_service import publish_builtin_skills

    factory, configured, storage = builtin_workspace
    packages = builtin_skill_packages()
    publish_builtin_skills(factory, configured, storage, packages)
    future = packages[0].model_copy(update={"content_version": 2})
    original = next(item for item in packages if item.key == "script.v1")
    changed = original.model_copy(
        update={
            "content": original.content.model_copy(
                update={"instructions": "不能覆盖已经发布的正文"}
            )
        }
    )
    with pytest.raises(Conflict, match="增加版本"):
        publish_builtin_skills(factory, configured, storage, [future, changed])
    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AgentSkillVersion)
                .where(
                    AgentSkillVersion.skill_key == "planning.v1",
                    AgentSkillVersion.content_version == 2,
                )
            )
            == 0
        )


def test_builtin_publication_storage_failure_publishes_no_new_version(builtin_workspace):
    from short_drama.service.agent_builtin_skill_service import publish_builtin_skills

    factory, configured, storage = builtin_workspace
    storage.fail_put = True
    with pytest.raises(WorkflowError, match="发布 Skill 资料"):
        publish_builtin_skills(factory, configured, storage)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentSkillVersion)) == 8


def test_concurrent_builtin_publishers_reuse_one_immutable_release(builtin_workspace):
    from short_drama.service.agent_builtin_skill_service import publish_builtin_skills

    factory, configured, storage = builtin_workspace
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: publish_builtin_skills(factory, configured, storage), range(2))
        )
    assert sorted(len(item["published"]) for item in results) == [0, 12]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AgentSkillVersion)) == 20


def test_skill_package_create_summary_idempotency_and_account_isolation(workspace):
    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        storage = _SkillTextStorage(session)
        library = AgentSkillService(session, settings(), storage)
        created = library.create(_skill_package(), "create-package")
        assert created.enabled and created.settings_version == 1
        assert created.resource_count == 1 and created.compatible
        assert created.content_version == created.row_version == 1
        assert library.create(_skill_package(), "create-package").id == created.id
        with pytest.raises(Conflict):
            library.create(_skill_package(name="另一个方法"), "create-package")
        summary = library.list(source="personal", query="选入", mode="standard")
        assert summary.total == 1 and summary.items[0].id == created.id
        assert "instructions" not in summary.items[0].model_dump()
        assert "resources" not in summary.items[0].model_dump()
        assert "agent-skills/" not in created.model_dump_json()
        assert (
            library.resource_content(created.id, "1", "assets/shots.json").content == '{"shots":[]}'
        )
        session.info["actor"] = actor(2)
        other = AgentSkillService(session, settings(), storage)
        assert other.list(source="personal").total == 0
        with pytest.raises(NotFound):
            other.detail(created.id)
        with pytest.raises(NotFound):
            other.resource_content(created.id, "1", "assets/shots.json")
        with pytest.raises(NotFound):
            other.copy(created.id, {"content_version": "1"}, "copy-other")
        own = other.create(_skill_package(), "create-package")
        assert own.id != created.id


def test_skill_upload_does_not_reuse_an_unpublished_object(workspace):
    from short_drama.schemas.agent_skill import SkillCreate

    factory, _, _ = workspace
    with factory() as session:
        storage = _SkillTextStorage(session)
        library = AgentSkillService(session, settings(), storage)
        resources = SkillCreate.model_validate(_skill_package()).resources
        first = library._store_resources(1, resources)
        second = library._store_resources(1, resources)
        assert first[0]["checksum_sha256"] == second[0]["checksum_sha256"]
        assert first[0]["object_name"] != second[0]["object_name"]


def test_skill_gc_keeps_all_history_and_previews_expired_orphans(workspace, monkeypatch):
    from short_drama.schemas.agent_skill import SkillCreate
    from short_drama.service import agent_skill_resource_service as resources

    factory, _, _ = workspace
    configured = settings()
    with factory() as session:
        session.info["actor"] = actor(1)
        storage = _SkillTextStorage(session)
        library = AgentSkillService(session, configured, storage)
        created = library.create(_skill_package(), "gc-package")
        library.patch(
            created.id,
            {
                "row_version": "1",
                "resources": [
                    {"path": "assets/shots.json", "kind": "template", "content": '{"v":2}'}
                ],
            },
        )
        library.delete(created.id, {"row_version": "2"})
        orphan = library._store_resources(1, SkillCreate.model_validate(_skill_package()).resources)
        bucket, key = orphan[0]["bucket"], orphan[0]["object_name"]
        future = utcnow() + timedelta(days=2)
        monkeypatch.setattr(resources, "database_now", lambda _session: future)
        preview = resources.collect_unreferenced_skill_resources(factory, configured, storage)
        assert preview["apply"] is False and preview["removed"] == []
        assert [item["object_name"] for item in preview["candidates"]] == [key]
        assert preview["protected"] == 2 and len(storage.objects) == 3
        result = resources.collect_unreferenced_skill_resources(
            factory, configured, storage, apply=True
        )
        assert len(result["removed"]) == 1 and result["errors"] == []
        assert (bucket, key, "stored-version") in storage.removed
        with session.begin():
            versions = list(session.scalars(select(AgentSkillVersion)))
            assert len(versions) == 2
            assert all(
                (item["bucket"], item["object_name"]) in storage.objects
                for version in versions
                for item in version.resources
            )


def test_skill_expired_upload_cannot_publish_after_gc_snapshot(workspace, monkeypatch):
    from short_drama.schemas.agent_skill import SkillCreate
    from short_drama.service import agent_skill_resource_service as resources

    factory, _, _ = workspace
    configured = settings()
    with factory() as session:
        session.info["actor"] = actor(1)
        storage = _SkillTextStorage(session)
        library = AgentSkillService(session, configured, storage)
        staged = library._store_resources(1, SkillCreate.model_validate(_skill_package()).resources)
        future = utcnow() + timedelta(days=2)
        monkeypatch.setattr(resources, "database_now", lambda _session: future)
        preview = resources.collect_unreferenced_skill_resources(factory, configured, storage)
        assert len(preview["candidates"]) == 1
        monkeypatch.setattr(library, "_store_resources", lambda *_args: staged)
        with pytest.raises(WorkflowError) as caught:
            library.create(_skill_package(), "expired-publication")
        assert caught.value.code == "agent_skill_upload_expired"
        with session.begin():
            assert session.scalar(select(func.count(AgentSkill.id))) == 0
            assert session.scalar(select(func.count(AgentSkillVersion.id))) == 0
        assert (
            len(
                resources.collect_unreferenced_skill_resources(
                    factory, configured, storage, apply=True
                )["removed"]
            )
            == 1
        )


def test_skill_gc_waits_for_publication_commit_before_reading_references(workspace, monkeypatch):
    from short_drama.service import agent_skill_resource_service as resources
    from short_drama.service import agent_skill_service as library_module

    factory, _, _ = workspace
    configured = settings()
    checked, release, listed = Event(), Event(), Event()
    clock = local()
    real_now = resources.database_now
    real_validate = resources.validate_staged_resources_locked

    def checked_upload(session, manifest):
        real_validate(session, manifest)
        checked.set()
        assert release.wait(10)

    monkeypatch.setattr(library_module, "validate_staged_resources_locked", checked_upload)
    monkeypatch.setattr(
        resources, "database_now", lambda session: getattr(clock, "now", None) or real_now(session)
    )
    with factory() as idle_session:
        storage = _SkillTextStorage(idle_session)
        real_list = storage.iter_objects

        def list_objects(*args, **kwargs):
            yield from real_list(*args, **kwargs)
            listed.set()

        monkeypatch.setattr(storage, "iter_objects", list_objects)

        def publish():
            with factory() as session:
                session.info["actor"] = actor(1)
                return AgentSkillService(session, configured, storage).create(
                    _skill_package(), "gc-concurrent-publication"
                )

        def collect():
            clock.now = utcnow() + timedelta(days=2)
            return resources.collect_unreferenced_skill_resources(
                factory, configured, storage, apply=True
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            publishing = pool.submit(publish)
            try:
                assert checked.wait(10)
                collecting = pool.submit(collect)
                assert listed.wait(10)
                with pytest.raises(TimeoutError):
                    collecting.result(timeout=0.2)
            finally:
                release.set()
            assert publishing.result(timeout=10).resource_count == 1
            result = collecting.result(timeout=10)
        assert result["protected"] == 1 and result["candidates"] == []
        assert storage.removed == []


def test_skill_gc_bounds_scope_age_limit_and_storage_errors(workspace, monkeypatch):
    from short_drama.core.exceptions import StorageUnavailable
    from short_drama.schemas.agent_skill import SkillCreate
    from short_drama.service import agent_skill_resource_service as resources

    factory, _, _ = workspace
    configured = settings()
    with factory() as session:
        storage = _SkillTextStorage(session)
        library = AgentSkillService(session, configured, storage)
        fresh = library._store_resources(1, SkillCreate.model_validate(_skill_package()).resources)
        bucket = fresh[0]["bucket"]
        # An old Last-Modified alone cannot make a newly staged key collectible.
        storage.modified[bucket, fresh[0]["object_name"]] = utcnow() - timedelta(days=2)
        legacy_keys = [f"agent-skills/1/content/{digit * 64}" for digit in ("a", "b")]
        ignored_key = "agent-skills/1/unknown-file"
        for key in [*legacy_keys, ignored_key]:
            storage.objects[bucket, key] = b"unpublished"
            storage.modified[bucket, key] = utcnow() - timedelta(days=2)
        for kwargs in ({"grace_hours": 23}, {"limit": 0}, {"namespace_prefix": "../"}):
            with pytest.raises(ValueError):
                resources.collect_unreferenced_skill_resources(
                    factory, configured, storage, **kwargs
                )
        preview = resources.collect_unreferenced_skill_resources(
            factory, configured, storage, limit=1
        )
        assert len(preview["candidates"]) == 1 and preview["recent"] == 1
        assert preview["ignored"] == 1 and not storage.removed

        def failed_remove(*_args, **_kwargs):
            raise StorageUnavailable("object deletion offline")

        monkeypatch.setattr(storage, "remove", failed_remove)
        result = resources.collect_unreferenced_skill_resources(
            factory, configured, storage, apply=True
        )
        assert len(result["errors"]) == 2 and result["removed"] == []
        assert all("回收 Skill 资料失败" in item["message"] for item in result["errors"])

        def failed_list(*_args, **_kwargs):
            raise StorageUnavailable("object listing offline")

        monkeypatch.setattr(storage, "iter_objects", failed_list)
        with pytest.raises(WorkflowError) as caught:
            resources.collect_unreferenced_skill_resources(factory, configured, storage, apply=True)
        assert caught.value.code == "agent_skill_gc_storage_unavailable"


def test_skill_package_versions_resources_and_old_task_snapshot_survive_edits(workspace):
    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        storage = _SkillTextStorage(session)
        library = AgentSkillService(session, settings(), storage)
        created = library.create(_skill_package(), "version-package")
        with session.begin():
            frozen = freeze_skills(
                session, 1, [SkillSelection(id=created.id, content_version="1")]
            )[0]
        revised = library.patch(
            created.id,
            {
                "row_version": "1",
                "description": "新的匹配用途。",
                "resources": [
                    {"path": "assets/shots.json", "kind": "template", "content": '{"shots":[1]}'}
                ],
            },
        )
        assert revised.content_version == revised.row_version == 2
        assert revised.package_checksum != created.package_checksum
        assert revised.settings_version == created.settings_version
        assert (
            library.resource_content(created.id, "1", "assets/shots.json").content == '{"shots":[]}'
        )
        assert (
            library.resource_content(created.id, "2", "assets/shots.json").content
            == '{"shots":[1]}'
        )
        assert frozen["instructions"] == created.instructions and frozen["version_id"]
        with pytest.raises(Conflict):
            library.patch(created.id, {"row_version": "1", "instructions": "过期编辑"})
        library.delete(created.id, {"row_version": "2"})
        with session.begin():
            old = session.get(AgentSkillVersion, int(frozen["version_id"]))
            assert old.instructions == frozen["instructions"] and old.resources
        with pytest.raises(NotFound):
            library.resource_content(created.id, "1", "assets/shots.json")
        with session.begin(), pytest.raises(NotFound):
            freeze_skills(session, 1, [SkillSelection(id=created.id, content_version="1")])


def test_skill_package_personal_settings_builtin_readonly_and_copy(workspace):
    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        library = AgentSkillService(session, settings())
        changed = library.patch_settings("script.v1", {"row_version": "0", "enabled": False})
        assert (
            not changed.enabled and changed.settings_version == 1 and changed.content_version == 1
        )
        assert library.list(enabled=False).total == 1
        with session.begin(), pytest.raises(WorkflowError) as caught:
            freeze_skills(session, 1, [SkillSelection(id="script.v1", content_version="1")])
        assert caught.value.code == "agent_skill_disabled"
        with pytest.raises(Conflict):
            library.patch_settings("script.v1", {"row_version": "0", "enabled": True})
        with pytest.raises(WorkflowError) as caught:
            library.patch("script.v1", {"row_version": "1", "instructions": "overwrite"})
        assert caught.value.code == "agent_skill_readonly"
        copied = library.copy("script.v1", {"content_version": "1"}, "copy-builtin")
        assert not copied.builtin and copied.enabled
        assert copied.id != "script.v1" and "script.v1" in copied.source
        assert library.copy("script.v1", {"content_version": "1"}, "copy-builtin").id == copied.id
        session.info["actor"] = actor(2)
        assert library.detail("script.v1").enabled


def test_skill_package_storage_failure_and_corrupt_resource_never_publish(workspace):
    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        storage = _SkillTextStorage(session)
        library = AgentSkillService(session, settings(), storage)
        storage.fail_put = True
        with pytest.raises(WorkflowError) as caught:
            library.create(_skill_package(), "storage-package")
        assert caught.value.code == "agent_skill_storage_unavailable"
        with session.begin():
            assert session.scalar(select(func.count(AgentSkill.id))) == 0
        storage.fail_put = False
        created = library.create(_skill_package(), "storage-package")
        storage.objects[next(iter(storage.objects))] = b"tampered"
        with pytest.raises(WorkflowError) as caught:
            library.resource_content(created.id, "1", "assets/shots.json")
        assert caught.value.code == "agent_skill_resource_corrupt"
        with pytest.raises(WorkflowError):
            library.copy(created.id, {"content_version": "1"}, "copy-corrupt")
        with session.begin():
            assert session.scalar(select(func.count(AgentSkill.id))) == 1


def test_skill_package_versions_cannot_be_mutated_by_scoped_or_worker_session(workspace):
    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        library = AgentSkillService(session, settings())
        created = library.create(
            _skill_package(instructions="方法正文", resources=[]), "immutable-package"
        )
        for system in (False, True):
            if system:
                session.info.pop("actor", None)
            with pytest.raises(WorkflowError) as caught, session.begin():
                version = session.scalar(
                    select(AgentSkillVersion).where(AgentSkillVersion.skill_key == created.id)
                )
                version.instructions = "rewritten"
            assert caught.value.code == "agent_skill_version_immutable"
            with pytest.raises(WorkflowError), session.begin():
                session.execute(update(AgentSkillVersion).values(instructions="bulk rewrite"))


def test_skill_package_export_import_and_copy_keep_all_resources(workspace):
    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        library = AgentSkillService(session, settings(), _SkillTextStorage(session))
        created = library.create(
            _skill_package(
                enabled=False,
                exclusions="不适用于未选内容。",
                resources=[
                    {"path": "assets/shots.json", "kind": "template", "content": '{"shots":[]}'},
                    {"path": "references/table.txt", "kind": "template", "content": "镜头表"},
                ],
            ),
            "export-package",
        )
        data, filename = library.export(created.id, "1")
        preview = library.preview_import(io.BytesIO(data), filename)
        assert preview.compatible and preview.draft.resources[0].content == '{"shots":[]}'
        assert preview.draft.enabled
        assert preview.draft.exclusions == created.exclusions
        assert preview.draft.resources[1].kind == "template"
        imported = library.create(preview.draft.model_dump(), "import-package")
        assert imported.id != created.id and imported.resource_count == 2
        copied = library.copy(created.id, {"content_version": "1"}, "copy-package")
        assert copied.id != created.id and copied.resource_count == 2
        assert (
            library.resource_content(copied.id, "1", "assets/shots.json").content == '{"shots":[]}'
        )


def test_skill_package_setting_changes_do_not_publish_content_and_unknown_tools_cannot_load(
    workspace,
):
    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        library = AgentSkillService(session, settings())
        created = library.create(
            _skill_package(instructions="方法正文", resources=[], enabled=False),
            "settings-package",
        )
        updated = library.patch(created.id, {"row_version": "1", "instructions": "修订方法"})
        assert not updated.enabled
        enabled = library.patch_settings(created.id, {"row_version": "1", "enabled": True})
        assert enabled.content_version == updated.content_version == 2
        assert enabled.row_version == updated.row_version == 2
        with session.begin():
            assert (
                session.scalar(
                    select(func.count(AgentSkillVersion.id)).where(
                        AgentSkillVersion.skill_key == created.id
                    )
                )
                == 2
            )
        with pytest.raises(WorkflowError) as caught:
            library.create(
                _skill_package(
                    instructions="调用外部命令", resources=[], required_tools=["execute_python"]
                ),
                "unsupported-package",
            )
        assert caught.value.code == "agent_skill_incompatible"


def test_incompatible_skill_can_be_disabled_but_not_enabled(workspace, monkeypatch):
    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        library = AgentSkillService(session, settings())
        created = library.create(
            _skill_package(instructions="方法正文", resources=[]), "retired-capability"
        )
        monkeypatch.setattr(
            "short_drama.service.agent_skill_service.platform_tool_issues",
            lambda _tools: ["所需能力已退役"],
        )
        disabled = library.patch_settings(created.id, {"row_version": "1", "enabled": False})
        assert not disabled.enabled and not disabled.compatible
        with pytest.raises(WorkflowError) as caught:
            library.patch_settings(created.id, {"row_version": "2", "enabled": True})
        assert caught.value.code == "agent_skill_incompatible"
        current = library.detail(created.id)
        assert not current.enabled and current.settings_version == 2


def test_skill_package_concurrent_create_and_settings_are_serialized(workspace):
    factory, _, _ = workspace

    def create():
        with factory() as session:
            session.info["actor"] = actor(1)
            return (
                AgentSkillService(session, settings())
                .create(_skill_package(instructions="方法正文", resources=[]), "concurrent-create")
                .id
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        identifiers = list(pool.map(lambda _: create(), range(2)))
    assert identifiers[0] == identifiers[1]

    def setting():
        with factory() as session:
            session.info["actor"] = actor(1)
            try:
                return (
                    AgentSkillService(session, settings())
                    .patch_settings(
                        "script.v1",
                        {"row_version": "0", "enabled": False},
                    )
                    .settings_version
                )
            except Conflict:
                return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: setting(), range(2)))
    assert sorted(map(str, results)) == ["1", "conflict"]


def test_skill_package_offline_backfill_preserves_legacy_identity_and_settings(workspace):
    from scripts.agent_system_migration import backfill_skill_packages
    from short_drama.service.agent_skill_service import skill_digest

    factory, _, _ = workspace
    identifier = next_id()
    with factory() as session, session.begin():
        session.add(
            AgentSkill(
                id=identifier,
                owner_user_id=1,
                name="旧方法",
                filename="legacy.md",
                instructions="原方法正文",
                content_version=7,
                row_version=9,
                checksum_sha256=skill_digest("原方法正文"),
                enabled=0,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        )
    with factory().get_bind().begin() as connection:
        first = backfill_skill_packages(connection)
        second = backfill_skill_packages(connection)
    assert first == {"personal_versions": 1, "builtin_versions": 8, "settings": 1}
    assert not any(second.values())
    with factory() as session:
        session.info["actor"] = actor(1)
        legacy = AgentSkillService(session, settings()).detail(str(identifier))
        assert (
            legacy.id == str(identifier) and legacy.content_version == 7 and legacy.row_version == 9
        )
        assert not legacy.enabled and legacy.settings_version == 1
        assert legacy.instructions == "原方法正文" and legacy.package_checksum


def test_skill_package_authenticated_http_contracts_never_expose_directory_bodies(workspace):
    factory, _, _ = workspace
    configured = Settings(
        _env_file=None,
        auth_enabled=True,
        agent_enabled=True,
        auth_cookie_secure=False,
        public_origin="http://testserver",
    )
    app = create_app(configured)
    app.state.session_factory = factory
    app.state.agent_schema_ready = True

    def client(owner):
        token, csrf = f"skill-token-{owner}", f"skill-csrf-{owner}"
        with factory() as session, session.begin():
            session.add(
                UserSession(
                    id=next_id(),
                    user_id=owner,
                    token_hash=token_hash(token),
                    csrf_hash=token_hash(csrf),
                    created_at=utcnow(),
                    expires_at=utcnow() + timedelta(days=1),
                )
            )
        result = TestClient(app)
        result.cookies.set("sd_session", token)
        result.headers.update({"Origin": configured.public_origin, "X-CSRF-Token": csrf})
        return result

    owner, other = client(1), client(2)
    body = _skill_package(instructions="方法正文", resources=[])
    assert TestClient(app).get("/api/v1/agent/skills").status_code == 401
    assert owner.post("/api/v1/agent/skills", json=body).status_code == 422
    assert (
        owner.post(
            "/api/v1/agent/skills",
            json={**body, "owner_user_id": "2"},
            headers={"Idempotency-Key": "forged-owner"},
        ).status_code
        == 422
    )
    response = owner.post(
        "/api/v1/agent/skills", json=body, headers={"Idempotency-Key": "http-create"}
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert (
        created["content_version"] == created["row_version"] == created["settings_version"] == "1"
    )
    path = f"/api/v1/agent/skills/{created['id']}"
    assert other.get(path).status_code == 404
    page = owner.get("/api/v1/agent/skills", params={"source": "personal", "query": "分镜"}).json()
    assert page["total"] == 1 and page["items"][0]["id"] == created["id"]
    assert "instructions" not in page["items"][0] and "resources" not in page["items"][0]
    assert owner.patch(path, json={"row_version": "1", "tags": ["重复", "重复"]}).status_code == 422
    assert (
        owner.post(
            path + "/copy",
            json={"content_version": "1", "name": " "},
            headers={"Idempotency-Key": "blank-copy"},
        ).status_code
        == 422
    )
    assert (
        owner.patch(
            "/api/v1/agent/skills/script.v1", json={"row_version": "1", "instructions": "overwrite"}
        ).status_code
        == 403
    )
    preview = owner.post(
        "/api/v1/agent/skills/imports/preview",
        files={
            "file": (
                "SKILL.md",
                b"---\nname: imported\ndescription: Imported method.\n---\n# Method",
                "text/markdown",
            ),
        },
    )
    assert preview.status_code == 200 and preview.json()["compatible"]
    exported = owner.get(path + "/export", params={"content_version": "1"})
    assert exported.status_code == 200 and exported.headers["content-type"] == "application/zip"
    assert "invocation_mode" not in created
    receipt = owner.get("/api/v1/agent/skills/requests/http-create")
    assert receipt.status_code == 200 and receipt.json()["id"] == created["id"]
    assert other.get("/api/v1/agent/skills/requests/http-create").status_code == 404


def test_import_replacement_preserves_hidden_tags_unless_explicitly_cleared(workspace):
    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        library = AgentSkillService(session, settings())
        created = library.create(
            _skill_package(instructions="正文", resources=[], tags=["隐藏标签"], enabled=False),
            "tags-original",
        )
        preview = library.preview_import(
            io.BytesIO(b"---\nname: replacement\ndescription: Method\n---\nNew body"), "method.md"
        )
        assert preview.tags_provided is False
        body = preview.draft.model_dump(exclude={"enabled", "tags"})
        updated = library.patch(created.id, {**body, "row_version": "1"})
        assert updated.tags == ["隐藏标签"] and not updated.enabled
        assert library.list(query="隐藏标签", source="personal").total == 0
        assert library.list(query="replacement", source="personal").total == 1
        cleared = library.patch(created.id, {"row_version": "2", "tags": []})
        assert cleared.tags == []
        assert library.request_receipt("tags-original").id == created.id
        session.info["actor"] = actor(2)
        with pytest.raises(NotFound):
            library.request_receipt("tags-original")


def test_mode_incompatibility_and_missing_references_are_rejected_before_storage(workspace):
    from short_drama.agent.tools.contracts import STANDARD_TOOL

    factory, _, _ = workspace
    with factory() as session:
        session.info["actor"] = actor(1)
        storage = _SkillTextStorage(session)
        library = AgentSkillService(session, settings(), storage)
        for changes in (
            {"required_tools": [STANDARD_TOOL], "modes": ["standard", "infinite_canvas"]},
            {"instructions": "[缺失](references/missing.md)"},
        ):
            with pytest.raises(WorkflowError):
                library.create(_skill_package(**changes), "invalid-package")
        assert storage.objects == {}
        accepted = library.create(
            _skill_package(required_tools=[STANDARD_TOOL], modes=["standard"]), "valid-mode"
        )
        before = dict(storage.objects)
        with pytest.raises(WorkflowError):
            library.patch(
                accepted.id,
                {
                    "row_version": "1",
                    "modes": ["infinite_canvas"],
                    "resources": [{"path": "references/new.md", "content": "must not upload"}],
                },
            )
        assert storage.objects == before
        with pytest.raises(WorkflowError):
            library.patch(accepted.id, {"row_version": "1", "modes": ["infinite_canvas"]})


def test_skill_management_migration_preserves_settings_and_version_constraint(
    migration_mysql_engine,
):
    engine = migration_mysql_engine
    folder = (
        Path(__file__).resolve().parents[5] / "docs/database/migrations/2026-10-26-skill-management"
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            (folder / "002_restore_invocation_mode.sql").read_text(encoding="utf-8")
        )
        connection.exec_driver_sql(
            "INSERT INTO agent_skill_settings "
            "(id, owner_user_id, skill_key, enabled, invocation_mode, row_version, updated_at) "
            "VALUES (9, 1, 'script.v1', 0, 'manual', 7, UTC_TIMESTAMP(3))"
        )
        connection.exec_driver_sql(
            (folder / "001_drop_invocation_mode.sql").read_text(encoding="utf-8")
        )
        assert "invocation_mode" not in {
            item["name"] for item in inspect(connection).get_columns("agent_skill_settings")
        }
        row = connection.exec_driver_sql(
            "SELECT enabled, row_version FROM agent_skill_settings WHERE id = 9"
        ).one()
        assert tuple(row) == (0, 7)
        with pytest.raises(OperationalError) as caught:
            connection.exec_driver_sql(
                "UPDATE agent_skill_settings SET row_version = 0 WHERE id = 9"
            )
        assert caught.value.orig.args[0] == 3819
