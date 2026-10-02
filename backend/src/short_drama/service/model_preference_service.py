from sqlalchemy import select

from short_drama.core.exceptions import NotFound
from short_drama.domain import AIModelConfig
from short_drama.domain.collaboration import User, UserModelPreference
from short_drama.utils.snowflake import next_id


def get_model_preference(session, context_key):
    actor = session.info.get("actor")
    if actor is None:
        return None
    return session.scalar(
        select(UserModelPreference.config_id)
        .join(AIModelConfig, AIModelConfig.id == UserModelPreference.config_id)
        .where(
            UserModelPreference.user_id == actor.user_id,
            UserModelPreference.context_key == context_key,
            AIModelConfig.enabled == 1,
            AIModelConfig.is_deleted == 0,
        )
    )


def save_model_preference(session, context_key, config_id):
    actor = session.info["actor"]
    session.scalar(select(User.id).where(User.id == actor.user_id).with_for_update())
    preference = session.scalar(
        select(UserModelPreference)
        .where(
            UserModelPreference.user_id == actor.user_id,
            UserModelPreference.context_key == context_key,
        )
        .with_for_update()
    )
    if config_id is None:
        if preference:
            session.delete(preference)
        return
    config = session.scalar(
        select(AIModelConfig).where(
            AIModelConfig.id == config_id, AIModelConfig.enabled == 1, AIModelConfig.is_deleted == 0
        )
    )
    if config is None:
        raise NotFound("Model configuration does not exist")
    if preference:
        preference.config_id = config.id
    else:
        session.add(
            UserModelPreference(
                id=next_id(), user_id=actor.user_id, context_key=context_key, config_id=config.id
            )
        )
