"""Thin SDK command streaming/cancellation around the official backend."""

from __future__ import annotations

import asyncio
import logging
from contextvars import ContextVar
from datetime import timedelta
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Any

from deepagents.backends.protocol import ExecuteResponse, ReadResult
from langchain_opensandbox import OpenSandboxBackend
from opensandbox.models.execd import RunCommandOpts
from opensandbox.models.execd_sync import ExecutionHandlersSync

if TYPE_CHECKING:
    from opensandbox import SandboxSync

    from short_drama.agent.runtime.context import RuntimeContext

from short_drama.agent.core.access import LeaseLost
from short_drama.agent.tools.runtime import execution_locked
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.assistant.operations.trace import TRACE_PREVIEW_BYTES

logger = logging.getLogger(__name__)
sandbox_call: ContextVar[tuple[RuntimeContext, str, str] | None] = ContextVar(
    "sandbox_call", default=None
)
ARCHIVE_MAX_BYTES = 4 * 1024**2


class CommandBackend(OpenSandboxBackend):
    def __init__(self, workspace: Any, scope_id: int, fence: tuple[int, str]) -> None:
        self.workspace, self.scope_id, self.fence = workspace, scope_id, fence
        self._active, self._state_lock = None, Lock()
        super().__init__(
            sandbox=workspace.ensure(scope_id, fence=fence),
            timeout=workspace.context.settings.agent_sandbox_command_timeout_seconds,
        )

    def cancel(self) -> None:
        with self._state_lock:
            execution_id = self._active
        if execution_id is not None:
            self._sandbox.commands.interrupt(execution_id)

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        effective = self._default_timeout if timeout is None else timeout
        if not 1 <= effective <= self._default_timeout:
            raise WorkflowError(
                "agent_sandbox_timeout_limit", "命令超时不能超过部署上限或关闭", 422
            )
        with self.workspace.lock(self.scope_id):
            self.workspace.admitted(self.scope_id, self.fence)
            return self._run(command, effective)

    def _run(self, command: str, timeout: int) -> ExecuteResponse:
        settings = self.workspace.context.settings
        call = sandbox_call.get()
        traced = call is not None and call[2] == "execute"
        visible, archives, stopped, finished = (
            bytearray(),
            {"stdout": bytearray(), "stderr": bytearray()},
            Event(),
            Event(),
        )
        stream_totals = {"stdout": 0, "stderr": 0}
        total = 0

        def output(message, stream):
            nonlocal total
            data = (message.text + "\n").encode("utf-8")
            total += len(data)
            stream_totals[stream] += len(data)
            remaining = settings.agent_sandbox_output_max_bytes - len(visible)
            chunk = data[: max(0, remaining)]
            visible.extend(chunk)
            if traced:
                archives[stream].extend(data[: max(0, ARCHIVE_MAX_BYTES - len(archives[stream]))])
                trace_remaining = TRACE_PREVIEW_BYTES - (stream_totals[stream] - len(data))
                trace_chunk = data[: max(0, trace_remaining)]
                if trace_chunk:
                    self._event(
                        "sandbox.log",
                        {"stream": stream, "content": trace_chunk.decode("utf-8", errors="ignore")},
                        call,
                    )

        def init(message):
            with self._state_lock:
                self._active = message.id
            if traced:
                self._event("sandbox.started", {"execution_id": message.id}, call)

        def watch():
            while not finished.wait(0.25):
                try:
                    self.workspace.admitted(self.scope_id, self.fence)
                except LeaseLost:
                    stopped.set()
                    try:
                        self.cancel()
                    except Exception:
                        logger.exception(
                            "agent_sandbox_interrupt_failed", extra={"scope_id": self.scope_id}
                        )
                        # 删除远端容器是取消失败时的最终进程边界，不能只取消本地 await。
                        self._sandbox.kill()
                    return

        monitor = Thread(target=watch, name="agent-sandbox-stop", daemon=True)
        monitor.start()
        try:
            execution = self._sandbox.commands.run(
                command,
                opts=RunCommandOpts(
                    timeout=timedelta(seconds=timeout), working_directory="/workspace"
                ),
                handlers=ExecutionHandlersSync(
                    on_init=init,
                    on_stdout=lambda message: output(message, "stdout"),
                    on_stderr=lambda message: output(message, "stderr"),
                    skip_accumulation=True,
                ),
            )
        finally:
            finished.set()
            monitor.join()
            with self._state_lock:
                self._active = None
        if stopped.is_set():
            raise LeaseLost()
        truncated = total > settings.agent_sandbox_output_max_bytes
        if traced:
            for stream, archived in archives.items():
                if stream_totals[stream] > TRACE_PREVIEW_BYTES:
                    self._archive(bytes(archived), call, stream_totals[stream], stream)
            self._event(
                "sandbox.finished",
                {
                    "execution_id": execution.id,
                    "exit_code": execution.exit_code,
                    "truncated": truncated,
                },
                call,
            )
        return ExecuteResponse(
            output=visible.decode("utf-8", errors="ignore"),
            exit_code=execution.exit_code if execution.exit_code is not None else -1,
            truncated=truncated,
        )

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        worker = asyncio.create_task(asyncio.to_thread(self.execute, command, timeout=timeout))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            await asyncio.to_thread(self.cancel)
            await asyncio.gather(worker, return_exceptions=True)
            raise

    def _event(self, kind: str, payload: dict, call: tuple[RuntimeContext, str, str]) -> None:
        from short_drama.agent.core.events import append_event

        context, call_id, _name = call
        with context.factory.begin() as session:
            conversation, _task, run, operation = execution_locked(session, context, call_id)
            if context.events is not None:
                context.events.sandbox_locked(session, conversation, run, operation, kind, payload)
            append_event(
                session,
                conversation,
                kind,
                {
                    "scope_id": str(self.scope_id),
                    "operation_id": str(operation.id),
                    "tool_call_id": str(operation.tool_call_id),
                    **payload,
                },
                run_id=run.id,
            )

    def _archive(
        self,
        data: bytes,
        call: tuple[RuntimeContext, str, str],
        total: int,
        stream: str = "combined",
    ) -> None:
        from short_drama.agent.runtime.trace_content import display_safe
        from short_drama.agent.sandbox.resources import publish_file

        context, call_id, _ = call
        reference = publish_file(
            context,
            call_id,
            f"command-{stream}.log",
            display_safe(data.decode("utf-8", errors="ignore")).encode("utf-8"),
            "text/plain",
            source="sandbox_log",
        )
        self._event(
            "sandbox.log.archived",
            {
                "resource_ref": reference,
                "total_bytes": total,
                "archive_truncated": total > len(data),
                "stream": stream,
            },
            call,
        )


class ScopedBackend(OpenSandboxBackend):
    """An initialized backend facade resolves the real scope only inside a tool call."""

    def __init__(self) -> None:
        pass

    def _bound(self) -> CommandBackend:
        call = sandbox_call.get()
        if call is None:
            raise WorkflowError("agent_sandbox_scope", "代码工具缺少可信执行范围", 403)
        context = call[0]
        return context.sandbox.backend(context.scope_id, fence=context.fence)

    @property
    def _sandbox(self) -> SandboxSync:
        return self._bound()._sandbox

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        return self._bound().execute(command, timeout=timeout)

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        # ContextVar 随 to_thread 传播；创建与重连同样不能阻塞图线程。
        binding = asyncio.create_task(asyncio.to_thread(self._bound))
        try:
            backend = await asyncio.shield(binding)
        except asyncio.CancelledError:
            await asyncio.gather(binding, return_exceptions=True)
            raise
        return await backend.aexecute(command, timeout=timeout)

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        result = super().read(file_path, offset, limit)
        if result.file_data and result.file_data["encoding"] != "utf-8":
            return ReadResult(error="二进制文件请显式导出为 ResourceRef，不能直接送入模型")
        return result

    async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        return await asyncio.to_thread(self.read, file_path, offset, limit)
