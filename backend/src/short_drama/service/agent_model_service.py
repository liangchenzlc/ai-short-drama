"""Private decision models and explicit, version-bound tool protocol probes."""

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import select

from short_drama.agent.model_gateway import AgentGatewayError, AgentModelGateway
from short_drama.ai import select_adapter
from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.core.identity import require_actor
from short_drama.domain import AIModelConfig, UserModelPreference
from short_drama.domain.collaboration import User, UserSession
from short_drama.schemas.agent_runtime import AgentModelRead, AgentModelsRead, ModelVerify
from short_drama.schemas.base import parse_identifier
from short_drama.service.base import BaseService, utcnow


def model_snapshot(row):
    return {
        "id": str(row.id),
        "name": row.name,
        "row_version": row.row_version,
        "service_type": row.service_type,
        "model_key": row.model_key,
        "provider": row.provider,
        "base_url": row.base_url,
        "capability_cache": deepcopy(row.capability_cache),
        "credential_cipher": row.apikey,
        "credential_identity": hashlib.sha256((row.apikey or "").encode()).hexdigest(),
    }


def capability_evidence(row):
    evidence = (row.capability_cache or {}).get("agent") or {}
    valid = (
        evidence.get("row_version") == row.row_version
        and evidence.get("credential_identity")
        == hashlib.sha256((row.apikey or "").encode()).hexdigest()
        and evidence.get("model_key") == row.model_key
        and evidence.get("base_url") == row.base_url
    )
    return evidence if valid else {}


def read_model(row, preferred_id=None):
    evidence = capability_evidence(row)
    try:
        protocol = select_adapter(model_snapshot(row))
        if protocol not in {"openai_chat.v1", "openai_responses.v1"}:
            protocol = None
    except Exception:
        protocol = None
    verified = bool(evidence.get("tool_calling") and evidence.get("tool_result_continuation"))
    return AgentModelRead(
        id=row.id,
        name=row.name,
        model_key=row.model_key,
        row_version=row.row_version,
        protocol=protocol,
        tool_calling=bool(evidence.get("tool_calling")),
        tool_result_continuation=bool(evidence.get("tool_result_continuation")),
        streaming=evidence.get("streaming", "not_tested"),
        verified=verified,
        preferred=row.id == preferred_id,
    )


class AgentModelService(BaseService):
    model = AIModelConfig

    def __init__(self, session, settings, gateway=None):
        super().__init__(session)
        self.settings = settings
        self.gateway = gateway or AgentModelGateway(settings)

    def _actor(self):
        actor = require_actor(self.session)
        if not self.settings.agent_enabled:
            raise WorkflowError("agent_disabled", "Agent 模式尚未启用", 503)
        return actor

    def available(self, identifier, *, lock=False):
        actor = self._actor()
        stmt = select(AIModelConfig).where(
            AIModelConfig.id == parse_identifier(identifier),
            AIModelConfig.owner_user_id == actor.user_id,
            AIModelConfig.service_type == "text",
            AIModelConfig.enabled == 1,
            AIModelConfig.is_deleted == 0,
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        row = self.session.scalar(stmt)
        if row is None:
            raise NotFound("Decision model does not exist")
        if read_model(row).protocol is None:
            raise WorkflowError("unsupported_agent_protocol", "此配置的协议暂不支持 Agent", 422)
        return row

    def preferred_id(self):
        actor = self._actor()
        return self.session.scalar(
            select(UserModelPreference.config_id).where(
                UserModelPreference.user_id == actor.user_id,
                UserModelPreference.context_key == "agent:decision",
            )
        )

    def select_model(self, identifier=None):
        actor = self._actor()
        if identifier is None:
            identifier = self.preferred_id()
        if identifier is None:
            identifier = self.session.scalar(
                select(AIModelConfig.id)
                .where(
                    AIModelConfig.owner_user_id == actor.user_id,
                    AIModelConfig.service_type == "text",
                    AIModelConfig.enabled == 1,
                    AIModelConfig.is_deleted == 0,
                )
                .order_by(AIModelConfig.is_default.desc(), AIModelConfig.id)
                .limit(1)
            )
        if identifier is None:
            raise WorkflowError("agent_model_required", "请先配置用于 Agent 决策的文本模型", 422)
        row = self.available(identifier, lock=True)
        if not read_model(row).verified:
            raise WorkflowError("agent_model_unverified", "请先校验模型的工具调用能力", 422)
        return row

    def list_models(self):
        actor = self._actor()
        with self._transaction():
            preferred = self.preferred_id()
            rows = self.session.scalars(
                select(AIModelConfig)
                .where(
                    AIModelConfig.owner_user_id == actor.user_id,
                    AIModelConfig.service_type == "text",
                    AIModelConfig.enabled == 1,
                    AIModelConfig.is_deleted == 0,
                )
                .order_by(AIModelConfig.is_default.desc(), AIModelConfig.id)
            ).all()
            items = [read_model(row, preferred) for row in rows]
            return AgentModelsRead(
                items=items,
                preferred_id=preferred if any(row.id == preferred for row in rows) else None,
            )

    def _probe_identity(self, actor, *, require_session=False):
        if not self.settings.agent_enabled:
            raise WorkflowError("agent_disabled", "Agent 模式尚未启用", 503)
        user = self.session.scalar(
            select(User)
            .where(User.id == actor.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        login = self.session.scalar(
            select(UserSession)
            .where(UserSession.id == actor.session_id, UserSession.user_id == actor.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            user is None
            or user.status != "active"
            or not user.email_verified_at
            or (require_session and login is None)
            or (login is not None and (login.revoked_at or login.expires_at <= utcnow()))
        ):
            raise WorkflowError("authentication_required", "Please sign in", 401)
        return login is not None

    @staticmethod
    def _same_probe_model(row, snapshot):
        return (
            row is not None
            and all(
                getattr(row, field) == snapshot[field]
                for field in ("row_version", "model_key", "base_url", "provider", "service_type")
            )
            and row.apikey == snapshot["credential_cipher"]
            and row.enabled == 1
            and not row.is_deleted
        )

    def verify(self, identifier, payload):
        """Exactly two segments at most. No locks/transactions during remote I/O."""
        expected = self._payload(ModelVerify, payload)["row_version"]
        token = uuid4().hex
        actor = self._actor()
        admitted, admission_failure = 0, None
        with self._transaction():
            require_session = self._probe_identity(actor)
            row = self.available(identifier, lock=True)
            if row.row_version != expected:
                raise Conflict("Model configuration changed; reload before checking")
            cache = deepcopy(row.capability_cache or {})
            probe = cache.get("agent_probe") or {}
            if (
                probe.get("state") == "running"
                and probe.get("row_version") == row.row_version
                and probe.get("until", "") > utcnow().isoformat()
            ):
                raise Conflict("Agent capability check is already running")
            cache["agent_probe"] = {
                "token": token,
                "state": "running",
                "row_version": expected,
                "requests": 0,
                "until": (utcnow() + timedelta(minutes=5)).isoformat(),
            }
            row.capability_cache = cache
            snapshot = model_snapshot(row)

        async def admit(_request):
            nonlocal admitted, admission_failure
            try:
                with self._transaction():
                    self._probe_identity(actor, require_session=require_session)
                    current = self.available(identifier, lock=True)
                    if not self._same_probe_model(current, snapshot):
                        raise Conflict("Model configuration changed during checking")
                    cache = deepcopy(current.capability_cache or {})
                    probe = cache.get("agent_probe") or {}
                    if (
                        probe.get("token") != token
                        or probe.get("state") != "running"
                        or probe.get("until", "") <= utcnow().isoformat()
                        or probe.get("requests", 0) >= 2
                    ):
                        raise Conflict("Capability check is no longer admitted")
                    probe["requests"] = probe.get("requests", 0) + 1
                    cache["agent_probe"] = probe
                    current.capability_cache = cache
                    next_count = probe["requests"]
                admitted = next_count
            except BusinessError as error:
                admission_failure = error.code
                raise

        key = self.settings.encryption_key
        try:
            credential = (
                KeyCipher(key.get_secret_value() if key else None).decrypt(
                    snapshot["credential_cipher"]
                )
                if snapshot["credential_cipher"]
                else ""
            )
            evidence = asyncio.run(
                self.gateway.validate_capability(
                    snapshot, credential, conversation_id=f"probe-{token}", on_request=admit
                )
            )
            result = asdict(evidence)
            result.pop("segments", None)
            failure = None
        except AgentGatewayError as error:
            result, failure = (
                {"requests": max(admitted, error.requests)},
                (admission_failure or error.code),
            )
        except BusinessError as error:
            result, failure = {"requests": admitted}, error.code
        except ValueError:
            result, failure = {"requests": 0}, "agent_credential_unavailable"
        with self._transaction():
            try:
                self._probe_identity(actor, require_session=require_session)
            except BusinessError as error:
                failure = failure or error.code
            row = self.session.scalar(
                select(AIModelConfig)
                .where(
                    AIModelConfig.id == int(snapshot["id"]),
                    AIModelConfig.owner_user_id == actor.user_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise NotFound("Decision model does not exist")
            changed = not self._same_probe_model(row, snapshot)
            if changed:
                failure = "agent_model_changed"
            cache = deepcopy(row.capability_cache or {})
            if (cache.get("agent_probe") or {}).get("token") != token:
                raise Conflict("A newer capability check replaced this result")
            cache["agent_probe"] = {
                "state": "failed" if failure else "complete",
                "requests": result["requests"],
                "error": failure,
                "row_version": expected,
            }
            if not failure:
                cache["agent"] = {
                    **result,
                    "row_version": expected,
                    "credential_identity": snapshot["credential_identity"],
                    "model_key": row.model_key,
                    "base_url": row.base_url,
                    "checked_at": utcnow().isoformat(),
                }
            else:
                cache.pop("agent", None)
            row.capability_cache = cache
            response = read_model(row, self.preferred_id()) if not failure else None
        if changed:
            raise Conflict("Model configuration changed during checking")
        if failure:
            raise WorkflowError(failure, "模型能力校验失败；请检查配置后手动重试", 502)
        return response
