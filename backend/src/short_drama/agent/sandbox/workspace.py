"""SDK lifecycle outside SQL transactions, with durable scope ownership."""

from __future__ import annotations

import asyncio
import logging
import shlex
from datetime import timedelta
from pathlib import PurePosixPath
from threading import RLock
from types import SimpleNamespace
from typing import TYPE_CHECKING

from opensandbox import SandboxManagerSync, SandboxSync
from opensandbox.config import ConnectionConfigSync
from opensandbox.exceptions import SandboxApiException
from opensandbox.models.sandboxes import Host, SandboxFilter, Volume
from sqlalchemy.orm import Session, sessionmaker

from short_drama.agent.core.access import LeaseLost, lock_run, may_decide
from short_drama.agent.core.events import TERMINAL
from short_drama.agent.core.fence import owns_execution
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.assistant.execution.scope import terminal_workspace_run_ids
from short_drama.domain.assistant.execution.scope import AgentExecutionScope
from short_drama.service.assistant.execution.scope import (
    sandbox_scope_locked,
    sandbox_scopes_locked,
)

if TYPE_CHECKING:
    from short_drama.agent.runtime.context import RuntimeContext
    from short_drama.agent.sandbox.backend import CommandBackend
    from short_drama.core.config import Settings

logger = logging.getLogger(__name__)


class SandboxWorkspaceService:
    def __init__(self, context: RuntimeContext | SimpleNamespace) -> None:
        self.context = context
        self._clients, self._backends, self._locks = {}, {}, {}
        self._guard = RLock()

    def lock(self, scope_id: int):
        with self._guard:
            return self._locks.setdefault(scope_id, RLock())

    def connection(self) -> ConnectionConfigSync:
        settings = self.context.settings
        if not getattr(settings, "agent_sandbox_domain", "") or not settings.agent_sandbox_api_key:
            raise WorkflowError("agent_sandbox_unconfigured", "代码环境尚未配置", 503)
        return ConnectionConfigSync(
            domain=settings.agent_sandbox_domain,
            protocol=settings.agent_sandbox_protocol,
            api_key=settings.agent_sandbox_api_key.get_secret_value(),
            use_server_proxy=True,
            request_timeout=timedelta(seconds=settings.agent_sandbox_command_timeout_seconds + 30),
        )

    def _scope(
        self,
        session: Session,
        scope_id: int,
        fence: tuple[int, str] | None,
        *,
        admission: bool = True,
    ) -> AgentExecutionScope:
        rows = lock_run(session, self.context.run_id)
        if rows is None:
            raise LeaseLost()
        project, conversation, run = rows
        if (
            run.initiated_by != self.context.owner_id
            or not fence
            or not fence[1]
            or not owns_execution(run, fence)
            or not run.lease_token
        ):
            raise LeaseLost()
        if admission and (
            run.cancel_requested or not may_decide(session, project, conversation, run)
        ):
            raise LeaseLost()
        return sandbox_scope_locked(session, run, scope_id)

    def metadata(self, scope_id: int) -> dict[str, str]:
        return {
            "app": "short-drama-assistant",
            "run": str(self.context.run_id),
            "scope": str(scope_id),
            "owner": str(self.context.owner_id),
        }

    def _find(self, scope_id: int):
        with SandboxManagerSync.create(connection_config=self.connection()) as manager:
            return manager.list_sandbox_infos(
                SandboxFilter(metadata=self.metadata(scope_id), page_size=100)
            ).sandbox_infos

    def ensure(self, scope_id: int, *, fence: tuple[int, str]) -> SandboxSync:
        with self.lock(scope_id):
            with self.context.factory.begin() as session:
                scope = self._scope(session, scope_id, fence)
                if scope.sandbox_status in {"cleanup_pending", "closed"}:
                    raise WorkflowError("agent_sandbox_closed", "此代码环境已结束或等待清理", 409)
                if scope_id in self._clients:
                    return self._clients[scope_id]
                sandbox_id, creating = scope.sandbox_id, scope.sandbox_status == "creating"
                if scope.image_version is None:
                    root = PurePosixPath(self.context.settings.agent_sandbox_workspace_root)
                    if not root.is_absolute() or ".." in root.parts or str(root) == "/":
                        raise WorkflowError(
                            "agent_sandbox_config", "工作区需要 Docker 宿主绝对目录", 503
                        )
                    scope.workspace_volume = str(root / f"run-{scope.run_id}" / f"scope-{scope.id}")
                    scope.image_version = self.context.settings.agent_sandbox_image
                    scope.sandbox_service = (
                        self.context.settings.agent_sandbox_protocol
                        + "://"
                        + self.context.settings.agent_sandbox_domain
                    )
                image, path = scope.image_version, scope.workspace_volume
                scope.sandbox_status = "creating"
            client = None
            try:
                if sandbox_id:
                    try:
                        client = SandboxSync.connect(
                            sandbox_id, connection_config=self.connection()
                        )
                    except SandboxApiException as error:
                        if error.status_code != 404:
                            raise
                if client is None and creating:
                    matches = self._find(scope_id)
                    if len(matches) != 1:
                        raise WorkflowError(
                            "agent_sandbox_creation_unknown", "环境创建结果待核对，未重复创建", 409
                        )
                    client = SandboxSync.connect(matches[0].id, connection_config=self.connection())
                if client is None:
                    settings = self.context.settings
                    client = SandboxSync.create(
                        image,
                        connection_config=self.connection(),
                        timeout=timedelta(hours=24),
                        metadata=self.metadata(scope_id),
                        resource={
                            "cpu": settings.agent_sandbox_cpu,
                            "memory": settings.agent_sandbox_memory,
                        },
                        volumes=[
                            Volume(name="workspace", host=Host(path=path), mount_path="/workspace")
                        ],
                        env={
                            "PATH": "/workspace/.venv/bin:/usr/local/bin:/usr/bin:/bin",
                            "VIRTUAL_ENV": "/workspace/.venv",
                            "HOME": "/workspace",
                        },
                    )
                initialized = client.commands.run(
                    "test -x /workspace/.venv/bin/python || python3 -m venv /workspace/.venv"
                )
                if initialized.exit_code != 0:
                    raise WorkflowError("agent_sandbox_init", "代码环境初始化失败", 503)
                with self.context.factory.begin() as session:
                    scope = self._scope(session, scope_id, fence)
                    scope.sandbox_id, scope.sandbox_status = client.id, "running"
                self._clients[scope_id] = client
                return client
            except Exception:
                if client is not None:
                    client.close()
                logger.exception("agent_sandbox_ensure_failed", extra={"scope_id": scope_id})
                raise

    def backend(self, scope_id: int, *, fence: tuple[int, str]) -> CommandBackend:
        from short_drama.agent.sandbox.backend import CommandBackend

        with self.lock(scope_id):
            self.ensure(scope_id, fence=fence)
            if scope_id not in self._backends:
                self._backends[scope_id] = CommandBackend(self, scope_id, fence)
            return self._backends[scope_id]

    def admitted(self, scope_id: int, fence: tuple[int, str]) -> None:
        with self.context.factory.begin() as session:
            self._scope(session, scope_id, fence)

    async def cancel(self) -> None:
        await asyncio.gather(
            *(asyncio.to_thread(backend.cancel) for backend in list(self._backends.values()))
        )

    def release_for_wait(self, run_id: int, *, fence: tuple[int, str]) -> None:
        if run_id != self.context.run_id:
            raise NotFound("执行范围不存在")
        with self.context.factory.begin() as session:
            self._scope(session, self.context.scope_id, fence)
            run = lock_run(session, run_id)[2]
            identifiers = [scope.id for scope in sandbox_scopes_locked(session, run)]
        for scope_id in identifiers:
            with self.lock(scope_id):
                with self.context.factory.begin() as session:
                    scope = self._scope(session, scope_id, fence)
                    identifier = scope.sandbox_id
                self._destroy(scope_id, identifier)
                with self.context.factory.begin() as session:
                    scope = self._scope(session, scope_id, fence, admission=False)
                    scope.sandbox_id, scope.sandbox_status = None, "released"

    def _destroy(self, scope_id: int, identifier: str | None) -> None:
        client = self._clients.pop(scope_id, None)
        self._backends.pop(scope_id, None)
        if client is None and identifier:
            try:
                client = SandboxSync.connect(identifier, connection_config=self.connection())
            except SandboxApiException as error:
                if error.status_code != 404:
                    raise
        if client is not None:
            try:
                client.destroy()
            except SandboxApiException as error:
                if error.status_code != 404:
                    raise
            finally:
                client.close()

    def cleanup_run(self, run_id: int, *, fence: tuple[int, str]) -> bool:
        if run_id != self.context.run_id:
            raise NotFound("执行范围不存在")
        return cleanup_run(
            self.context.factory,
            self.context.settings,
            run_id,
            owner_id=self.context.owner_id,
            fence=fence,
            workspace=self,
        )

    def close(self) -> None:
        for client in self._clients.values():
            client.close()
        self._clients.clear()


def cleanup_run(
    factory: sessionmaker[Session],
    settings: Settings,
    run_id: int,
    *,
    owner_id: int | None = None,
    fence: tuple[int, str] | None = None,
    workspace: SandboxWorkspaceService | None = None,
) -> bool:
    """Trusted maintenance remains reachable after Run leases and conversations close."""
    with factory.begin() as session:
        rows = lock_run(session, run_id)
        if rows is None:
            return False
        _, conversation, run = rows
        if owner_id is not None and run.initiated_by != owner_id:
            raise NotFound("执行范围不存在")
        if fence is not None:
            if not owns_execution(run, fence) or not run.lease_token:
                raise LeaseLost()
        elif run.status not in TERMINAL or run.lease_token is not None:
            return False
        scopes = sandbox_scopes_locked(session, run)
        bindings = [
            (scope.id, scope.sandbox_id, scope.workspace_volume, scope.image_version)
            for scope in scopes
        ]
        for scope in scopes:
            scope.sandbox_status = "cleanup_pending"
        owner_id = run.initiated_by
    if not bindings:
        return True
    service = workspace or SandboxWorkspaceService(
        SimpleNamespace(factory=factory, settings=settings, run_id=run_id, owner_id=owner_id)
    )
    try:
        for scope_id, identifier, path, image in bindings:
            with service.lock(scope_id):
                matches = service._find(scope_id)
                for match in matches:
                    service._destroy(scope_id, match.id)
                if identifier not in {match.id for match in matches}:
                    service._destroy(scope_id, identifier)
                _remove_workspace(service, path, image, run_id, scope_id)
                with factory.begin() as session:
                    scope = session.get(AgentExecutionScope, scope_id)
                    scope.sandbox_id, scope.sandbox_status = None, "closed"
        return True
    except Exception:
        logger.exception("agent_sandbox_cleanup_pending", extra={"run_id": run_id})
        return False
    finally:
        if workspace is None:
            service.close()


def _remove_workspace(
    service: SandboxWorkspaceService, path: str, image: str, run_id: int, scope_id: int
) -> None:
    root = PurePosixPath(service.context.settings.agent_sandbox_workspace_root)
    expected = root / f"run-{run_id}" / f"scope-{scope_id}"
    if not root.is_absolute() or str(root) == "/" or path != str(expected):
        raise WorkflowError("agent_sandbox_cleanup_path", "持久工作区绑定不属于部署目录", 409)
    # 维护容器只接受数据库推导的目录；模型没有父目录挂载或这个调用入口。
    script = (
        "import pathlib,shutil; root=pathlib.Path('/cleanup'); "
        f"parent=root/'run-{run_id}'; target=parent/'scope-{scope_id}'; "
        "assert parent.parent==root and not parent.is_symlink() and not target.is_symlink(); "
        "shutil.rmtree(target) if target.exists() else None; "
        "parent.rmdir() if parent.exists() and not any(parent.iterdir()) else None"
    )
    maintenance = SandboxSync.create(
        image,
        connection_config=service.connection(),
        timeout=timedelta(minutes=2),
        resource={"cpu": "1", "memory": "256Mi"},
        metadata={"app": "short-drama-assistant-cleanup", "run": str(run_id)},
        volumes=[Volume(name="cleanup", host=Host(path=str(root)), mount_path="/cleanup")],
    )
    try:
        result = maintenance.commands.run("python3 -c " + shlex.quote(script))
        if result.exit_code != 0:
            raise WorkflowError("agent_sandbox_cleanup_failed", "工作区清理失败，稍后重试", 503)
    finally:
        maintenance.destroy()


def cleanup_terminal_runs(
    factory: sessionmaker[Session], settings: Settings, limit: int = 100
) -> int:
    with factory() as session:
        identifiers = terminal_workspace_run_ids(session, statuses=TERMINAL, limit=limit)
    return sum(cleanup_run(factory, settings, identifier) for identifier in identifiers)
