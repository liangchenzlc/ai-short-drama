"""管理边界替身验证：不连接 Docker/MySQL，也不代替真实集成验收。"""

import importlib.util
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import URL

spec = importlib.util.spec_from_file_location(
    "benchmark_database_under_test",
    Path(__file__).resolve().parents[2] / "scripts" / "benchmark_database.py",
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


@pytest.fixture
def settings():
    return SimpleNamespace(
        db_host="127.0.0.1",
        db_port=3306,
        db_user="fixture_app",
        database_url=URL.create(
            "mysql+pymysql", username="fixture_app", host="127.0.0.1", database="application"
        ),
    )


@pytest.fixture
def boundary(monkeypatch):
    calls = []
    commands = []
    urls = []
    failures = set()
    inspections = {"partial_revokes": "1", "grant_rows": "1"}

    class Engine:
        def dispose(self):
            calls.append("dispose")
            if "dispose" in failures:
                raise RuntimeError("pool cleanup failed")

    engine = Engine()

    def run(arguments, **kwargs):
        assert arguments[:2] == ["docker", "inspect"]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"3306/tcp": [{"HostIp": "127.0.0.1", "HostPort": "3306"}]}),
        )

    def manage(container, sql, operation, **kwargs):
        assert container == "fixture-mysql"
        action = sql.split()[0]
        if action == "SELECT":
            return inspections["partial_revokes" if "partial_revokes" in sql else "grant_rows"]
        calls.append(action)
        commands.append(sql)
        assert re.search(r"`short_drama_[a-f0-9]{32}_test`", sql.replace("\\_", "_"))
        assert "`application`" not in sql
        if action in failures:
            raise RuntimeError(f"{operation} failed")

    def create(url, **kwargs):
        urls.append(url)
        return engine

    monkeypatch.setattr(benchmark.subprocess, "run", run)
    monkeypatch.setattr(benchmark, "_docker_mysql", manage)
    monkeypatch.setattr(benchmark, "create_engine", create)
    monkeypatch.setattr(benchmark, "_load_schema", lambda engine: calls.append("schema"))
    monkeypatch.setattr("short_drama.db.session.configure_mysql", lambda engine: engine)
    return SimpleNamespace(
        calls=calls,
        commands=commands,
        urls=urls,
        failures=failures,
        engine=engine,
        inspections=inspections,
    )


@pytest.mark.parametrize(
    ("attribute", "value", "container"),
    [
        ("db_host", "remote.example.test", "fixture-mysql"),
        ("db_port", 3307, "fixture-mysql"),
        ("db_user", "fixture'account", "fixture-mysql"),
        ("db_host", "127.0.0.1", "fixture-mysql;invalid"),
    ],
)
def test_invalid_docker_boundary_rejects_before_ddl(
    settings, boundary, attribute, value, container
):
    setattr(settings, attribute, value)
    with (
        pytest.raises(ValueError),
        benchmark.isolated_database(settings, docker_container=container),
    ):
        pytest.fail("invalid Docker boundary reached database body")
    assert boundary.calls == []
    assert boundary.urls == []


@pytest.mark.parametrize(
    "configured",
    [None, "sqlite:///fixture_test", "mysql+pymysql://fixture_app@127.0.0.1/application"],
)
def test_invalid_test_url_rejects_before_database_connection(
    monkeypatch, settings, boundary, configured
):
    if configured is None:
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("TEST_DATABASE_URL", configured)
    with pytest.raises(ValueError), benchmark.isolated_database(settings):
        pytest.fail("invalid TEST_DATABASE_URL reached database body")
    assert boundary.calls == []
    assert boundary.urls == []


def test_normal_run_uses_fresh_random_schema_and_removes_temporary_authorization(
    settings, boundary
):
    for _ in range(2):
        with benchmark.isolated_database(settings, docker_container="fixture-mysql") as engine:
            assert engine is boundary.engine
    names = [url.database for url in boundary.urls]
    assert len(set(names)) == 2
    assert all(re.fullmatch(r"short_drama_[a-f0-9]{32}_test", name) for name in names)
    assert boundary.calls == ["CREATE", "GRANT", "schema", "dispose", "REVOKE", "DROP"] * 2


@pytest.mark.parametrize("partial_revokes", ["0", "1"])
def test_grant_and_revoke_target_only_the_exact_random_schema(settings, boundary, partial_revokes):
    boundary.inspections["partial_revokes"] = partial_revokes
    with benchmark.isolated_database(settings, docker_container="fixture-mysql"):
        pass
    name = boundary.urls[0].database
    pattern = name if partial_revokes == "1" else name.replace("_", "\\_")
    grants = [sql for sql in boundary.commands if sql.startswith(("GRANT", "REVOKE"))]
    assert len(grants) == 2
    assert all(f"ON `{pattern}`.*" in sql for sql in grants)


def test_unknown_grant_semantics_rejects_before_ddl(settings, boundary):
    boundary.inspections["partial_revokes"] = "unknown"
    with (
        pytest.raises(RuntimeError),
        benchmark.isolated_database(settings, docker_container="fixture-mysql"),
    ):
        pytest.fail("unknown grant semantics reached database body")
    assert boundary.calls == []


def test_failed_exact_grant_verification_still_cleans_up(settings, boundary):
    boundary.inspections["grant_rows"] = "0"
    with (
        pytest.raises(RuntimeError),
        benchmark.isolated_database(settings, docker_container="fixture-mysql"),
    ):
        pytest.fail("unverified grant reached database body")
    assert "REVOKE" in boundary.calls
    assert boundary.calls[-1] == "DROP"


def test_body_failure_still_revokes_and_drops(settings, boundary):
    with pytest.raises(RuntimeError, match="body failed"):
        with benchmark.isolated_database(settings, docker_container="fixture-mysql"):
            raise RuntimeError("body failed")
    assert "REVOKE" in boundary.calls
    assert boundary.calls[-1] == "DROP"


@pytest.mark.parametrize("failure", ["GRANT", "dispose", "REVOKE"])
def test_setup_or_cleanup_failure_cannot_leave_random_schema(settings, boundary, failure):
    boundary.failures.add(failure)
    with pytest.raises(RuntimeError):
        with benchmark.isolated_database(settings, docker_container="fixture-mysql"):
            pass
    # A failed GRANT can have taken effect before the administrator connection failed.
    assert "REVOKE" in boundary.calls
    assert boundary.calls[-1] == "DROP"


def test_uncertain_create_failure_still_attempts_drop(settings, boundary):
    boundary.failures.add("CREATE")
    with (
        pytest.raises(RuntimeError),
        benchmark.isolated_database(settings, docker_container="fixture-mysql"),
    ):
        pytest.fail("failed CREATE reached database body")
    assert boundary.calls == ["CREATE", "DROP"]


def test_admin_failure_never_echoes_subprocess_output(monkeypatch, capsys):
    monkeypatch.setattr(
        benchmark.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="private administrator output", stderr="private connection detail"
        ),
    )
    with pytest.raises(RuntimeError) as failure:
        benchmark._docker_mysql("fixture-mysql", "SELECT 1;", "test operation")
    assert "private" not in str(failure.value)
    assert capsys.readouterr() == ("", "")
