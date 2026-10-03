"""创建随机隔离 MySQL 库；可显式使用本机 Docker 管理权限运行集成测试。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

if TYPE_CHECKING:
    from short_drama.core.config import Settings


def _docker_mysql(
    container: str, sql: str, operation: str, *, missing_grant_ok: bool = False
) -> str:
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                container,
                "sh",
                "-c",
                'test -n "$MYSQL_ROOT_PASSWORD" && '
                'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -uroot --batch --skip-column-names',
            ],
            input=sql,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Docker MySQL {operation} timed out after 30 seconds") from None
    if result.returncode:
        if missing_grant_ok and re.search(r"\bERROR 1141\b", result.stderr):
            return ""
        # 不回显容器环境、连接串或管理员命令输出。
        raise RuntimeError(
            f"Docker MySQL {operation} failed (exit {result.returncode}); "
            "check container availability and its database administrator configuration"
        )
    return result.stdout.strip()


def _verify_local_container(settings: Settings, container: str) -> None:
    if settings.db_host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Docker isolation requires a loopback MySQL host")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", container):
        raise ValueError("Invalid Docker container name")
    if not re.fullmatch(r"[A-Za-z0-9_]+", settings.db_user):
        raise ValueError("Docker isolation requires an alphanumeric MySQL application account")
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", container],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode:
        raise RuntimeError("Cannot inspect the explicitly selected local MySQL container")
    bindings = json.loads(result.stdout).get("3306/tcp") or []
    if not any(
        item["HostIp"] in {"127.0.0.1", "::1"} and item["HostPort"] == str(settings.db_port)
        for item in bindings
    ):
        raise ValueError("Selected container's loopback MySQL port does not match Settings")


def _load_schema(engine: Engine) -> None:
    sql_path = next((Path(__file__).resolve().parents[2] / "docs").rglob("schema.mysql8.sql"))
    # 与 integration/conftest.py 相同：完整 DDL 无带分号的字符串或多行注释。
    source = re.sub(r"(?m)^\s*--.*$", "", sql_path.read_text(encoding="utf-8"))
    with engine.begin() as connection:
        for statement in source.split(";"):
            if statement.strip():
                connection.execute(text(statement))


@contextmanager
def isolated_database(
    settings: Settings, *, docker_container: str | None = None
) -> Iterator[Engine]:
    """仅写新建的随机 _test 库；退出时撤销临时授权，再独立删除库。"""
    from short_drama.db.session import configure_mysql

    database = f"short_drama_{uuid4().hex}_test"
    assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", database)
    creation_attempted = grant_attempted = False
    engine = server = None
    if docker_container:
        _verify_local_container(settings, docker_container)
        url = settings.database_url
        partial_revokes = _docker_mysql(
            docker_container, "SELECT @@GLOBAL.partial_revokes;", "grant semantics inspection"
        )
        if partial_revokes not in {"0", "1"}:
            raise RuntimeError("Cannot determine the container's database grant semantics")
        # partial_revokes=OFF时，数据库授权中的_是通配符，必须逐个转义。
        grant_database = database if partial_revokes == "1" else database.replace("_", "\\_")
    else:
        configured = os.environ.get("TEST_DATABASE_URL")
        if not configured:
            raise ValueError("Set TEST_DATABASE_URL or explicitly select --docker-container")
        url = make_url(configured)
        if url.drivername != "mysql+pymysql" or not (url.database or "").endswith("_test"):
            raise ValueError("TEST_DATABASE_URL must use mysql+pymysql and end in _test")
        server = create_engine(
            url.set(database=""),
            isolation_level="AUTOCOMMIT",
            hide_parameters=True,
            connect_args={"connect_timeout": 5},
        )
    try:
        create = f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
        # 先记录操作意图：超时/非零退出并不证明DDL没有生效。
        creation_attempted = True
        if docker_container:
            _docker_mysql(docker_container, create + ";", "isolated database creation")
        else:
            with server.connect() as connection:
                connection.exec_driver_sql(create)
        if docker_container:
            grant_attempted = True
            _docker_mysql(
                docker_container,
                f"GRANT ALL PRIVILEGES ON `{grant_database}`.* TO '{settings.db_user}'@'%';",
                "isolated database grant",
            )
            grant_rows = _docker_mysql(
                docker_container,
                "SELECT COUNT(*) FROM mysql.db WHERE "
                f"HEX(Db)='{grant_database.encode().hex()}' "
                f"AND User='{settings.db_user}' AND Host='%';",
                "exact database grant verification",
            )
            if grant_rows != "1":
                raise RuntimeError("Temporary grant does not target the exact isolated schema")
        engine = configure_mysql(
            create_engine(url.set(database=database), pool_pre_ping=True, hide_parameters=True)
        )
        _load_schema(engine)
        yield engine
    finally:
        assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", database)
        try:
            if engine is not None:
                engine.dispose()
        finally:
            try:
                if grant_attempted:
                    _docker_mysql(
                        docker_container,
                        f"REVOKE ALL PRIVILEGES ON `{grant_database}`.* "
                        f"FROM '{settings.db_user}'@'%';",
                        "isolated database revoke",
                        missing_grant_ok=True,
                    )
            finally:
                try:
                    if creation_attempted:
                        if docker_container:
                            _docker_mysql(
                                docker_container,
                                f"DROP DATABASE IF EXISTS `{database}`;",
                                "isolated database drop",
                            )
                        else:
                            with server.connect() as connection:
                                connection.exec_driver_sql(f"DROP DATABASE IF EXISTS `{database}`")
                finally:
                    if server is not None:
                        server.dispose()


def main() -> int:
    """替换隔离库创建入口，保留正式测试的 Session、Actor 和业务调用。"""
    # 与 python -m pytest 保持一致，直接执行脚本时仍能导入仓库内的迁移模块。
    backend_root = str(Path(__file__).resolve().parents[1])
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)

    import pytest

    from short_drama.core.config import Settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-container", required=True)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    settings = Settings()

    class IsolatedContainerDatabase:
        @pytest.hookimpl
        def pytest_plugin_registered(self, plugin, plugin_name, manager):
            source = getattr(plugin, "__file__", None)
            if source is None:
                return
            path = Path(source).resolve()
            if path.name == "conftest.py" and path.parent.name == "integration":
                plugin.isolated_mysql_database = self.databases

        @staticmethod
        def databases():
            with isolated_database(settings, docker_container=args.docker_container) as engine:
                yield engine

    paths = args.pytest_args
    if paths[:1] == ["--"]:
        paths = paths[1:]
    return pytest.main(paths or ["tests/integration", "-q"], plugins=[IsolatedContainerDatabase()])


if __name__ == "__main__":
    raise SystemExit(main())
