"""Real MySQL task methods; model responses and immutable storage are explicit substitutes."""

import io
import json
from contextlib import contextmanager
from threading import get_ident
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select

import short_drama.agent.skill.runtime as skill_runtime
import tests.integration.standard.assistant.test_global_assistant_tasks as task_tests
from short_drama.agent.tools.registry import get_spec
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.domain.skills.catalog.skill import AgentSkillVersion
from short_drama.service.skills.catalog.skill import AgentSkillService
from short_drama.service.skills.catalog.versions import freeze_skills
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    task_actor,
    task_service,
)

pytestmark = pytest.mark.integration
task_workspace = task_tests.task_workspace


def test_freeze_catalog_versions_are_batched_per_page_and_owner_is_preserved(skill_workspace):
    from short_drama.agent.skill.progressive import freeze_catalog_locked

    factory, settings, _model, _storage = skill_workspace
    counts, expected = [], {}
    for size in (10, 100, 250):
        for index in range(len(expected), size):
            item = create_skill(
                skill_workspace,
                name=f"批量方法{index}",
                slug=f"batch-{index}",
                instructions="短方法正文",
                resources=[],
            )
            expected[item.id] = item.content_version
        statements = []

        def measured(
            _connection, _cursor, statement, _parameters, _context, _many, captured=statements
        ):
            if statement.lstrip().upper().startswith("SELECT"):
                captured.append(statement)

        event.listen(factory.kw["bind"], "before_cursor_execute", measured)
        try:
            with factory.begin() as session:
                frozen = freeze_catalog_locked(session, 1, settings, tool_lookup=get_spec)
        finally:
            event.remove(factory.kw["bind"], "before_cursor_execute", measured)
        counts.append(len(statements))
        personal = {item["id"]: item for item in frozen if not item["builtin"]}
        assert set(personal) == set(expected)
        with factory() as session:
            versions = {
                str(row.id): row for row in session.scalars(select(AgentSkillVersion)).all()
            }
            for key, item in personal.items():
                version = versions[item["version_id"]]
                assert version.skill_key == key and version.content_version == expected[key]
                assert (
                    version.owner_user_id == 1
                    and version.checksum_sha256 == item["package_checksum"]
                )
        assert len(statements) <= 5 * ((size + 99) // 100)
    assert counts[1] == counts[0]


def test_run_freezes_metadata_only_and_search_has_distinct_truncation(skill_workspace):
    from short_drama.agent.skill.progressive import catalog_locked

    skill = create_skill(
        skill_workspace,
        description="适用说明" * 470 + "独特命中",
        exclusions="不要处理未授权内容" * 100,
    )
    factory, settings, _model, storage = skill_workspace
    _conversation, accepted = new_skill_task(skill_workspace)
    with factory() as session, session.begin():
        run = session.get(AgentRun, accepted.run.id)
        task = session.get(AgentTask, accepted.task.id)
        frozen = run.config_snapshot["skill_catalog"]
        item = next(item for item in frozen if item["id"] == skill.id)
        assert item["version_id"]
        assert "instructions" not in item and "resources" not in item
        compact = catalog_locked(session, 1, task, settings, state={}, run=run)
        l1 = next(item for item in compact["items"] if item["id"] == skill.id)
        assert len(l1["description"]) <= 180
        assert len(l1["exclusions"]) <= 180
        searched = catalog_locked(
            session, 1, task, settings, state={}, run=run, query="独特命中", search=True
        )
        found = searched["items"][0]
        assert len(found["description"]) > 180
        assert found["truncated"]
        assert "独特命中" in found["match_excerpt"]
        assert "instructions" not in found and "resources" not in found
    with factory() as session:
        session.info["actor"] = task_actor()
        AgentSkillService(session, settings, storage, tool_lookup=get_spec).patch(
            skill.id,
            {
                "row_version": skill.row_version,
                "description": "新版目录",
                "instructions": "新版正文",
            },
        )
    with factory() as session, session.begin():
        run = session.get(AgentRun, accepted.run.id)
        task = session.get(AgentTask, accepted.task.id)
        found = catalog_locked(session, 1, task, settings, state={}, run=run, search=True)["items"][
            0
        ]
        assert found["content_version"] == "1"
        assert "新版目录" not in found["description"]


class _Storage:
    def __init__(self, factory):
        self.factory, self.objects, self.reads = factory, {}, 0
        self.fail = False
        self.connection_threads = {}
        engine = factory.kw["bind"]
        event.listen(engine, "checkout", self._checkout)
        event.listen(engine, "checkin", self._checkin)

    def _checkout(self, _connection, record, _proxy):
        self.connection_threads[id(record)] = get_ident()

    def _checkin(self, _connection, record):
        self.connection_threads.pop(id(record), None)

    def _assert_no_transaction(self):
        threads = self.connection_threads.copy()
        assert get_ident() not in threads.values(), {
            "reading_thread": get_ident(),
            "connection_threads": threads,
        }

    def stat(self, bucket, key):
        from types import SimpleNamespace

        self._assert_no_transaction()
        if (bucket, key) not in self.objects:
            raise NotFound("object missing")
        return SimpleNamespace(bucket=bucket, object_name=key)

    def put(self, bucket, key, source, size, _mime):
        self._assert_no_transaction()
        self.objects[bucket, key] = source.read()
        assert len(self.objects[bucket, key]) == size
        return self.stat(bucket, key)

    @contextmanager
    def open(self, bucket, key):
        self._assert_no_transaction()
        self.reads += 1
        if self.fail:
            raise OSError("fixture storage offline")
        with io.BytesIO(self.objects[bucket, key]) as stream:
            yield stream


@pytest.fixture
def skill_workspace(task_workspace, monkeypatch):
    factory, settings, model = task_workspace
    settings.minio_image_bucket = "fixture-skill-text"
    storage = _Storage(factory)
    monkeypatch.setattr(
        skill_runtime,
        "read_resource_bytes",
        lambda configured, manifest: AgentSkillService(
            None, configured, storage, tool_lookup=get_spec
        )._resource_bytes(manifest),
    )
    return factory, settings, model, storage


def test_skill_resource_io_guard_tracks_reading_thread_not_control_thread(skill_workspace):
    import hashlib
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    factory, settings, _model, storage = skill_workspace
    storage.objects["guard-test", "body"] = b"resource bytes"
    manifest = {
        "bucket": "guard-test",
        "object_name": "body",
        "path": "body.txt",
        "byte_size": 14,
        "checksum_sha256": hashlib.sha256(b"resource bytes").hexdigest(),
    }
    service = AgentSkillService(None, settings, storage, tool_lookup=get_spec)
    ready, release = Event(), Event()

    def control_read():
        with factory() as session:
            session.scalar(select(1))
            ready.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(control_read)
        try:
            assert ready.wait(5)
            assert service._resource_bytes(manifest) == "resource bytes"
        finally:
            release.set()
            future.result()
    with factory() as session:
        session.scalar(select(1))
        with pytest.raises(AssertionError):
            service._resource_bytes(manifest)


def create_skill(workspace, *, owner=1, **changes):
    factory, settings, _model, storage = workspace
    with factory() as session:
        session.info["actor"] = task_actor(owner)
        return AgentSkillService(session, settings, storage, tool_lookup=get_spec).create(
            {
                "name": "雨夜方法",
                "slug": "rain-method",
                "description": "依据主动选入内容核对人物动机。",
                "instructions": "先看用户提供的输入，再按[模板](assets/method.txt)组织对白。",
                "resources": [
                    {
                        "path": "assets/method.txt",
                        "kind": "template",
                        "content": "角色目标：旧版参考。",
                    }
                ],
                **changes,
            },
            uuid4().hex,
        )


def test_old_script_resource_is_rejected_by_catalog_and_resource_read(skill_workspace):
    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    with factory() as session:
        version = session.scalar(
            select(AgentSkillVersion).where(AgentSkillVersion.skill_key == skill.id)
        )
        resources = [dict(item, kind="script") for item in version.resources]
        version_id = version.id
    with factory.kw["bind"].begin() as connection:
        connection.execute(
            AgentSkillVersion.__table__.update()
            .where(AgentSkillVersion.id == version_id)
            .values(resources=resources)
        )
    for operation in (
        lambda service: service.detail(skill.id),
        lambda service: service.resource_content(
            skill.id, skill.content_version, "assets/method.txt"
        ),
    ):
        with factory() as session:
            session.info["actor"] = task_actor()
            with pytest.raises(WorkflowError) as error:
                operation(AgentSkillService(session, settings, storage, tool_lookup=get_spec))
            assert error.value.code == "invalid_agent_skill_resource"
            assert error.value.status_code == 409


def test_missing_skill_version_is_not_created_while_reading_or_selecting(skill_workspace):
    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    with factory.kw["bind"].begin() as connection:
        connection.execute(
            AgentSkillVersion.__table__.delete().where(AgentSkillVersion.skill_key == skill.id)
        )
    for operation in (
        lambda service: service.detail(skill.id),
        lambda service: service.patch(skill.id, {"row_version": skill.row_version, "name": "修改"}),
    ):
        with factory() as session:
            session.info["actor"] = task_actor()
            with pytest.raises(WorkflowError) as error:
                operation(AgentSkillService(session, settings, storage, tool_lookup=get_spec))
            assert error.value.code == "agent_skill_version_missing"
    with factory.begin() as session:
        session.info["actor"] = task_actor()
        with pytest.raises(WorkflowError) as error:
            freeze_skills(
                session,
                1,
                [SimpleNamespace(id=skill.id, content_version=skill.content_version)],
                tool_lookup=get_spec,
            )
        assert error.value.code == "agent_skill_version_missing"
        assert (
            session.scalar(select(AgentSkillVersion).where(AgentSkillVersion.skill_key == skill.id))
            is None
        )


def new_skill_task(workspace, skill=None):
    factory, settings, model, _storage = workspace
    with factory() as session:
        service = task_service(session, settings)
        conversation = service.create_global_conversation({}, uuid4().hex)
        accepted = service.send_task_message(
            conversation.id,
            {
                "content": "讨论雨夜人物，不写入作品。",
                "model_config_id": model,
                **(
                    {"skills": [{"id": skill.id, "content_version": skill.content_version}]}
                    if skill
                    else {}
                ),
            },
            uuid4().hex,
        )
    return conversation, accepted


@pytest.mark.parametrize("change", ["edit", "disable", "delete"])
def test_frozen_skill_reload_and_resources_use_durable_graph_scope(skill_workspace, change):
    from short_drama.agent.runtime.executor import AgentRuntime
    from short_drama.service.skills.catalog.skill import AgentSkillService
    from tests.support.assistant_runtime import RuntimeTransport, calls, tool_result

    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace, exclusions="不用于没有来源的改写")
    _, accepted = new_skill_task(skill_workspace)
    activation = []
    load = {"name": skill.id, "content_version": "1", "purpose": "核对雨夜人物"}

    def after_first_load(body):
        loaded = tool_result(body, "load")
        activation.append(loaded["activation_id"])
        assert loaded["instructions"] == skill.instructions
        assert loaded["exclusions"] == "不用于没有来源的改写"
        with factory() as session:
            session.info["actor"] = task_actor()
            library = AgentSkillService(session, settings, storage, tool_lookup=get_spec)
            if change == "edit":
                library.patch(skill.id, {"row_version": "1", "instructions": "新版不得倒灌"})
            elif change == "disable":
                library.patch_settings(skill.id, {"row_version": "1", "enabled": False})
            else:
                library.delete(skill.id, {"row_version": "1"})
        return calls(("repeat", "runtime__load_skill", load))

    def first_resource(body):
        repeated = tool_result(body, "repeat")
        assert repeated["already_loaded"] and "instructions" not in repeated
        assert repeated["activation_id"] == activation[0]
        return calls(
            (
                "read-first",
                "runtime__read_skill_resource",
                {"activation_id": activation[0], "path": "assets/method.txt", "limit": 6},
            )
        )

    def release(body):
        assert tool_result(body, "read-first")["content"] == "角色目标：旧"
        return calls(("release", "runtime__release_skill", {"activation_id": activation[0]}))

    def second_resource(body):
        reloaded = tool_result(body, "reload")
        assert reloaded["content_version"] == "1"
        assert reloaded["body_operation_id"] == tool_result(body, "load")["body_operation_id"]
        assert "新版不得倒灌" not in json.dumps(reloaded, ensure_ascii=False)
        return calls(
            (
                "read-again",
                "runtime__read_skill_resource",
                {"activation_id": reloaded["activation_id"], "path": "assets/method.txt"},
            )
        )

    transport = RuntimeTransport(
        [
            calls(("load", "runtime__load_skill", load)),
            after_first_load,
            first_resource,
            release,
            calls(("reload", "runtime__load_skill", load)),
            second_resource,
            {"text": "按原版本方法完成讨论"},
        ]
    )
    reads_before = storage.reads
    AgentRuntime(factory, settings, transport=transport).execute(accepted.run.id, 1)
    with factory() as session:
        run = session.get(AgentRun, accepted.run.id)
        assert run.status == "succeeded", run.error
        operations = session.scalars(
            select(AgentOperation)
            .where(AgentOperation.run_id == run.id)
            .order_by(AgentOperation.id)
        ).all()
        assert len(operations) == 6 and all(item.status == "succeeded" for item in operations)
        assert operations[-1].result["content"] == "角色目标：旧版参考。"
        assert "storage_key" not in json.dumps(operations[-1].result, ensure_ascii=False)
        assert "skill_activations" not in session.get(AgentTask, accepted.task.id).effective_state
    assert storage.reads - reads_before == 2


def test_resource_failure_retains_activation_but_another_task_cannot_guess_it(skill_workspace):
    from short_drama.agent.runtime.executor import AgentRuntime
    from tests.support.assistant_runtime import RuntimeTransport, calls, tool_result

    factory, settings, _model, storage = skill_workspace
    skill = create_skill(skill_workspace)
    _, accepted = new_skill_task(skill_workspace)
    activation = []
    reads_before = storage.reads

    def read(body):
        activation.append(tool_result(body, "load")["activation_id"])
        storage.fail = True
        return calls(
            (
                "read",
                "runtime__read_skill_resource",
                {"activation_id": activation[0], "path": "assets/method.txt"},
            )
        )

    def after_failure(body):
        assert "error" in tool_result(body, "read")
        storage.fail = False
        return calls(
            (
                "retry",
                "runtime__read_skill_resource",
                {"activation_id": activation[0], "path": "assets/method.txt"},
            )
        )

    AgentRuntime(
        factory,
        settings,
        transport=RuntimeTransport(
            [
                calls(
                    (
                        "load",
                        "runtime__load_skill",
                        {"name": skill.id, "content_version": "1", "purpose": "核对人物"},
                    )
                ),
                read,
                after_failure,
                {"text": "成功读回原资料"},
            ]
        ),
    ).execute(accepted.run.id, 1)
    _, other = new_skill_task(skill_workspace)
    AgentRuntime(
        factory,
        settings,
        transport=RuntimeTransport(
            [
                calls(
                    (
                        "guess",
                        "runtime__read_skill_resource",
                        {"activation_id": activation[0], "path": "assets/method.txt"},
                    )
                ),
                {"text": "另一任务没有该激活权限"},
            ]
        ),
    ).execute(other.run.id, 1)
    with factory() as session:
        assert session.get(AgentRun, accepted.run.id).status == "succeeded"
        guessed = session.scalar(
            select(AgentOperation).where(AgentOperation.run_id == other.run.id)
        )
        assert guessed.status == "failed" and guessed.error["code"] == "not_found"
    assert storage.reads - reads_before == 2
