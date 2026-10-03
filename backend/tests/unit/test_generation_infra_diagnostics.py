"""Operations checks must stay read-only and tolerate prompt-only installations."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from short_drama.core.config import Settings

spec = importlib.util.spec_from_file_location(
    "generation_infra_diagnostics",
    Path(__file__).resolve().parents[2] / "scripts" / "check_generation_infra.py",
)
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


@pytest.mark.parametrize("schema_status", ["absent", "partial"])
def test_agent_not_ready_does_not_query_private_tables(monkeypatch, capsys, schema_status):
    monkeypatch.setattr(
        diagnostics,
        "inspect_agent_schema",
        lambda connection: {"status": schema_status, "gaps": ["agent_runs"]},
    )
    # No database methods exist: an aggregate query would fail this test.
    diagnostics.check_agent(object(), SimpleNamespace(agent_enabled=False, auth_enabled=True))
    output = capsys.readouterr().out
    assert f"Agent schema: {schema_status}" in output
    assert "aggregate checks skipped" in output


def test_ready_agent_checks_only_aggregate_state(monkeypatch, capsys):
    monkeypatch.setattr(
        diagnostics, "inspect_agent_schema", lambda connection: {"status": "ready", "gaps": []}
    )
    queries = []

    class Database:
        def execute(self, statement):
            queries.append(str(statement))
            return SimpleNamespace(all=lambda: [("unknown", 2), ("succeeded", 3)])

        def scalar(self, statement):
            queries.append(str(statement))
            return 2

    diagnostics.check_agent(Database(), SimpleNamespace(agent_enabled=False, auth_enabled=True))
    assert all(query.startswith("SELECT") and "COUNT(*)" in query for query in queries)
    assert not any(
        field in " ".join(queries).lower()
        for field in [
            "content",
            "request_messages",
            "arguments",
            "response",
            "config_snapshot",
            " id",
        ]
    )
    output = capsys.readouterr().out
    assert "Agent unknown turns: 2; never reset or resubmit automatically" in output
    assert "Agent expired active leases: 2" in output


@pytest.mark.parametrize("namespace", ["short_drama", "studio_dev"])
def test_broker_checks_are_passive_and_include_agent_after_missing_queue(
    monkeypatch, capsys, namespace
):
    declarations = []

    class MissingQueue(Exception):
        reply_code = 404

    class Channel:
        def queue_declare(self, *, queue, passive):
            declarations.append((queue, passive))
            if queue.endswith(".text"):
                raise MissingQueue("private broker detail must not be printed")
            return SimpleNamespace(message_count=0, consumer_count=1)

        def close(self):
            pass

    class Broker:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def connect(self):
            pass

        def channel(self):
            return Channel()

    monkeypatch.setattr(diagnostics, "Connection", Broker)
    diagnostics.check_broker(Settings(_env_file=None, generation_queue_namespace=namespace))
    assert len(declarations) == 6 and all(passive for _, passive in declarations)
    assert (f"{namespace}.tasks.agent", True) in declarations
    assert (f"{namespace}.tasks.render", True) in declarations
    assert "private broker detail" not in capsys.readouterr().out


def test_mysql_exception_details_are_suppressed_and_engine_disposed(monkeypatch, capsys):
    disposed = []

    class Engine:
        def connect(self):
            raise RuntimeError("private credential or endpoint")

        def dispose(self):
            disposed.append(True)

    monkeypatch.setattr(diagnostics, "build_engine", lambda settings: Engine())
    diagnostics.check_mysql(object())
    assert disposed == [True]
    assert capsys.readouterr().out == "MySQL check failed (RuntimeError); details suppressed\n"


def test_settings_validation_never_outputs_input_values(monkeypatch, capsys):
    def invalid_settings():
        raise ValueError("credential mistakenly entered as a setting value")

    monkeypatch.setattr(diagnostics, "Settings", invalid_settings)
    assert diagnostics.main() == 1
    assert capsys.readouterr().out == "Settings check failed (ValueError); details suppressed\n"


def test_engine_initialization_never_outputs_url(monkeypatch, capsys):
    def invalid_engine(settings):
        raise ValueError("connection URL or credential")

    monkeypatch.setattr(diagnostics, "build_engine", invalid_engine)
    diagnostics.check_mysql(object())
    assert capsys.readouterr().out == "MySQL check failed (ValueError); details suppressed\n"
