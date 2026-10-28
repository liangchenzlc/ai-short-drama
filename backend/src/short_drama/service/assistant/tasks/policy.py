"""Real-time account media policy, kept separate from task authority and model credentials."""

from sqlalchemy import select

from short_drama.core.exceptions import Conflict, NotFound
from short_drama.core.identity import require_actor
from short_drama.domain import AIModelConfig
from short_drama.domain.assistant.operations.preference import AgentPreference
from short_drama.domain.identity.accounts.user import User
from short_drama.schemas.assistant.tasks.policy import GenerationPolicyPatch, GenerationPolicyRead
from short_drama.schemas.base import UINT64_MAX
from short_drama.service.base import BaseService, utcnow
from short_drama.utils.snowflake import next_id

MEDIA_KINDS = ("image", "video", "audio")


def policy_state_locked(session, owner: int, *, lock=False) -> tuple[bool, int]:
    statement = select(
        AgentPreference.media_generation_enabled, AgentPreference.policy_version
    ).where(AgentPreference.owner_user_id == owner)
    row = session.execute(statement.with_for_update() if lock else statement).one_or_none()
    return (bool(row[0]), row[1]) if row else (True, 0)


def generation_policy_locked(session, owner: int) -> GenerationPolicyRead:
    enabled, version = policy_state_locked(session, owner)
    configured = set(
        session.scalars(
            select(AIModelConfig.service_type).where(
                AIModelConfig.owner_user_id == owner,
                AIModelConfig.service_type.in_(MEDIA_KINDS),
                AIModelConfig.enabled == 1,
                AIModelConfig.is_deleted == 0,
            )
        )
    )
    return GenerationPolicyRead(
        media_generation_enabled=enabled,
        policy_version=version,
        configured_media=[kind for kind in MEDIA_KINDS if kind in configured],
        available_media=[kind for kind in MEDIA_KINDS if kind in configured] if enabled else [],
    )


class AssistantPolicyService(BaseService):
    model = AgentPreference

    def get_generation_policy(self) -> GenerationPolicyRead:
        owner = require_actor(self.session).user_id
        with self._transaction(read_only=True):
            return generation_policy_locked(self.session, owner)

    def update_generation_policy(self, payload) -> GenerationPolicyRead:
        values = GenerationPolicyPatch.model_validate(payload)
        owner = require_actor(self.session).user_id
        with self._transaction():
            user = self.session.scalar(select(User.id).where(User.id == owner).with_for_update())
            if user is None:
                raise NotFound("账号不存在或已停止使用")
            row = self.session.scalar(
                select(AgentPreference)
                .where(AgentPreference.owner_user_id == owner)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            version = row.policy_version if row else 0
            if values.policy_version != version:
                raise Conflict("助手生成设置已变化，请重新读取后保存")
            if row is None:
                row = AgentPreference(
                    id=next_id(),
                    owner_user_id=owner,
                    media_generation_enabled=int(values.media_generation_enabled),
                    policy_version=1,
                    updated_at=utcnow(),
                )
                self.session.add(row)
            elif bool(row.media_generation_enabled) != values.media_generation_enabled:
                if version == UINT64_MAX:
                    raise Conflict("助手生成设置版本已达到上限")
                row.media_generation_enabled = int(values.media_generation_enabled)
                row.policy_version += 1
                row.updated_at = utcnow()
            self.session.flush()
            return generation_policy_locked(self.session, owner)
