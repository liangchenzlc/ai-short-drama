"""Exercise the live acceptance assertions with explicit controlled text in real Workers."""

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from pymysql.err import OperationalError as MySQLOperationalError
from sqlalchemy import event, select
from sqlalchemy.exc import OperationalError

from short_drama.agent.task.state import pending_task_locked
from short_drama.core.time import utcnow
from short_drama.domain import AIModelConfig, Asset, Project
from short_drama.domain.assistant.execution.models import AgentRun, AgentTurn
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.domain.assistant.tasks.task import AgentOperation, AgentTask
from short_drama.utils.snowflake import next_id
from tests.integration.standard.assistant import test_agent_runtime_live as live
from tests.integration.standard.assistant.test_agent_questions import QUESTION
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    harness_workspace as harness_workspace,
)
from tests.integration.standard.assistant.test_global_assistant_tasks import (
    task_service,
)
from tests.support.agent_runtime_live import LiveCase, live_text_config
from tests.support.agent_runtime_live import controlled_case as controlled_case
from tests.support.agent_runtime_live_worker import deliver
from tests.support.assistant_runtime import calls

pytestmark = pytest.mark.integration
task_workspace = harness_workspace


@pytest.mark.parametrize(
    "root,model",
    [
        ("https://t8api.com", "gpt-6.1-terra"),
        ("https://sub2.de5.net", "gpt-6.1-terra"),
        ("https://api.deepseek.com", "deepseek-flash"),
        ("HTTPS://API.DEEPSEEK.COM///", "deepseek-flash"),
    ],
    ids=["t8-terra", "sub2-terra", "deepseek-flash", "deepseek-normalized-root"],
)
def test_live_target_accepts_authorized_pair(monkeypatch, root, model):
    monkeypatch.setenv("RUN_AGENT_RUNTIME_LIVE", "1")
    monkeypatch.setenv("AGENT_LIVE_URL", root)
    monkeypatch.setenv("AGENT_LIVE_MODEL", model)
    monkeypatch.setenv("AGENT_LIVE_API_KEY", "target-fixture-private-key")
    actual_root, actual_model, secret = live_text_config()
    assert (actual_root, actual_model) == (root.rstrip("/"), model)
    assert isinstance(secret, SecretStr)
    assert secret.get_secret_value() == "target-fixture-private-key"
    # 缺少数据库字段，让已获授权的 Worker 只通过目标门禁后立即停止。
    with pytest.raises(KeyError) as caught:
        deliver({"root": root, "model": model, "api_key": "target-fixture-private-key"})
    assert caught.value.args == ("database_url",)


@pytest.mark.parametrize("enabled", [None, "0", "true"])
def test_live_target_requires_explicit_opt_in(monkeypatch, enabled):
    for name in (
        "RUN_AGENT_RUNTIME_LIVE",
        "AGENT_LIVE_URL",
        "AGENT_LIVE_MODEL",
        "AGENT_LIVE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    if enabled is not None:
        monkeypatch.setenv("RUN_AGENT_RUNTIME_LIVE", enabled)
    with pytest.raises(RuntimeError, match="尚未启用"):
        live_text_config()


@pytest.mark.parametrize("enabled", [None, "0", "true"])
def test_live_target_worker_requires_explicit_opt_in(monkeypatch, enabled):
    monkeypatch.delenv("RUN_AGENT_RUNTIME_LIVE", raising=False)
    if enabled is not None:
        monkeypatch.setenv("RUN_AGENT_RUNTIME_LIVE", enabled)
    with pytest.raises(RuntimeError, match="尚未启用"):
        deliver(
            {
                "root": "https://api.deepseek.com",
                "model": "deepseek-flash",
                "api_key": "target-fixture-private-key",
                "controlled_replies": None,
            }
        )


def test_live_target_worker_keeps_controlled_mode_without_opt_in(monkeypatch):
    monkeypatch.delenv("RUN_AGENT_RUNTIME_LIVE", raising=False)
    with pytest.raises(KeyError) as caught:
        deliver(
            {
                "root": "https://t8api.com",
                "model": "gpt-6.1-terra",
                "api_key": "target-fixture-private-key",
                "controlled_replies": {},
            }
        )
    assert caught.value.args == ("database_url",)


@pytest.mark.parametrize(
    "root,model",
    [
        ("https://t8api.com", "deepseek-flash"),
        ("https://sub2.de5.net", "deepseek-flash"),
        ("https://api.deepseek.com", "gpt-6.1-terra"),
        ("https://api.deepseek.com.evil.test", "deepseek-flash"),
        ("https://api.deepseek.com", "deepseek-flash-other"),
        ("https://api.deepseek.com", "deepseek-v4-pro"),
        ("https://api.deepseek.com", "deepseek-chat"),
        ("https://api.deepseek.com", "deepseek-reasoner"),
    ],
    ids=["t8-flash", "sub2-flash", "deepseek-terra", "host", "model", "v4-pro", "chat", "reasoner"],
)
def test_live_target_rejects_unauthorized_pair(monkeypatch, root, model):
    monkeypatch.setenv("RUN_AGENT_RUNTIME_LIVE", "1")
    monkeypatch.setenv("AGENT_LIVE_URL", root)
    monkeypatch.setenv("AGENT_LIVE_MODEL", model)
    monkeypatch.setenv("AGENT_LIVE_API_KEY", "target-fixture-private-key")
    with pytest.raises(RuntimeError, match="授权"):
        live_text_config()
    with pytest.raises(RuntimeError, match="授权"):
        deliver({"root": root, "model": model, "api_key": "target-fixture-private-key"})


def report_turns(case, *, count=1):
    from tests.integration.standard.assistant.test_agent_context_compaction import runtime

    _, accepted, _ = case.create("受控报告器验证，不执行模型或工具")
    _, context = runtime((case.factory, case.settings, case.model_id), accepted)
    now, ids = utcnow(), []
    with case.factory.begin() as session:
        for index in range(count):
            turn_id = next_id()
            ids.append(turn_id)
            started = now + timedelta(seconds=count - index)
            session.add(
                AgentTurn(
                    id=turn_id,
                    run_id=context.run_id,
                    scope_id=context.scope_id,
                    turn_no=index + 1,
                    status="unknown",
                    request_messages=[
                        {
                            "body": {
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": "PRIVATE_LARGE_WIRE_" + "雨" * 150000
                                        if index in {0, count // 2, count - 1}
                                        else "受控小请求",
                                    }
                                ]
                            }
                        }
                    ],
                    response={"graph_message": {"data": {"content": f"受控答复{index}"}}},
                    usage={
                        "source": "assistant",
                        "reserved_output_tokens": 64,
                        "settled": False,
                    },
                    created_at=now,
                    started_at=started,
                    updated_at=started,
                    finished_at=started,
                )
            )
        run = session.get(AgentRun, context.run_id)
        run.status, run.finished_at = "failed", now
        run.error = {"code": "agent_context_budget_exhausted"}
        run.usage = {"decision_calls": count, "reserved_output_tokens": count * 64}
    case.facts["worker_deliveries"] = [{"controlled": True, "model_usage": "unknown"}]
    return list(reversed(ids))


def test_live_report_reads_large_json_by_bounded_id_and_preserves_unknown_usage(
    controlled_case, monkeypatch
):
    case = controlled_case
    monkeypatch.setenv("AGENT_LIVE_API_KEY", case.text_secret.get_secret_value())
    ordered = report_turns(case, count=130)
    engine, statements = case.factory.kw["bind"], []

    def capture(_connection, _cursor, statement, parameters, _context, _many):
        if statement.startswith("SELECT"):
            statements.append((statement.replace("`", ""), parameters))

    event.listen(engine, "before_cursor_execute", capture)
    try:
        case.report()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    report = json.loads((case.directory / f"{case.scene_id}.json").read_text(encoding="utf-8"))
    assert report["passed"] is False
    assert report["report_collection"]["status"] == "complete"
    assert report["calls_by_source"] == {"assistant": 130}
    assert report["tool_counts"] == {}
    assert [row["id"] for row in report["model_receipts"]] == [str(item) for item in ordered]
    assert [row["id"] for row in report["model_diagnostics"]] == [
        str(item) for item in [*ordered[:16], *ordered[-112:]]
    ]
    assert report["omitted_model_diagnostics"] == 2
    assert report["runs"][0]["usage"] == {"decision_calls": 130, "reserved_output_tokens": 8320}
    assert all(not row["usage"]["settled"] for row in report["model_receipts"])
    assert all("provider_usage" not in row["usage"] for row in report["model_receipts"])
    assert report["actual_provider_price"] == "unknown"
    encoded = json.dumps(report)
    assert "PRIVATE_LARGE_WIRE_" not in encoded and len(encoded) < 200000
    large_reads = [sql for sql, _ in statements if "agent_turns.request_messages" in sql]
    assert len(large_reads) == 128
    assert all("ORDER BY" not in sql and "agent_turns.id =" in sql for sql in large_reads)
    assert all(
        "agent_turns.usage" not in sql and "agent_turns.response" not in sql
        for sql, _ in statements
        if "agent_turns" in sql and "ORDER BY" in sql
    )


@pytest.mark.parametrize("body_passed", [False, True], ids=["body-failed", "body-passed"])
@pytest.mark.parametrize("stage", ["model_receipts", "model_diagnostics"])
def test_live_report_keeps_small_facts_and_reraises_collection_failure(
    controlled_case, body_passed, stage
):
    case = controlled_case
    report_turns(case)
    case.passed = body_passed
    engine = case.factory.kw["bind"]

    def fail(_connection, _cursor, statement, _parameters, _context, _many):
        statement = statement.replace("`", "")
        if "agent_turns.usage" in statement and (
            stage == "model_receipts" or "agent_turns.request_messages" in statement
        ):
            raise OperationalError(statement, {}, MySQLOperationalError(1038, "Out of sort memory"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(OperationalError) as caught:
            case.report()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert caught.value.orig.args == (1038, "Out of sort memory")
    report = json.loads((case.directory / f"{case.scene_id}.json").read_text(encoding="utf-8"))
    assert report["passed"] is False and report["test_body_passed"] is body_passed
    assert report["report_collection"] == {
        "status": "failed",
        "stage": stage,
        "error": {"type": "OperationalError", "driver_code": 1038},
    }
    assert report["facts"]["worker_deliveries"] == [{"controlled": True, "model_usage": "unknown"}]
    assert report["runs"][0]["error"] == {"code": "agent_context_budget_exhausted"}
    assert report["runs"][0]["usage"] == {"decision_calls": 1, "reserved_output_tokens": 64}
    assert report["calls_by_source"] == (None if stage == "model_receipts" else {"assistant": 1})
    assert (
        report["model_receipts"] is None
        if stage == "model_receipts"
        else (report["model_receipts"][0]["usage"]["settled"] is False)
    )
    assert report["actual_provider_price"] == "unknown"


@pytest.fixture
def report_publication(tmp_path):
    subject = SimpleNamespace(
        directory=tmp_path,
        scene_id="writer-publication",
        text_secret=SecretStr("writer-fixture-private-key"),
    )
    payload = {
        "passed": False,
        "test_body_passed": True,
        "report_collection": {"status": "collecting"},
        "actual_provider_price": "unknown",
        "usage": {"reserved_output_tokens": 64, "settled": False},
        "text": "中文报告",
    }
    path = tmp_path / "writer-publication.json"
    path.write_text('{"previous": true}', encoding="utf-8")
    return subject, payload, path, path.with_suffix(".json.tmp")


def test_live_report_publication_retries_transient_windows_access_denied(
    report_publication, monkeypatch
):
    subject, payload, path, temporary = report_publication
    previous = path.read_bytes()
    original_replace = Path.replace
    attempts, waits = [], []

    def replace(source, destination):
        if source == temporary and Path(destination) == path:
            attempts.append(source)
            assert path.read_bytes() == previous
            if len(attempts) <= 2:
                error = PermissionError(13, "controlled transient Windows access denied")
                error.winerror = 5
                raise error
        return original_replace(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("tests.support.agent_runtime_live.time.sleep", waits.append)
    LiveCase._write_report(subject, payload)
    assert len(attempts) == 3 and waits == [0.05, 0.05]
    assert json.loads(path.read_text(encoding="utf-8")) == payload
    assert not temporary.exists()


def test_live_report_publication_exhaustion_preserves_previous_report(
    report_publication, monkeypatch
):
    subject, payload, path, temporary = report_publication
    previous = path.read_bytes()
    error = PermissionError(13, "controlled persistent Windows access denied")
    error.winerror = 5
    attempts, waits, writes = [], [], []
    original_write_text = Path.write_text

    def replace(source, destination):
        assert source == temporary and Path(destination) == path
        attempts.append(source)
        raise error

    def write_text(source, *args, **kwargs):
        assert source == temporary
        writes.append(source)
        return original_write_text(source, *args, **kwargs)

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr("tests.support.agent_runtime_live.time.sleep", waits.append)
    with pytest.raises(PermissionError) as caught:
        LiveCase._write_report(subject, payload)
    assert caught.value is error
    assert len(attempts) == 5 and waits == [0.05] * 4 and writes == [temporary]
    assert path.read_bytes() == previous
    assert json.loads(temporary.read_text(encoding="utf-8")) == payload


@pytest.mark.parametrize(
    "kind,winerror",
    [("permission", None), ("permission", 32), ("os", None)],
    ids=["permission-without-winerror", "windows-sharing-violation", "other-os-error"],
)
def test_live_report_publication_does_not_retry_unrelated_errors(
    report_publication, monkeypatch, kind, winerror
):
    subject, payload, path, temporary = report_publication
    previous = path.read_bytes()
    error = (
        PermissionError(13, "controlled unrelated error")
        if kind == "permission"
        else (OSError(28, "controlled disk full"))
    )
    if winerror is not None:
        error.winerror = winerror
    attempts, waits = [], []

    def replace(source, destination):
        assert source == temporary and Path(destination) == path
        attempts.append(source)
        raise error

    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("tests.support.agent_runtime_live.time.sleep", waits.append)
    with pytest.raises(type(error)) as caught:
        LiveCase._write_report(subject, payload)
    assert caught.value is error
    assert attempts == [temporary] and waits == []
    assert path.read_bytes() == previous
    assert json.loads(temporary.read_text(encoding="utf-8")) == payload


def test_live_report_publication_does_not_retry_temporary_write_failure(
    report_publication, monkeypatch
):
    subject, payload, path, temporary = report_publication
    previous = path.read_bytes()
    error = PermissionError(13, "controlled temporary write denied")
    error.winerror = 5
    writes, attempts, waits = [], [], []

    def write_text(source, *args, **kwargs):
        assert source == temporary
        writes.append(source)
        raise error

    def replace(source, destination):
        attempts.append(source)

    monkeypatch.setattr(Path, "write_text", write_text)
    monkeypatch.setattr(Path, "replace", replace)
    monkeypatch.setattr("tests.support.agent_runtime_live.time.sleep", waits.append)
    with pytest.raises(PermissionError) as caught:
        LiveCase._write_report(subject, payload)
    assert caught.value is error
    assert writes == [temporary] and attempts == [] and waits == []
    assert path.read_bytes() == previous and not temporary.exists()


def workers(case, monkeypatch, plans):
    original, queued = case.execute, iter(plans)

    def execute(accepted, **kwargs):
        plan = next(queued)
        kwargs["controlled_replies"] = plan(accepted) if callable(plan) else plan
        return original(accepted, **kwargs)

    monkeypatch.setattr(case, "execute", execute)


def project(case, accepted):
    with case.factory() as session:
        task = pending_task_locked(session, session.get(AgentTask, accepted.task.id))
        return session.get(Project, int(task.execution_target["project_id"]))


def patch(case, accepted, *, shot=False, name="停止前角色", key="saved"):
    current = project(case, accepted)
    return calls(
        (
            key,
            "workbench__apply_standard_patch",
            {
                "project_id": str(current.id),
                "patch": {
                    **({"storyboard_version": str(current.storyboard_version)} if shot else {}),
                    "actions": [
                        {
                            "action": "create_shot" if shot else "create_asset",
                            "key": key,
                            "values": {"title": name, "script": "雨声中误会消解。"}
                            if shot
                            else {"kind": "character", "name": name},
                        }
                    ],
                },
            },
        )
    )


def media(case, accepted):
    current = project(case, accepted)
    with case.factory() as session:
        model = session.scalar(select(AIModelConfig).where(AIModelConfig.service_type == "image"))
        assets = session.scalars(
            select(Asset).where(Asset.project_id == current.id).order_by(Asset.id)
        ).all()
    return calls(
        (
            "submit",
            "workbench__submit_generation_batch",
            {
                "project_id": str(current.id),
                "requests": [
                    {
                        "target_kind": "asset",
                        "target_id": str(asset.id),
                        "row_version": str(asset.row_version),
                        "model_config_id": str(model.id),
                        "model_version": str(model.row_version),
                        "prompt": "受控图片",
                        "count": 1,
                    }
                    for asset in assets
                ],
            },
        )
    )


def media_wait():
    return calls(
        (
            "wait",
            "runtime__wait_generation",
            {
                "task_ids": [
                    {"$result": ["submit", "items", index, "generation_id"]} for index in range(2)
                ]
            },
        )
    )


def skill(case, accepted, name):
    with case.factory() as session:
        frozen = session.get(AgentRun, accepted.run.id).config_snapshot["skill_catalog"]
        return next(item for item in frozen if item["name"] == name)


def load(item, call_id="load"):
    return calls(
        (
            call_id,
            "runtime__load_skill",
            {
                "name": item["name"],
                "content_version": str(item["content_version"]),
                "purpose": "核对实际方法",
            },
        )
    )


def read(path, *, load_id="load", call_id="read"):
    return calls(
        (
            call_id,
            "runtime__read_skill_resource",
            {
                "activation_id": {"$result": [load_id, "activation_id"]},
                "path": path,
            },
        )
    )


def delegate(name):
    return (
        name,
        "task",
        {"subagent_type": "workbench-task", "description": f"子任务:{name}；完成自己的工作。"},
    )


def test_worker_protocol_parallel_calls_and_ledger(controlled_case, monkeypatch):
    case = controlled_case
    workers(
        case,
        monkeypatch,
        [
            [
                calls(
                    ("target", "runtime__describe_creation_target", {}),
                    ("policy", "runtime__get_generation_policy", {}),
                ),
                {"text": "实际已核对"},
            ]
        ],
    )
    live.test_live_protocol_streaming_parallel_tools_and_actual_usage(case, 0)


def test_worker_native_media_wait_and_cross_worker_resume(controlled_case, monkeypatch):
    case = controlled_case
    workers(
        case,
        monkeypatch,
        [
            lambda accepted: [
                media(case, accepted),
                patch(case, accepted, name="站长"),
                media_wait(),
            ],
            [{"text": "已核对实际图片"}],
        ],
    )
    live.test_live_native_async_wait_rebuilds_worker_without_repeating_business(case, 0)


@pytest.mark.parametrize("boundary", ["stream", "tool", "media", "question"])
def test_worker_stop_live_assertions_and_sse(controlled_case, monkeypatch, boundary):
    case = controlled_case

    def first(accepted):
        return {
            "stream": [{"text": "雨夜场景"}],
            "tool": [patch(case, accepted)],
            "media": [media(case, accepted), media_wait()] if boundary == "media" else [],
            "question": [calls(("question", "runtime__ask_user", QUESTION))],
        }[boundary]

    workers(
        case,
        monkeypatch,
        [
            first,
            [],
            [
                calls(("target", "runtime__describe_creation_target", {})),
                {"text": "核对已结算成果"},
            ],
        ],
    )
    live.test_live_stop_boundaries_and_new_message_use_a_new_run(case, 0, boundary)


@pytest.mark.parametrize(
    "boundary", ["business_committed", "interrupt_registered", "answer_committed"]
)
def test_worker_crash_live_assertions(controlled_case, monkeypatch, boundary):
    case = controlled_case
    if boundary == "business_committed":
        plans = [lambda accepted: [patch(case, accepted)], [{"text": "核对已保存人物"}]]
    else:
        plans = [[calls(("question", "runtime__ask_user", QUESTION))], []]
        plans.append(lambda accepted: [patch(case, accepted, shot=True), {"text": "保存结尾"}])
    workers(case, monkeypatch, plans)
    live.test_live_crash_windows_keep_one_business_or_answer_identity(case, 0, boundary)


@pytest.mark.parametrize("failure", ["schema", "business"])
def test_worker_business_crash_waits_for_success_after_failed_patch(
    controlled_case, monkeypatch, failure
):
    case = controlled_case

    def first(accepted):
        rejected = patch(case, accepted, shot=failure == "business", key="rejected")
        arguments = rejected["calls"][0][2]
        if failure == "schema":
            arguments["unexpected"] = True
        else:
            arguments["patch"]["storyboard_version"] = str(
                project(case, accepted).storyboard_version + 1
            )
        return [
            calls(("target", "runtime__describe_creation_target", {})),
            rejected,
            patch(case, accepted),
        ]

    workers(case, monkeypatch, [first, [{"text": "核对已保存人物"}]])
    live.test_live_crash_windows_keep_one_business_or_answer_identity(case, 0, "business_committed")
    with case.factory() as session:
        operations = session.scalars(
            select(AgentOperation).where(AgentOperation.run_id == case.runs[0])
        ).all()
        patches = [
            item for item in operations if item.tool_name == "workbench__apply_standard_patch"
        ]
        failed = [item for item in patches if item.status == "failed"]
        succeeded = [item for item in patches if item.status == "succeeded"]
        assert len(failed) == len(succeeded) == 1
        assert failed[0].business_invocation_id is None
        if failure == "business":
            assert failed[0].error["code"] == "storyboard_version_conflict"
        receipt = session.get(BusinessToolInvocation, succeeded[0].business_invocation_id)
        assert receipt.status == "succeeded"
        assert any(
            item.tool_name == "runtime__describe_creation_target" and item.status == "succeeded"
            for item in operations
        )


@pytest.mark.parametrize("outcome", ["answered", "skipped"])
@pytest.mark.parametrize("child", [False, True])
@pytest.mark.parametrize("failed_attempt", [False, True], ids=["first-valid", "failed-then-valid"])
def test_worker_question_answer_skip_and_child_api_assertions(
    controlled_case, monkeypatch, outcome, child, failed_attempt
):
    case = controlled_case
    replies = [calls(("question", "runtime__ask_user", QUESTION))]
    if failed_attempt:
        replies.insert(
            0, calls(("invalid-question", "runtime__ask_user", {**QUESTION, "scope_id": "forged"}))
        )
    if child:
        first = {"routes": {"root": [calls(delegate("one"))], "one": replies}}

        def second(accepted):
            return {
                "routes": {
                    "root": [{"text": "已整合结尾"}],
                    "one": [patch(case, accepted, shot=True), {"text": "保存结尾"}],
                }
            }
    else:
        first = replies

        def second(accepted):
            return [patch(case, accepted, shot=True), {"text": "保存结尾"}]

    workers(case, monkeypatch, [first, second])
    execute, initial = case.execute, []

    def capture(accepted, **kwargs):
        result = execute(accepted, **kwargs)
        if not initial:
            initial.append(accepted)
            attempts = sorted(
                (
                    item
                    for item in case.operations(accepted)
                    if item.tool_name == "runtime__ask_user"
                ),
                key=lambda item: item.id,
            )
            assert result.status == "waiting_input"
            assert len(attempts) == (2 if failed_attempt else 1)
            assert attempts[-1].status == "waiting_input"
            assert attempts[-1].wait_state["interrupt_id"]
            if failed_attempt:
                assert attempts[0].status == "failed" and attempts[0].wait_state is None
            with case.factory() as session:
                state = task_service(session, case.settings).task_runtime_state(
                    accepted.task.conversation_id
                )
                assert len(state.pending_questions) == 1 and state.pending_questions[0].can_answer
        return result

    monkeypatch.setattr(case, "execute", capture)
    live.test_live_question_api_and_cross_worker_resume(case, 0, outcome, child)
    registered = live.questions(case, initial[0])
    assert len(registered) == 1 and registered[0].wait_state["consumed"]
    assert registered[0].status == "succeeded"
    assert len({item["pid"] for item in case.facts["worker_deliveries"]}) == 2


def test_worker_single_question_acceptance_rejects_two_registered_questions(
    controlled_case, monkeypatch
):
    case, accepted_runs = controlled_case, []

    def first(accepted):
        accepted_runs.append(accepted)
        return [
            calls(
                ("first", "runtime__ask_user", QUESTION),
                ("second", "runtime__ask_user", {**QUESTION, "title": "第二张真实问题卡"}),
            )
        ]

    workers(case, monkeypatch, [first])
    with pytest.raises(AssertionError):
        live.test_live_question_api_and_cross_worker_resume(case, 0, "skipped", False)
    registered = live.questions(case, accepted_runs[0])
    assert len(registered) == 2
    assert len({item.wait_state["interrupt_id"] for item in registered}) == 2
    assert all(item.status == "waiting_input" for item in registered)
    assert not case.passed


def test_worker_two_waiting_children_keep_finished_sibling(controlled_case, monkeypatch):
    case = controlled_case

    def first(accepted):
        return {
            "routes": {
                "root": [calls(*(delegate(name) for name in ("one", "two", "three")))],
                "one": [calls(("q-one", "runtime__ask_user", QUESTION))],
                "two": [calls(("q-two", "runtime__ask_user", {**QUESTION, "title": "寄存处"}))],
                "three": [
                    patch(case, accepted, shot=True, name="站台", key="third"),
                    {"text": "站台已保存"},
                ],
            }
        }

    def second(accepted):
        return {
            "routes": {
                "root": [{"text": "三条支线完成"}],
                "one": [
                    patch(case, accepted, shot=True, name="钟楼", key="first"),
                    {"text": "钟楼已保存"},
                ],
                "two": [
                    patch(case, accepted, shot=True, name="寄存处", key="second"),
                    {"text": "寄存处已保存"},
                ],
            }
        }

    workers(case, monkeypatch, [first, second, second])
    execute, snapshots = case.execute, []

    def capture(accepted, **kwargs):
        result = execute(accepted, **kwargs)
        with case.factory() as session:
            snapshots.append(
                {
                    "turn_ids": set(session.scalars(select(AgentTurn.id))),
                    "delegations": {
                        item.id: item.result["delegation_inputs"]
                        for item in session.scalars(
                            select(AgentOperation).where(AgentOperation.tool_name == "task")
                        )
                    },
                }
            )
        return result

    monkeypatch.setattr(case, "execute", capture)
    live.test_live_two_waiting_children_resume_by_scope_and_keep_completed_sibling(case, 0)
    assert len({item["pid"] for item in case.facts["worker_deliveries"]}) == 3
    assert all(item["delegations"] == snapshots[0]["delegations"] for item in snapshots[1:])
    resumed = set()
    with case.factory() as session:
        for scope in session.scalars(
            select(AgentExecutionScope).where(AgentExecutionScope.parent_scope_id.is_not(None))
        ):
            delegate_call = session.scalar(
                select(AgentOperation).where(
                    AgentOperation.tool_call_id == scope.parent_tool_call_id
                )
            )
            description = delegate_call.arguments["description"]
            name = description.split("；", 1)[0].removeprefix("子任务:")
            frozen = delegate_call.result["delegation_inputs"]
            assert frozen["task_sources"]["items"][0]["content"] == description
            assert frozen["memory_epoch"] == 0
            for turn in session.scalars(select(AgentTurn).where(AgentTurn.scope_id == scope.id)):
                if turn.usage.get("source") != "tool_selection":
                    continue
                query = json.dumps(turn.request_messages[0]["body"]["messages"], ensure_ascii=False)
                assert description in query and "task_working_notes" not in query
                assert all(
                    "子任务:" + other not in query for other in {"one", "two", "three"} - {name}
                )
                if turn.id not in snapshots[0]["turn_ids"]:
                    resumed.add(name)
    assert resumed == {"one", "two"}


def integer_skill_worker():
    from itertools import repeat

    from tests.support import agent_runtime_live_worker as worker

    original = worker.controlled_transport

    def transport(replies):
        result = original(replies)
        queued = result.replies

        def reply(body):
            value = next(queued)
            value = value(body) if callable(value) else value
            for _call_id, name, arguments in value.get("calls", []):
                if name == "runtime__read_skill_resource":
                    arguments["activation_id"] = int(arguments["activation_id"])
            return value

        result.replies = repeat(reply)
        return result

    worker.controlled_transport = transport
    sys.stdout.write(json.dumps(worker.deliver(json.load(sys.stdin)), ensure_ascii=False))


@pytest.mark.parametrize("activation_type", [str, int], ids=["string", "integer"])
@pytest.mark.parametrize("final_release", [False, True], ids=["active", "released"])
@pytest.mark.parametrize("change", [0, 1])
def test_worker_skill_canonical_frozen_versions_and_trace(
    controlled_case, monkeypatch, change, final_release, activation_type, question_copy=None
):
    case, accepted_runs = controlled_case, []
    if activation_type is int:
        original_run = subprocess.run

        def run_worker(argv, **kwargs):
            if argv[-2:] == ["-m", "tests.support.agent_runtime_live_worker"]:
                argv = [
                    *argv[:-2],
                    "-c",
                    "from tests.integration.standard.assistant.test_agent_acceptance_runner "
                    "import integer_skill_worker; integer_skill_worker()",
                ]
            return original_run(argv, **kwargs)

        monkeypatch.setattr("tests.support.agent_runtime_live.subprocess.run", run_worker)

    def first(accepted):
        accepted_runs.append(accepted)
        item = skill(case, accepted, "雨夜取证方法")
        question = dict(QUESTION)
        if question_copy:
            source = (
                ["read", "content"]
                if question_copy.startswith("resource")
                else ["load", "instructions"]
            )
            question["question"] = {"$result": source}
            if question_copy.endswith("-form"):
                question["title"] = {"$result": source}
                question["fields"] = [
                    {
                        "key": "continue",
                        "label": {"$result": source},
                        "type": "single_select",
                        "required": True,
                        "placeholder": {"$result": source},
                        "options": [
                            {"value": "yes", "label": {"$result": source}, "recommended": True},
                            {"value": "no", "label": "调整方向"},
                        ],
                    }
                ]
        return [
            calls(("search", "runtime__search_skills", {"query": "尾部唯一检索词"})),
            load(item),
            read("references/evidence.md"),
            load(item, "again"),
            calls(("question", "runtime__ask_user", question)),
        ]

    def second(accepted):
        replies = [
            calls(
                (
                    "release",
                    "runtime__release_skill",
                    {"activation_id": {"$result": ["load", "activation_id"]}},
                )
            ),
            load(skill(case, accepted, "雨夜取证方法"), "reload"),
            read("references/evidence.md", load_id="reload", call_id="reread"),
        ]
        if final_release:
            replies.append(
                calls(
                    (
                        "final_release",
                        "runtime__release_skill",
                        {"activation_id": {"$result": ["reload", "activation_id"]}},
                    )
                )
            )
        return [*replies, {"text": "冻结版本重载一致"}]

    original_assert = live.assert_canonical_skill_requests

    def check_identities(turns, tool_calls, loads, reads, *details):
        assert len(reads) == 2 and all(item.status == "succeeded" for item in reads)
        assert all(type(item.arguments["activation_id"]) is activation_type for item in reads)
        if question_copy:
            ask = next(
                item
                for item in case.operations(accepted_runs[0])
                if item.tool_name == "runtime__ask_user"
            )
            source = (
                reads[0].result["content"]
                if question_copy.startswith("resource")
                else loads[0].result["instructions"]
            )
            assert ask.arguments["question"] == source
            assert ask.status == "succeeded" and ask.wait_state["consumed"] is True
            stored = next(item for item in tool_calls if item.id == ask.tool_call_id)
            actual = [
                json.loads(call["function"]["arguments"])["question"]
                for turn in turns
                for request in turn.request_messages
                for row in request["body"]["messages"]
                for call in row.get("tool_calls", [])
                if call["id"] == stored.provider_call_id
            ]
            assert actual and all(value == source for value in actual)
            case.facts["canonical_question_copy"] = {
                "mode": question_copy,
                "operation_id": str(ask.id),
                "tool_call_id": str(ask.tool_call_id),
                "provider_call_id": stored.provider_call_id,
                "question": ask.arguments["question"],
                "wait_state": ask.wait_state,
                "result": ask.result,
                "sdk_question_copies": len(actual),
                "worker_pids": [row["pid"] for row in case.facts["worker_deliveries"]],
            }
            case.report()
        result = original_assert(turns, tool_calls, loads, reads, *details)
        if question_copy:
            facts = (turns, tool_calls, loads, reads, *details)
            rejected, snapshots = _canonical_question_rejections(original_assert, facts)
            with case.factory() as session:
                for index in (0, 1, 8):
                    for item, snapshot in zip(facts[index], snapshots[index], strict=True):
                        stored_row = session.get(type(item), item.id)
                        assert {key: getattr(stored_row, key) for key in snapshot} == snapshot
            case.facts["canonical_question_copy"].update(
                negative_checks=rejected,
                original_objects_unchanged=True,
                original_database_rows_unchanged=True,
            )
            case.report()
        for field in ("arguments", "result"):
            invalid_reads = [
                SimpleNamespace(
                    id=item.id,
                    scope_id=item.scope_id,
                    tool_call_id=item.tool_call_id,
                    arguments=dict(item.arguments),
                    result=dict(item.result),
                )
                for item in reads
            ]
            for item in invalid_reads:
                values = getattr(item, field)
                values["activation_id"] = type(values["activation_id"])(
                    int(values["activation_id"]) + 1
                )
            with pytest.raises(AssertionError) as caught:
                original_assert(turns, tool_calls, loads, invalid_reads, *details)
            assert type(caught.value) is AssertionError
            assert str(caught.value).splitlines()[0] == "canonical_context_no_post_reload_read"
        return result

    monkeypatch.setattr(live, "assert_canonical_skill_requests", check_identities)
    workers(case, monkeypatch, [first, second])
    live.test_live_skill_canonical_reload_frozen_directory_and_trace(case, change)
    assert case.facts["canonical_context"]["final_active"] is (not final_release)


def _canonical_question_rejections(assert_requests, facts):
    from copy import deepcopy

    def records(items):
        return [
            {column.key: deepcopy(getattr(item, column.key)) for column in item.__table__.columns}
            for item in items
        ]

    indices = (0, 1, 2, 3, 8)
    before = {index: records(facts[index]) for index in indices}
    ask = next(item for item in facts[8] if item.tool_name == "runtime__ask_user")
    stored = next(item for item in facts[1] if item.id == ask.tool_call_id)
    inactive = next(
        index
        for index, turn in enumerate(facts[0])
        if turn.usage.get("source") == "assistant"
        and turn.turn_no > next(item.turn_no for item in facts[0] if item.id == stored.turn_id)
        and not any(
            item["active"]
            for item in live.canonical_current_activations(
                turn.request_messages[0]["body"]["messages"], facts[5], facts[4].id
            )
        )
    )
    source_groups = {}
    for name, operation in (("body", facts[2][0]), ("resource", facts[3][0])):
        provider_id = next(
            item.provider_call_id for item in facts[1] if item.id == operation.tool_call_id
        )
        for turn in facts[0]:
            for request in turn.request_messages:
                messages = request["body"]["messages"]
                row = next(
                    (
                        row
                        for row in messages
                        if any(call["id"] == provider_id for call in row.get("tool_calls", []))
                    ),
                    None,
                )
                if row is not None:
                    identifiers = {call["id"] for call in row["tool_calls"]}
                    source_groups[name] = [
                        row,
                        *[item for item in messages if item.get("tool_call_id") in identifiers],
                    ]
                    break
            if name in source_groups:
                break
        assert len(source_groups[name]) >= 2

    def copied():
        result = list(facts)
        for index in indices:
            result[index] = [SimpleNamespace(**deepcopy(item)) for item in before[index]]
        messages = result[0][inactive].request_messages[0]["body"]["messages"]
        operation = next(item for item in result[8] if item.id == ask.id)
        call = next(item for item in result[1] if item.id == stored.id)
        row = next(
            row
            for row in messages
            if any(item["id"] == stored.provider_call_id for item in row.get("tool_calls", []))
        )
        wire = next(item for item in row["tool_calls"] if item["id"] == stored.provider_call_id)
        answer = next(row for row in messages if row.get("tool_call_id") == stored.provider_call_id)
        return result, messages, operation, call, row, wire, answer

    def wire_question(wire):
        arguments = json.loads(wire["function"]["arguments"])
        arguments["question"] += "篡改"
        wire["function"]["arguments"] = json.dumps(arguments)

    def wire_answer(answer):
        payload = json.loads(answer["content"])
        payload["answer_message_id"] = "999"
        answer["content"] = json.dumps(payload)

    def saved_answer(call):
        call.result["message"]["data"]["additional_kwargs"]["source_message_ids"] = ["999"]

    def altered_value(operation):
        key = next(iter(operation.wait_state["answer"]["values"]))
        operation.wait_state["answer"]["values"][key] = "篡改答案"

    mutations = [
        (
            "unconsumed",
            "canonical_question_answer_not_consumed",
            lambda f, m, o, c, r, w, a: o.wait_state.update(consumed=False),
        ),
        (
            "not_ready",
            "canonical_question_answer_not_consumed",
            lambda f, m, o, c, r, w, a: o.wait_state.update(ready=False),
        ),
        (
            "wrong_answer_id",
            "canonical_question_answer_identity_mismatch",
            lambda f, m, o, c, r, w, a: o.wait_state.update(answer_message_id="999"),
        ),
        (
            "missing_answer_id",
            "canonical_question_answer_not_consumed",
            lambda f, m, o, c, r, w, a: o.wait_state.update(answer_message_id=None),
        ),
        (
            "wrong_answer_version",
            "canonical_question_answer_not_consumed",
            lambda f, m, o, c, r, w, a: o.wait_state["answer"].update(question_version="2"),
        ),
        (
            "wrong_answer_value",
            "canonical_question_answer_result_mismatch",
            lambda f, m, o, c, r, w, a: altered_value(o),
        ),
        (
            "wrong_operation_scope",
            "canonical_question_operation_identity_mismatch",
            lambda f, m, o, c, r, w, a: setattr(o, "scope_id", o.scope_id + 1),
        ),
        (
            "wrong_operation_run",
            "canonical_question_operation_identity_mismatch",
            lambda f, m, o, c, r, w, a: setattr(o, "run_id", o.run_id + 1),
        ),
        (
            "wrong_operation_task",
            "canonical_question_operation_identity_mismatch",
            lambda f, m, o, c, r, w, a: setattr(o, "task_id", o.task_id + 1),
        ),
        (
            "failed_operation",
            "canonical_question_operation_identity_mismatch",
            lambda f, m, o, c, r, w, a: setattr(o, "status", "failed"),
        ),
        (
            "unknown_operation",
            "canonical_question_operation_identity_mismatch",
            lambda f, m, o, c, r, w, a: f[8].remove(o),
        ),
        (
            "wrong_call_scope",
            "canonical_question_call_identity_mismatch",
            lambda f, m, o, c, r, w, a: setattr(c, "scope_id", c.scope_id + 1),
        ),
        (
            "wrong_call_run",
            "canonical_question_call_identity_mismatch",
            lambda f, m, o, c, r, w, a: setattr(c, "run_id", c.run_id + 1),
        ),
        (
            "wrong_provider_call",
            "canonical_question_call_identity_mismatch",
            lambda f, m, o, c, r, w, a: setattr(c, "provider_call_id", "forged"),
        ),
        (
            "wire_question_changed",
            "canonical_context_original_group_changed",
            lambda f, m, o, c, r, w, a: wire_question(w),
        ),
        (
            "operation_question_changed",
            "canonical_question_operation_arguments_changed",
            lambda f, m, o, c, r, w, a: o.arguments.update(
                question=o.arguments["question"] + "篡改"
            ),
        ),
        (
            "wire_answer_changed",
            "canonical_question_wire_answer_mismatch",
            lambda f, m, o, c, r, w, a: wire_answer(a),
        ),
        (
            "stored_answer_source_changed",
            "canonical_question_saved_answer_mismatch",
            lambda f, m, o, c, r, w, a: saved_answer(c),
        ),
        (
            "operation_answer_source_changed",
            "canonical_question_answer_result_mismatch",
            lambda f, m, o, c, r, w, a: o.result.update(source_message_ids=["999"]),
        ),
        (
            "missing_result",
            "canonical_context_incomplete_tool_groups",
            lambda f, m, o, c, r, w, a: m.remove(a),
        ),
        (
            "missing_call",
            "canonical_context_incomplete_tool_groups",
            lambda f, m, o, c, r, w, a: m.remove(r),
        ),
        (
            "duplicate_complete_group",
            "canonical_context_original_call_missing_or_duplicated",
            lambda f, m, o, c, r, w, a: m.extend(deepcopy([r, a])),
        ),
        (
            "skill_provenance",
            "canonical_question_not_independent_group",
            lambda f, m, o, c, r, w, a: c.result.update(
                source_context={"skill_activation_ids": [facts[2][0].result["activation_id"]]}
            ),
        ),
        (
            "canonical_group_sibling",
            "canonical_context_original_group_changed",
            lambda f, m, o, c, r, w, a: setattr(
                c,
                "turn_id",
                next(item.turn_id for item in facts[1] if item.id == facts[2][0].tool_call_id),
            ),
        ),
        (
            "unrecognized_ask_name",
            "canonical_context_released_source_returned",
            lambda f, m, o, c, r, w, a: w["function"].update(name="runtime__untrusted_ask"),
        ),
        (
            "ordinary_ai_content",
            "canonical_context_released_source_returned",
            lambda f, m, o, c, r, w, a: r.update(content=facts[7]),
        ),
        (
            "summary_content",
            "canonical_context_released_source_returned",
            lambda f, m, o, c, r, w, a: m.append(
                {"role": "user", "content": "summary: " + facts[7]}
            ),
        ),
        (
            "notes_content",
            "canonical_context_released_source_returned",
            lambda f, m, o, c, r, w, a: m.append(
                {
                    "role": "user",
                    "content": json.dumps({"task_working_notes": [{"text": facts[6]}]}),
                }
            ),
        ),
        (
            "unrecognized_result",
            "canonical_context_released_source_returned",
            lambda f, m, o, c, r, w, a: m.append(
                {
                    "role": "tool",
                    "tool_call_id": "untrusted",
                    "content": json.dumps({"content": facts[7]}),
                }
            ),
        ),
        (
            "direct_read_result",
            "canonical_context_released_source_returned",
            lambda f, m, o, c, r, w, a: m.extend(deepcopy(source_groups["resource"])),
        ),
        (
            "direct_body_instructions",
            "canonical_context_released_source_returned",
            lambda f, m, o, c, r, w, a: m.extend(deepcopy(source_groups["body"])),
        ),
    ]
    rejected = []
    for name, expected, mutate in mutations:
        sample = copied()
        mutate(*sample)
        if name == "unrecognized_result":
            sample[1].append(
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "untrusted",
                            "type": "function",
                            "function": {"name": "runtime__untrusted", "arguments": "{}"},
                        }
                    ],
                }
            )
        with pytest.raises(AssertionError) as caught:
            assert_requests(*sample[0])
        assert type(caught.value) is AssertionError
        assert str(caught.value).splitlines()[0] == expected, name
        rejected.append({"case": name, "error": expected})
    assert {index: records(facts[index]) for index in indices} == before
    return rejected, before


@pytest.mark.parametrize(
    "question_copy,activation_type,final_release",
    [("resource", str, False), ("body", int, True), ("resource-form", int, True)],
    ids=["resource-string-active", "body-integer-released", "resource-form-integer-released"],
)
def test_worker_skill_canonical_preserves_answered_question_copy(
    controlled_case, monkeypatch, question_copy, activation_type, final_release
):
    test_worker_skill_canonical_frozen_versions_and_trace(
        controlled_case, monkeypatch, 0, final_release, activation_type, question_copy
    )
    controlled_case.report()


@pytest.mark.parametrize(
    "missing_step,expected_error",
    [
        ("duplicate_load", "canonical_duplicate_load_missing"),
        ("post_reload_read", "canonical_context_no_post_reload_read"),
    ],
)
def test_worker_skill_canonical_rejects_missing_required_step(
    controlled_case, monkeypatch, missing_step, expected_error
):
    case = controlled_case

    def first(accepted):
        item = skill(case, accepted, "雨夜取证方法")
        replies = [
            calls(("search", "runtime__search_skills", {"query": "尾部唯一检索词"})),
            load(item),
            read("references/evidence.md"),
            read("references/evidence.md", call_id="early_read"),
        ]
        if missing_step != "duplicate_load":
            replies.append(load(item, "again"))
        return [*replies, calls(("question", "runtime__ask_user", QUESTION))]

    def second(accepted):
        return [
            calls(
                (
                    "release",
                    "runtime__release_skill",
                    {"activation_id": {"$result": ["load", "activation_id"]}},
                )
            ),
            load(skill(case, accepted, "雨夜取证方法"), "reload"),
            {"text": "未执行后置重读"},
        ]

    workers(case, monkeypatch, [first, second])
    with pytest.raises(AssertionError, match=expected_error):
        live.test_live_skill_canonical_reload_frozen_directory_and_trace(case, 0)


@pytest.mark.parametrize("withdrawal", ["selection", "forget"])
def test_worker_skill_withdrawal_keeps_live_source_assertions(
    controlled_case, monkeypatch, withdrawal
):
    case = controlled_case

    def first(accepted):
        return [
            load(skill(case, accepted, "撤回取证方法")),
            read("references/source.md"),
            calls(
                (
                    "note",
                    "runtime__record_task_observation",
                    {
                        "operation_id": {"$result": ["read", "receipt_operation_id"]},
                        "source_quote": {"$result": ["read", "content"]},
                        "note": {"$result": ["read", "content"]},
                    },
                )
            ),
            calls(("question", "runtime__ask_user", QUESTION)),
        ]

    workers(
        case,
        monkeypatch,
        [
            first,
            [
                calls(("policy", "runtime__get_generation_policy", {})),
                {"text": "仅按当前可用材料继续"},
            ],
        ],
    )
    live.test_live_skill_withdrawal_removes_body_resources_and_notes(case, 0, withdrawal)


def test_worker_parent_child_skill_scope_assertions(controlled_case, monkeypatch):
    case = controlled_case

    def replies(accepted):
        return {
            "routes": {
                "root": [
                    load(skill(case, accepted, "父级节奏方法"), "parent"),
                    calls(delegate("one")),
                    {"text": "已整合节奏建议"},
                ],
                "one": [
                    load(skill(case, accepted, "子级对白方法"), "child"),
                    {"text": "对白保留停顿"},
                ],
            }
        }

    workers(case, monkeypatch, [replies])
    live.test_live_parent_child_skill_activation_and_history_are_isolated(case, 0)


def test_worker_dynamic_settings_policy_and_quantity_assertions(controlled_case, monkeypatch):
    case = controlled_case
    workers(
        case,
        monkeypatch,
        [
            [calls(("question", "runtime__ask_user", QUESTION))],
            lambda accepted: [media(case, accepted), {"text": "实时策略已关闭"}],
            [{"text": "新指令镜头节奏"}],
            [{"text": "讨论对白节奏"}],
        ],
    )
    live.test_live_settings_freeze_realtime_media_policy_and_quantity_objects(case, 0)
