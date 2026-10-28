"""真实 MySQL 行锁下的认证 HTTP 读取与写入版本保护。"""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.domain import Project
from short_drama.domain.identity.accounts.user import User
from short_drama.main import create_app
from short_drama.service.identity.authentication.auth import PASSWORDS

pytestmark = pytest.mark.integration
PASSWORD = "read-concurrency-fixture-password"


class NoObjectIO:
    """此测试不需要对象服务；认证、数据库与业务路由均使用真实实现。"""

    def __init__(self, settings: Settings) -> None:
        pass

    def close(self) -> None:
        pass


@pytest.fixture
def authenticated_client(mysql_engine, db_session):
    with db_session.begin():
        db_session.execute(
            update(User).where(User.id == 1).values(password_hash=PASSWORDS.hash(PASSWORD))
        )
    url = mysql_engine.url
    settings = Settings(
        db_name=url.database,
        db_host=url.host,
        db_port=url.port or 3306,
        db_user=url.username,
        db_password=url.password or "",
        auth_enabled=True,
        agent_enabled=False,
        auth_cookie_secure=False,
        public_origin="http://testserver",
    )
    with (
        patch("short_drama.main.MinioStorage", NoObjectIO),
        TestClient(create_app(settings)) as client,
    ):
        client.headers["Origin"] = "http://testserver"
        assert client.get("/api/v1/projects").status_code == 401
        response = client.post(
            "/api/v1/auth/login", json={"username": "legacy_fixture", "password": PASSWORD}
        )
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = client.cookies["sd_csrf"]
        assert client.get("/api/v1/auth/me").json()["user"]["id"] == "1"
        yield client


@pytest.mark.parametrize("mutation", ["rename", "content"])
def test_editor_reads_finish_before_write_lock_release(
    mysql_engine, authenticated_client, mutation
):
    client = authenticated_client
    locked_model = Project
    project = client.post("/api/v1/projects", json={"name": "Concurrent reads"})
    assert project.status_code == 201, project.text
    project_id = project.json()["id"]
    root = f"/api/v1/projects/{project_id}"
    script = client.put(
        root + "/script",
        json={"content_version": "1", "script_id": None, "content": "Committed script"},
    )
    assert script.status_code == 200, script.text
    assert script.json()["content_version"] == "2"
    script_id = script.json()["script"]["id"]
    assembly = client.post(root + "/assembly/initialize")
    assert assembly.status_code == 200, assembly.text
    assert assembly.json()["clips"] == []
    locked_id = int(project_id)
    writer_lock_queried = Event()
    app_engine = client.app.state.session_factory.kw["bind"]

    def observe_writer_lock(_connection, _cursor, statement, _parameters, context, _many):
        compiled = getattr(context.compiled, "statement", None)
        if (
            "FOR UPDATE" in statement
            and hasattr(compiled, "get_final_froms")
            and any(
                getattr(table, "name", None) == locked_model.__tablename__
                for table in compiled.get_final_froms()
            )
        ):
            writer_lock_queried.set()

    def write_script():
        return client.put(
            root + "/script",
            json={"content_version": "2", "script_id": script_id, "content": "Concurrent edit"},
        )

    with ThreadPoolExecutor(max_workers=2) as executor, Session(mysql_engine) as holder:
        try:
            holder.begin()
            assert (
                holder.scalar(
                    select(locked_model).where(locked_model.id == locked_id).with_for_update()
                )
                is not None
            )
            if mutation == "content":
                holder.execute(
                    update(Project).where(Project.id == locked_id).values(content_version=3)
                )
            else:
                holder.execute(
                    update(Project).where(Project.id == locked_id).values(name="Pending rename")
                )
            # 真正持有未提交的InnoDB行锁；读取必须在holder提交前完成。
            for suffix in ["/writing", "", "/shots", "/assembly"]:
                response = executor.submit(client.get, root + suffix).result(timeout=5)
                assert response.status_code == 200, response.text
                document = response.json()
                if suffix == "/writing":
                    assert document["content_version"] == "2"
                    assert document["editing_script"]["content"] == "Committed script"
                elif suffix == "":
                    assert document["name"] == "Concurrent reads"
                elif suffix == "/shots":
                    assert document["items"] == []
                else:
                    assert document["clips"] == []
                assert holder.in_transaction()
            # 其他读取已完成；只监听HTTP写入使用的独立应用Engine。
            event.listen(app_engine, "before_cursor_execute", observe_writer_lock)
            writer = executor.submit(write_script)
            assert writer_lock_queried.wait(timeout=2)
            # 写请求已抵达目标FOR UPDATE，而非认证或排队阶段。
            assert not writer.done()
            with pytest.raises(TimeoutError):
                writer.result(timeout=0.25)
            holder.commit()
            response = writer.result(timeout=3)
        finally:
            holder.rollback()
            if event.contains(app_engine, "before_cursor_execute", observe_writer_lock):
                event.remove(app_engine, "before_cursor_execute", observe_writer_lock)
    if mutation == "content":
        assert response.status_code == 409, response.text
        assert (
            client.get(root + "/writing").json()["editing_script"]["content"] == "Committed script"
        )
    else:
        assert response.status_code == 200, response.text
        assert response.json()["content_version"] == "3"
