"""Real MySQL token scope, revocation, and public MCP client protocol."""

import asyncio
from importlib import import_module, util
from uuid import uuid4

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import func, select

from short_drama.core.config import Settings
from short_drama.db.session import session_factory
from short_drama.domain import AgentRun, Asset, AuditEvent, BusinessToolInvocation
from short_drama.main import create_app
from short_drama.service.projects.management.project import ProjectService
from tests.integration.standard.assets.test_asset_image_generation import flow as flow

pytestmark = pytest.mark.integration


def test_mcp_schema_readiness_matches_generated_mysql(mysql_engine):
    from short_drama.db.readiness import inspect_mcp_schema

    with mysql_engine.connect() as connection:
        state = inspect_mcp_schema(connection)
    assert state["status"] == "ready", state


def test_startup_rejects_missing_mcp_schema_with_agent_disabled(
    migration_mysql_engine, monkeypatch
):
    from fastapi.testclient import TestClient

    with migration_mysql_engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE mcp_access_tokens DROP COLUMN token_hint")
    monkeypatch.setattr("short_drama.main.build_engine", lambda _settings: migration_mysql_engine)
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))
    with pytest.raises(RuntimeError, match="MCP schema incomplete"):
        with TestClient(app):
            pass


def token_service(session):
    try:
        present = util.find_spec("short_drama.service.identity.accounts.mcp_token") is not None
    except ModuleNotFoundError:
        present = False
    assert present, "Account MCP token service is missing"
    service = import_module("short_drama.service.identity.accounts.mcp_token")
    from short_drama.agent.tools.registry import TOOL_SPECS

    return service.McpTokenService(session, tool_specs=TOOL_SPECS)


def test_secret_authenticates_then_delete_revokes_without_deleting_row(db_session):
    project = ProjectService(db_session).create_project({"name": "令牌范围"})
    factory = session_factory(db_session.get_bind())
    # Assert the missing product boundary before importing its input type.
    token_service(db_session)
    schema = import_module("short_drama.schemas.identity.accounts.mcp_token")
    created = token_service(db_session).create(
        schema.McpTokenCreate(
            name="工具调用",
            workbench_enabled=True,
        ),
        "secret-authenticates",
    )
    with factory() as session:
        principal = token_service(session).authenticate(created.secret, "127.0.0.1", "request-1")
    assert principal.actor.user_id == 1
    assert principal.token_id == created.id
    assert token_service(db_session).current_project_ids(principal.actor.user_id) == frozenset(
        {project.id}
    )
    token_service(db_session).delete(created.id)
    from short_drama.core.exceptions import BusinessError

    with factory() as session, pytest.raises(BusinessError) as rejected:
        token_service(session).authenticate(created.secret, "127.0.0.1", "request-2")
    assert rejected.value.status_code == 401


def test_account_token_creation_is_idempotent_without_projects(db_session, monkeypatch):
    from datetime import timedelta

    from short_drama.core.exceptions import Conflict
    from short_drama.core.time import utcnow
    from short_drama.schemas.identity.accounts.mcp_token import McpTokenCreate
    from short_drama.service.identity.accounts import mcp_token as module

    service = token_service(db_session)
    initial_time = utcnow()
    monkeypatch.setattr(module, "utcnow", lambda: initial_time)
    values = McpTokenCreate(
        name="无项目也可接入", workbench_enabled=False, media_generation_enabled=True
    )
    first = service.create(values, "one-intent")
    monkeypatch.setattr(module, "utcnow", lambda: initial_time + timedelta(days=1))
    again = service.create(values, "one-intent")
    assert first.secret and again.secret is None
    assert first.id == again.id and first.expires_at == again.expires_at
    assert first.expires_at == initial_time + timedelta(days=90)
    assert len(first.tool_names) == 8
    assert service.current_project_ids(1) == frozenset()
    with pytest.raises(Conflict):
        service.create(McpTokenCreate(name="不同意图"), "one-intent")
    permanent = service.create(McpTokenCreate(name="永久", expires_at=None), "permanent")
    assert permanent.expires_at is None


def test_mcp_generation_and_real_image_content_survive_token_revocation_without_an_assistant_run(
    flow,
):
    import base64
    from contextlib import contextmanager
    from io import BytesIO
    from threading import get_ident
    from types import SimpleNamespace

    from sqlalchemy import event

    from short_drama.domain import AsyncTask
    from short_drama.service.media.image_groups.groups import ImageGroupService
    from tests.integration.standard.assets.test_asset_image_generation import create_asset, drain

    asset = create_asset(flow)
    submitter = create_token(
        flow.session, flow.project.id, tools=["workbench__submit_generation_batch"]
    )
    reader = create_token(
        flow.session,
        flow.project.id,
        tools=[
            "workbench__get_generation_status",
            "workbench__read_image_group",
            "workbench__read_image_content",
        ],
    )
    app = mcp_app(flow.factory)
    current = {}
    transactions = set()
    engine = flow.session.get_bind()

    def begin(connection):
        transactions.add(get_ident())

    def end(connection):
        transactions.discard(get_ident())

    @contextmanager
    def open_image(bucket, key):
        assert get_ident() not in transactions, "image I/O held a SQL transaction"
        yield BytesIO(flow.storage.objects[bucket, key][0])

    async def scenario():
        async with app.router.lifespan_context(app):
            app.state.storage = SimpleNamespace(settings=flow.settings, open=open_image)
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                headers={"Authorization": "Bearer " + submitter.secret},
                follow_redirects=True,
            ) as http:
                async with streamable_http_client(
                    "http://testserver/mcp", http_client=http, terminate_on_close=False
                ) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=10) as client:
                        await client.initialize()
                        accepted = await client.call_tool(
                            "workbench__submit_generation_batch",
                            {
                                "request_id": "image-admission",
                                "arguments": {
                                    "project_id": str(flow.project.id),
                                    "requests": [
                                        {
                                            "target_kind": "asset",
                                            "target_id": str(asset.id),
                                            "row_version": str(asset.row_version),
                                            "model_config_id": str(flow.config.id),
                                            "model_version": str(flow.config.row_version),
                                            "prompt": "controlled local image",
                                            "count": 1,
                                        }
                                    ],
                                },
                            },
                        )
                        assert not accepted.is_error, accepted
                        assert not flow.provider.calls
                        generation_id = int(
                            accepted.structured_content["items"][0]["generation_id"]
                        )
                        with flow.factory() as session:
                            version = session.get(AsyncTask, generation_id).message_version
                        flow.executor.execute(generation_id, version)
                        assert len(flow.provider.calls) == 1
                        token_service(flow.session).delete(submitter.id)
                        response = await http.post(
                            "http://testserver/mcp/",
                            json={"jsonrpc": "2.0", "id": 100, "method": "tools/list"},
                        )
                        assert response.status_code == 401
            assert drain(flow, generation_id) == "succeeded"
            current.update(ImageGroupService(flow.session).current("asset", asset.id))
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                headers={"Authorization": "Bearer " + reader.secret},
                follow_redirects=True,
            ) as http:
                async with streamable_http_client(
                    "http://testserver/mcp", http_client=http, terminate_on_close=False
                ) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=10) as client:
                        await client.initialize()
                        status = await client.call_tool(
                            "workbench__get_generation_status",
                            {
                                "project_id": str(flow.project.id),
                                "generation_ids": [str(generation_id)],
                            },
                        )
                        assert not status.is_error and status.structured_content["settled"]
                        slot = current["items"][0]
                        result = await client.call_tool(
                            "workbench__read_image_content",
                            {
                                "project_id": str(flow.project.id),
                                "target_kind": "asset",
                                "target_id": str(asset.id),
                                "group_id": current["id"],
                                "item_id": slot["id"],
                                "media_id": slot["media_id"],
                                "purpose": "读取真实PNG像素",
                            },
                        )
                        assert not result.is_error, result
                        image = next(item for item in result.content if item.type == "image")
                        assert image.mime_type == "image/png"
                        data = base64.b64decode(image.data)
                        assert data.startswith(b"\x89PNG\r\n\x1a\n")
                        assert data in [obj[0] for obj in flow.storage.objects.values()]
            with flow.factory() as session:
                assert session.get(AsyncTask, generation_id).status == "succeeded"
                assert session.scalar(select(func.count()).select_from(AgentRun)) == 0
                assert session.scalar(select(func.count()).select_from(BusinessToolInvocation)) == 1
                image_audit = session.scalar(
                    select(AuditEvent).where(
                        AuditEvent.object_type == "workbench__read_image_content",
                        AuditEvent.action == "read",
                    )
                )
                assert (
                    image_audit.token_id == reader.id and image_audit.project_id == flow.project.id
                )

    for name, listener in (("begin", begin), ("commit", end), ("rollback", end)):
        event.listen(engine, name, listener)
    try:
        asyncio.run(scenario())
    finally:
        for name, listener in (("begin", begin), ("commit", end), ("rollback", end)):
            event.remove(engine, name, listener)


@pytest.mark.parametrize("token_state", ["active", "revoked", "expired"])
def test_mcp_worker_checks_its_token_before_sending_independently_of_assistant_settings(
    flow, monkeypatch, token_state
):
    from datetime import timedelta

    from short_drama.domain import AsyncTask
    from short_drama.schemas.assistant.operations.media import SubmitGenerationBatch
    from short_drama.service.assistant.operations.invocation import (
        BusinessInvocation,
        BusinessInvocationService,
        InvocationScope,
    )
    from short_drama.service.assistant.operations.media import submit_generation_batch_locked
    from short_drama.service.assistant.settings.preferences import AssistantSettingsService
    from tests.integration.standard.assets.test_asset_image_generation import create_asset, drain

    settings = AssistantSettingsService(flow.session, flow.settings)
    current = settings.get_settings()
    settings.update_settings(
        {
            "settings_version": str(current.settings_version),
            "workbench_enabled": False,
            "media_generation_enabled": False,
        }
    )
    asset = create_asset(flow)
    issued = create_token(
        flow.session, flow.project.id, tools=["workbench__submit_generation_batch"]
    )
    identity = BusinessInvocation(
        actor=flow.session.info["actor"],
        source="mcp",
        request_id=f"mcp-worker-{token_state}",
        token_id=issued.id,
        scope=InvocationScope(
            frozenset({flow.project.id}), frozenset(issued.tool_names), "project", {}
        ),
    )
    values = SubmitGenerationBatch(
        project_id=str(flow.project.id),
        requests=[
            {
                "target_kind": "asset",
                "target_id": str(asset.id),
                "row_version": str(asset.row_version),
                "model_config_id": str(flow.config.id),
                "model_version": str(flow.config.row_version),
                "prompt": "controlled token authorization image",
                "count": 1,
            }
        ],
    )
    with flow.factory() as session:
        result = BusinessInvocationService(session, flow.settings).execute(
            identity,
            "workbench__submit_generation_batch",
            values,
            submit_generation_batch_locked,
            mutating=True,
        )
    task_id = int(result["items"][0]["generation_id"])
    with flow.factory() as session:
        version = session.get(AsyncTask, task_id).message_version
    claim = flow.executor.store.claim_execution(task_id, version)
    assert claim is not None and not flow.provider.calls
    task, record, lease = claim
    if token_state == "revoked":
        token_service(flow.session).delete(issued.id)
    elif token_state == "expired":
        monkeypatch.setattr(
            "short_drama.service.assistant.operations.authorization.utcnow",
            lambda: issued.expires_at + timedelta(seconds=1),
        )
    # 先拿到 Worker 租约再改变令牌，证明真正外发处也读取当前授权。
    flow.executor._generate(task, record, version, lease)
    if token_state == "active":
        assert drain(flow, task_id) == "succeeded"
        assert len(flow.provider.calls) == 1
    else:
        with flow.factory() as session:
            task = session.get(AsyncTask, task_id)
            assert task.status == "cancelled"
            assert task.error["code"] == "mcp_authentication_required"
        assert not flow.provider.calls


def test_other_accounts_cannot_manage_or_authorize_foreign_tokens_or_projects(db_session):
    from short_drama.core.exceptions import BusinessError
    from short_drama.core.time import utcnow
    from short_drama.domain import User
    from short_drama.schemas.identity.accounts.mcp_token import McpTokenCreate
    from tests.support.integration.identity import actor

    project = ProjectService(db_session).create_project({"name": "私有作品"})
    token = create_token(db_session, project.id, tools=["workbench__read_project"])
    factory = session_factory(db_session.get_bind())
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="other",
                display_name="Other",
                email="other@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    with factory() as session:
        session.info["actor"] = actor(2)
        service = token_service(session)
        assert service.list() == []
        assert service.current_project_ids(2) == frozenset()
        with pytest.raises(BusinessError) as rejected:
            service.delete(token.id)
        assert rejected.value.status_code == 404
        created = service.create(McpTokenCreate(name="无作品令牌"), "empty-projects")
        assert len(created.tool_names) == 8
        assert "project_ids" not in created.model_dump()


def test_expiry_account_status_ip_and_new_projects_use_current_account_access(
    db_session, monkeypatch
):
    from datetime import UTC, timedelta

    from short_drama.core.exceptions import BusinessError
    from short_drama.core.time import utcnow
    from short_drama.domain import User
    from short_drama.service.identity.accounts import mcp_token as module

    project = ProjectService(db_session).create_project({"name": "原作品"})
    boundary = utcnow() + timedelta(minutes=5)
    token = create_token(
        db_session,
        project.id,
        tools=["workbench__read_project"],
        expires_at=boundary.replace(tzinfo=UTC),
        allowed_ips=["192.0.2.4/24", "2001:db8::/32"],
    )
    later = ProjectService(db_session).create_project({"name": "新作品"})
    factory = session_factory(db_session.get_bind())
    with factory() as session:
        service = token_service(session)
        for address in ("192.0.2.8", "2001:db8::9"):
            principal = service.authenticate(token.secret, address, "allowed")
            assert service.current_project_ids(principal.actor.user_id) == frozenset(
                {project.id, later.id}
            )
        with pytest.raises(BusinessError) as rejected:
            service.authenticate(token.secret, "198.51.100.8", "denied")
        assert rejected.value.status_code == 403
        monkeypatch.setattr(module, "utcnow", lambda: boundary - timedelta(microseconds=1))
        assert service.authenticate(token.secret, "192.0.2.8", "last-microsecond")
        monkeypatch.setattr(module, "utcnow", lambda: boundary)
        with pytest.raises(BusinessError) as rejected:
            service.authenticate(token.secret, "192.0.2.8", "at-expiry")
        assert rejected.value.status_code == 401
        monkeypatch.undo()
    for values in ({"status": "disabled"}, {"status": "active", "email_verified_at": None}):
        with db_session.get_bind().begin() as connection:
            connection.execute(User.__table__.update().where(User.id == 1).values(**values))
        with factory() as session, pytest.raises(BusinessError) as rejected:
            token_service(session).authenticate(token.secret, "192.0.2.8", "account-disabled")
        assert rejected.value.status_code == 401


def create_token(session, project_id, *, tools, name="外部调用", **kwargs):
    from short_drama.schemas.identity.accounts.mcp_token import McpTokenCreate

    return token_service(session).create(
        McpTokenCreate(
            name=name,
            workbench_enabled=any(
                name in tools
                for name in (
                    "workbench__apply_standard_patch",
                    "workbench__apply_canvas_patch",
                    "workbench__set_image_group_primary",
                    "workbench__submit_generation_batch",
                )
            ),
            media_generation_enabled="workbench__submit_generation_batch" in tools,
            **kwargs,
        ),
        uuid4().hex,
    )


def mcp_app(factory):
    url = factory.kw["bind"].url
    settings = Settings(
        _env_file=None,
        auth_enabled=False,
        agent_enabled=False,
        db_host=url.host,
        db_port=url.port,
        db_user=url.username,
        db_password=url.password,
        db_name=url.database,
    )
    return create_app(settings)


def test_real_client_scoped_catalog_business_idempotency_read_audit_and_session_revocation(
    db_session,
):
    from jsonschema import Draft202012Validator

    from short_drama.domain import Project

    project = ProjectService(db_session).create_project({"name": "授权的作品"})
    outside = ProjectService(db_session).create_project({"name": "未选作品"})
    tools = [
        "workbench__list_projects",
        "workbench__read_project",
        "workbench__read_assets",
        "workbench__read_storyboard",
        "workbench__apply_standard_patch",
    ]
    # This fails until each new catalog option is backed by a real business handler.
    first = create_token(db_session, project.id, tools=tools)
    second = create_token(db_session, project.id, tools=tools)
    factory = session_factory(db_session.get_bind())
    app = mcp_app(factory)
    before_runs = 0
    with factory() as session:
        before_runs = session.scalar(select(func.count()).select_from(AgentRun))
    args = {
        "project_id": str(project.id),
        "patch": {
            "actions": [
                {
                    "action": "create_asset",
                    "key": "hero",
                    "values": {"name": "外部主角", "kind": "character"},
                }
            ]
        },
    }
    sessions = []

    async def scenario():
        async with app.router.lifespan_context(app):

            async def call(token, replay=False):
                async def capture(response):
                    identifier = response.headers.get("mcp-session-id")
                    if identifier:
                        sessions.append(identifier)

                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    headers={"Authorization": "Bearer " + token.secret},
                    follow_redirects=True,
                    event_hooks={"response": [capture]},
                ) as http:
                    async with streamable_http_client(
                        "http://testserver/mcp", http_client=http, terminate_on_close=False
                    ) as (read, write):
                        async with ClientSession(read, write, read_timeout_seconds=10) as client:
                            await client.initialize()
                            listed = await client.list_tools()
                            assert {item.name for item in listed.tools} == set(token.tool_names)
                            write_tool = next(
                                item
                                for item in listed.tools
                                if item.name == "workbench__apply_standard_patch"
                            )
                            validator = Draft202012Validator(write_tool.input_schema)
                            assert validator.is_valid({"request_id": "probe", "arguments": args})
                            assert not validator.is_valid({"arguments": args})
                            assert not validator.is_valid(
                                {"request_id": "probe", "arguments": args | {"owner_id": "2"}}
                            )
                            projects = await client.call_tool("workbench__list_projects", {})
                            assert not projects.is_error, projects
                            assert set(
                                item["id"] for item in projects.structured_content["items"]
                            ) == {str(project.id), str(outside.id)}
                            assert projects.structured_content["total"] == 2
                            another = await client.call_tool(
                                "workbench__read_project", {"project_id": str(outside.id)}
                            )
                            assert not another.is_error and another.structured_content["id"] == str(
                                outside.id
                            )
                            denied = await client.call_tool("runtime__load_skill", {})
                            assert denied.is_error and "403" in denied.content[0].text
                            result = await client.call_tool(
                                "workbench__apply_standard_patch",
                                {"request_id": "跨令牌请求-1", "arguments": args},
                            )
                            assert not result.is_error, result
                            assets = await client.call_tool(
                                "workbench__read_assets", {"project_id": str(project.id)}
                            )
                            assert not assets.is_error and assets.structured_content["total"] == 1
                            assert assets.structured_content["items"][0]["name"] == "外部主角"
                            shots = await client.call_tool(
                                "workbench__read_storyboard", {"project_id": str(project.id)}
                            )
                            assert not shots.is_error and shots.structured_content["total"] == 0
                            if replay:
                                changed = await client.call_tool(
                                    "workbench__apply_standard_patch",
                                    {
                                        "request_id": "跨令牌请求-1",
                                        "arguments": args
                                        | {
                                            "patch": {
                                                "actions": [
                                                    {
                                                        "action": "create_asset",
                                                        "key": "hero",
                                                        "values": {
                                                            "name": "篡改",
                                                            "kind": "character",
                                                        },
                                                    }
                                                ]
                                            }
                                        },
                                    },
                                )
                                assert changed.is_error and "409" in changed.content[0].text
                            return result.structured_content

            original = await call(first)
            repeated = await call(second, replay=True)
            assert repeated == original
            assert sessions, "The real stateful protocol must issue a session identity"
            with factory() as session:
                session.info["actor"] = db_session.info["actor"]
                token_service(session).delete(first.id)
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as raw:
                for method in ("initialize", "tools/list", "tools/call", "ping"):
                    response = await raw.post(
                        "http://testserver/mcp/",
                        json={"jsonrpc": "2.0", "id": 98, "method": method},
                        headers={
                            "Authorization": "Bearer " + first.secret,
                            "Mcp-Session-Id": sessions[0],
                        },
                    )
                    assert response.status_code == 401

    asyncio.run(scenario())
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Asset)) == 1
        assert session.scalar(select(func.count()).select_from(BusinessToolInvocation)) == 1
        assert session.scalar(select(func.count()).select_from(AgentRun)) == before_runs
        audits = list(session.scalars(select(AuditEvent).where(AuditEvent.source == "mcp")))
        assert any(
            row.action == "create"
            and row.object_type == "assets"
            and row.token_id == first.id
            and row.request_id == "跨令牌请求-1"
            for row in audits
        )
        assert any(
            row.action == "read"
            and row.object_type == "mcp_access_tokens"
            and row.object_id == str(second.id)
            and row.project_id is None
            for row in audits
        )
        assert all(row.token_id in {first.id, second.id} for row in audits)
        assert session.get(Project, outside.id) is not None


@pytest.mark.parametrize("write", [False, True])
def test_concurrent_protocol_requests_keep_account_token_and_tool_scope_local(
    db_session, monkeypatch, write
):
    from threading import Barrier

    from short_drama.core.time import utcnow
    from short_drama.domain import User
    from short_drama.service.assistant.operations.invocation import BusinessInvocationService
    from tests.support.integration.identity import actor

    first_project = ProjectService(db_session).create_project({"name": "First account"})
    tool_name = "workbench__apply_standard_patch" if write else "workbench__read_project"
    first_names = [tool_name]
    first = create_token(db_session, first_project.id, tools=first_names)
    factory = session_factory(db_session.get_bind())
    with factory.begin() as session:
        session.add(
            User(
                id=2,
                username="parallel",
                display_name="Parallel",
                email="parallel@example.test",
                password_hash="test-only",
                status="active",
                email_verified_at=utcnow(),
                created_at=utcnow(),
            )
        )
    with factory() as session:
        session.info["actor"] = actor(2)
        second_project = ProjectService(session).create_project({"name": "Second account"})
        second_names = [tool_name, "workbench__read_assets"]
        second = create_token(session, second_project.id, tools=second_names)
    app = mcp_app(factory)
    overlap = Barrier(2)
    execute = BusinessInvocationService.execute

    def overlapping_execute(service, *args, **kwargs):
        assert not service.session.in_transaction()
        overlap.wait(timeout=10)
        return execute(service, *args, **kwargs)

    monkeypatch.setattr(BusinessInvocationService, "execute", overlapping_execute)

    async def call(token, project, names):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": "Bearer " + token.secret},
            follow_redirects=True,
        ) as http:
            async with streamable_http_client(
                "http://testserver/mcp", http_client=http, terminate_on_close=False
            ) as (read, write_stream):
                async with ClientSession(read, write_stream, read_timeout_seconds=20) as client:
                    await client.initialize()
                    assert {tool.name for tool in (await client.list_tools()).tools} == set(
                        token.tool_names
                    )
                    arguments = {"project_id": str(project.id)}
                    if write:
                        arguments = {
                            "request_id": "并发首次业务写入-相同字符串",
                            "arguments": {
                                **arguments,
                                "patch": {
                                    "actions": [
                                        {
                                            "action": "create_asset",
                                            "key": "hero",
                                            "values": {"kind": "character", "name": project.name},
                                        }
                                    ]
                                },
                            },
                        }
                    result = await client.call_tool(tool_name, arguments)
                    assert not result.is_error, result
                    if write:
                        assert len(result.structured_content["result_refs"]) == 1
                        assert result.structured_content["result_refs"][0]["kind"] == "asset"
                        return result.structured_content["project_id"]
                    assert result.structured_content["name"] == project.name
                    return result.structured_content["id"]

    async def scenario():
        async with app.router.lifespan_context(app):
            projects = await asyncio.wait_for(
                asyncio.gather(
                    call(first, first_project, first_names),
                    call(second, second_project, second_names),
                ),
                timeout=30,
            )
            assert set(projects) == {str(first_project.id), str(second_project.id)}

    asyncio.run(scenario())
    with factory() as session:
        reads = list(
            session.scalars(
                select(AuditEvent).where(
                    AuditEvent.object_type == ("assets" if write else tool_name),
                    AuditEvent.source == "mcp",
                    *([AuditEvent.action == "create"] if write else []),
                )
            )
        )
        assert {(row.actor_user_id, row.project_id, row.token_id) for row in reads} == {
            (1, first_project.id, first.id),
            (2, second_project.id, second.id),
        }
        assert session.scalar(select(func.count()).select_from(BusinessToolInvocation)) == (
            2 if write else 0
        )
        if write:
            assert {(row.project_id, row.name) for row in session.scalars(select(Asset))} == {
                (first_project.id, first_project.name),
                (second_project.id, second_project.name),
            }


def test_http_ip_auth_ignores_untrusted_forwarding_and_rejects_invalid_trusted_chain(db_session):
    project = ProjectService(db_session).create_project({"name": "IP allowlist"})
    token = create_token(
        db_session, project.id, tools=["workbench__read_project"], allowed_ips=["192.0.2.0/24"]
    )
    factory = session_factory(db_session.get_bind())
    app = mcp_app(factory)
    app.state.session_factory = factory

    async def scenario():
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app, client=("198.51.100.7", 1234))
        ) as http:

            async def post(xff):
                return await http.post(
                    "http://testserver/mcp/",
                    json={},
                    headers={"Authorization": "Bearer " + token.secret, "X-Forwarded-For": xff},
                )

            assert (await post("192.0.2.8")).status_code == 403
            app.state.settings.mcp_trusted_proxy_cidrs = ["198.51.100.0/24"]
            assert (await post("192.0.2.8, bad-ip")).status_code == 400
            assert (await post("203.0.113.8")).status_code == 403
            token_service(db_session).delete(token.id)
            assert (await post("192.0.2.8")).status_code == 401

    asyncio.run(scenario())


def test_protocol_masks_unexpected_internal_errors_but_keeps_business_errors(
    db_session, monkeypatch
):
    from dataclasses import replace

    from short_drama.api.mcp import server

    project = ProjectService(db_session).create_project({"name": "Safe protocol errors"})
    token = create_token(db_session, project.id, tools=["workbench__read_project"])
    specs = server.get_mcp_tool_specs()

    def fail(*args):
        raise RuntimeError("private-diagnostic-marker")

    monkeypatch.setattr(
        server,
        "get_mcp_tool_specs",
        lambda: tuple(
            replace(spec, handler=fail) if spec.name == "workbench__read_project" else spec
            for spec in specs
        ),
    )
    app = mcp_app(session_factory(db_session.get_bind()))

    async def scenario():
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                headers={"Authorization": "Bearer " + token.secret},
                follow_redirects=True,
            ) as http:
                async with streamable_http_client(
                    "http://testserver/mcp", http_client=http, terminate_on_close=False
                ) as (read, write):
                    async with ClientSession(read, write, read_timeout_seconds=10) as client:
                        await client.initialize()
                        rejected = await client.call_tool(
                            "workbench__read_project", {"project_id": str(project.id + 1)}
                        )
                        assert rejected.is_error and "403" in rejected.content[0].text
                        failed = await client.call_tool(
                            "workbench__read_project", {"project_id": str(project.id)}
                        )
                        assert failed.is_error
                        assert "private-diagnostic-marker" not in failed.content[0].text

    asyncio.run(scenario())
