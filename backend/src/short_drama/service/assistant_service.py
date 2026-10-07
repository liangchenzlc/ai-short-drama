"""Project-private chat façade; legacy execution services keep their own contracts."""

import hashlib

from sqlalchemy import func, select

from short_drama.agent.authorization import digest
from short_drama.core.exceptions import Conflict, WorkflowError
from short_drama.db.access import require_project
from short_drama.domain.agent import AgentConversation
from short_drama.schemas.agent_runtime import MessageCreate
from short_drama.schemas.assistant import AssistantConversationCreate, AssistantMessageCreate
from short_drama.schemas.base import parse_identifier
from short_drama.service.agent_run_service import AgentRunService
from short_drama.service.base import Page, utcnow
from short_drama.utils.snowflake import next_id


class AssistantService(AgentRunService):
    def _conversation(self, identifier, *, lock=False, expected_scope=None):
        row = super()._conversation(identifier, lock=lock, expected_scope=expected_scope)
        if row.scope_version != 2:
            raise WorkflowError("assistant_legacy_readonly", "旧对话仅供查看，请新建项目对话", 409)
        return row

    def create_project_conversation(self, payload, idempotency_key=None, *, resolve=False):
        values = self._payload(AssistantConversationCreate, payload)
        actor = self._actor()
        if idempotency_key is not None and (
            not isinstance(idempotency_key, str)
            or not 1 <= len(idempotency_key) <= 64
            or not all(
                char.isascii() and (char.isalnum() or char in "_-:") for char in idempotency_key
            )
        ):
            raise WorkflowError("invalid_idempotency_key", "新建对话需要有效幂等键", 422)
        key = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key else None
        request_hash = digest(values)
        with self._transaction():
            require_project(self.session, values["project_id"], lock=True)
            if key is not None:
                existing = self.session.scalar(
                    select(AgentConversation).where(
                        AgentConversation.owner_user_id == actor.user_id,
                        AgentConversation.create_key == key,
                    )
                )
                if existing is not None:
                    if existing.scope_version != 2 or existing.create_hash != request_hash:
                        raise Conflict("Idempotency key was used for a different conversation")
                    return self._read_conversation(existing)
            if resolve:
                existing = self.session.scalar(
                    select(AgentConversation)
                    .where(
                        AgentConversation.owner_user_id == actor.user_id,
                        AgentConversation.project_id == values["project_id"],
                        AgentConversation.scope_version == 2,
                        AgentConversation.status == "active",
                    )
                    .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
                    .limit(1)
                    .with_for_update()
                )
                if existing is not None:
                    return self._read_conversation(existing)
            now = utcnow()
            row = AgentConversation(
                id=next_id(),
                owner_user_id=actor.user_id,
                **values,
                episode_id=None,
                stage=None,
                subject_type=None,
                subject_id=None,
                task_type=None,
                scope_version=2,
                status="active",
                row_version=1,
                next_message_seq=1,
                next_event_seq=1,
                fixed_requirements={},
                create_key=key,
                create_hash=request_hash if key else None,
                created_at=now,
                updated_at=now,
            )
            self.session.add(row)
            self.session.flush()
            return self._read_conversation(row)

    def list_project_conversations(
        self, project_id, offset=0, limit=20, *, include_archived=False, search=None, legacy=False
    ):
        actor = self._actor()
        project_id = parse_identifier(project_id)
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            require_project(self.session, project_id, lock=False)
            conditions = [
                AgentConversation.owner_user_id == actor.user_id,
                AgentConversation.project_id == project_id,
                AgentConversation.scope_version.in_((0, 1))
                if legacy
                else AgentConversation.scope_version == 2,
            ]
            if not include_archived:
                conditions.append(AgentConversation.status == "active")
            if search is not None:
                if not isinstance(search, str) or len(search) > 120:
                    raise WorkflowError("invalid_search", "对话搜索最多 120 个字", 422)
                if search.strip():
                    conditions.append(
                        AgentConversation.title.contains(search.strip(), autoescape=True)
                    )
            rows = self.session.scalars(
                select(AgentConversation)
                .where(*conditions)
                .order_by(AgentConversation.updated_at.desc(), AgentConversation.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            return Page(
                items=[self._read_conversation(row) for row in rows],
                total=self.session.scalar(
                    select(func.count(AgentConversation.id)).where(*conditions)
                ),
                offset=offset,
                limit=limit,
            )

    def send_message(self, identifier, payload, idempotency_key):
        inputs = AssistantMessageCreate.model_validate(
            self._payload(AssistantMessageCreate, payload)
        )
        values = MessageCreate(
            content=inputs.content,
            mode="discuss",
            model_config_id=inputs.model_config_id,
            attachment_ids=inputs.attachment_ids,
            skills=inputs.skills,
            video_audio=inputs.video_audio,
        )
        return self._send_message_values(
            identifier, values, idempotency_key, assistant_input=inputs
        )
