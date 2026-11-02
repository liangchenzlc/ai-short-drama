"""One short transaction for authorized business writes and their durable receipt."""

from collections.abc import Callable, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.core.time import utcnow
from short_drama.dao.assistant.operations.invocation import BusinessInvocationDAO
from short_drama.db.scope.resource_scope import require_project
from short_drama.domain.assistant.operations.invocation import BusinessToolInvocation
from short_drama.domain.identity.audit.models import AuditEvent
from short_drama.service.base import BaseService
from short_drama.utils.json_digest import digest
from short_drama.utils.snowflake import next_id


@dataclass(frozen=True)
class InvocationScope:
    project_ids: frozenset[int]
    tool_names: frozenset[str]
    subject_policy: Literal["selected", "project"]
    allowed_subjects: Mapping[str, frozenset[str]]
    image_catalogs: Mapping[str, dict] = field(default_factory=dict)
    subject_snapshots: Mapping[str, dict] = field(default_factory=dict)
    script_reference: Mapping[str, Any] | None = None
    generation_ids: frozenset[int] = frozenset()


@dataclass(frozen=True)
class BusinessInvocation:
    actor: ActorContext
    source: Literal["assistant", "mcp"]
    request_id: str
    scope: InvocationScope
    token_id: int | None = None


def assistant_request_id(run_id: int, scope_id: int, tool_call_id: str) -> str:
    return sha256(f"assistant:{run_id}:{scope_id}:{tool_call_id}".encode()).hexdigest()


def require_invocation_project(identity: BusinessInvocation, project_id: int) -> None:
    if project_id not in identity.scope.project_ids:
        raise WorkflowError("business_project_scope", "作品超出本次调用范围", 403)


def require_invocation_subject(
    identity: BusinessInvocation, kind: str, identifier: int | str
) -> None:
    if identity.scope.subject_policy == "selected" and str(
        identifier
    ) not in identity.scope.allowed_subjects.get(kind, frozenset()):
        raise WorkflowError("business_subject_scope", "对象未选入本次调用范围", 403)


@contextmanager
def _invocation_context(session: Session, identity: BusinessInvocation):
    values = {
        "actor": identity.actor,
        "invocation_scope": identity.scope,
        "business_invocation": identity,
    }
    previous = {key: session.info.get(key) for key in values}
    session.info.update(values)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                session.info.pop(key, None)
            else:
                session.info[key] = value


class BusinessInvocationService(BaseService):
    model = BusinessToolInvocation

    def __init__(self, session: Session, settings: Any = None) -> None:
        super().__init__(session)
        self.settings = settings
        self.invocations = BusinessInvocationDAO(session)

    def execute(
        self,
        identity: BusinessInvocation,
        tool_name: str,
        values: BaseModel,
        handler: Callable[..., dict],
        *,
        mutating: bool,
    ) -> dict:
        with _invocation_context(self.session, identity):
            with self._transaction(read_only=not mutating):
                return self.execute_locked(identity, tool_name, values, handler, mutating=mutating)

    def execute_locked(self, identity, tool_name, values, handler, *, mutating):
        with _invocation_context(self.session, identity):
            self._authorize(identity, tool_name, values, mutating=mutating)
            if not mutating:
                result = handler(self.session, identity, values, None, self.settings)
                self._audit_read(identity, tool_name, values)
                return result
            return self._write(identity, tool_name, values, handler)

    def _audit_read(self, identity: BusinessInvocation, tool_name: str, values: BaseModel) -> None:
        project_id = getattr(values, "project_id", None)
        self.session.add(
            AuditEvent(
                id=next_id(),
                actor_user_id=identity.actor.user_id,
                project_id=project_id,
                object_type=tool_name if project_id is not None else "mcp_access_tokens",
                object_id=str(project_id if project_id is not None else identity.token_id),
                action="read",
                request_id=identity.actor.request_id,
                source=identity.source,
                token_id=identity.token_id,
                created_at=utcnow(),
            )
        )

    def _authorize(
        self, identity: BusinessInvocation, tool_name: str, values: BaseModel, *, mutating: bool
    ) -> None:
        if tool_name not in identity.scope.tool_names:
            raise WorkflowError("business_tool_scope", "工具超出本次调用范围", 403)
        project_id = getattr(values, "project_id", None)
        if project_id is not None:
            require_invocation_project(identity, int(project_id))
            require_project(self.session, project_id, lock=mutating)
        # 与原生 Worker/助手一致先锁作品，再锁账号；账号锁串行跨令牌首次受理。
        user = self.invocations.account(identity.actor.user_id, lock=mutating)
        if user is None or user.status != "active" or user.email_verified_at is None:
            raise WorkflowError("authentication_required", "Please sign in", 401)

    def _write(
        self,
        identity: BusinessInvocation,
        tool_name: str,
        values: BaseModel,
        handler: Callable[..., dict],
    ) -> dict:
        arguments_hash = digest(values.model_dump(mode="json"))
        candidate = BusinessToolInvocation(
            id=next_id(),
            owner_user_id=identity.actor.user_id,
            tool_name=tool_name,
            request_id=identity.request_id,
            arguments_hash=arguments_hash,
            source=identity.source,
            token_id=identity.token_id,
            status="running",
            result_refs=[],
            changes=[],
            created_at=utcnow(),
        )
        receipt = self.invocations.reserve_receipt(candidate)
        if receipt.id != candidate.id:
            if receipt.arguments_hash != arguments_hash:
                raise Conflict("相同请求标识不能更改业务参数")
            if receipt.status != "succeeded":
                raise Conflict("原受理结果待核实，请沿原身份查询")
            return deepcopy(receipt.result)
        result = handler(self.session, identity, values, receipt.id, self.settings)
        receipt.changes = result.pop("changes", [])
        receipt.result_refs = result.get("result_refs", [])
        receipt.result = {**result, "business_invocation_id": str(receipt.id)}
        receipt.status, receipt.finished_at = "succeeded", utcnow()
        self.session.flush()
        return deepcopy(receipt.result)
