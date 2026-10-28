from short_drama.core.exceptions import NotFound
from short_drama.core.identity import require_actor
from short_drama.dao.ai_config.preferences.preferences import ModelPreferenceDAO
from short_drama.domain.ai_config.preferences.models import UserModelPreference
from short_drama.service.base import BaseService
from short_drama.utils.snowflake import next_id


def get_model_preference(session, context_key):
    actor = session.info.get("actor")
    if actor is None:
        return None
    return ModelPreferenceDAO(session).selected_config(actor.user_id, context_key)


def save_model_preference(session, context_key, config_id):
    actor = session.info["actor"]
    dao = ModelPreferenceDAO(session)
    dao.lock_user(actor.user_id)
    preference = dao.preference(actor.user_id, context_key)
    if config_id is None:
        if preference:
            session.delete(preference)
        return
    config = dao.enabled_config(config_id)
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


class ModelPreferenceService(BaseService):
    model = UserModelPreference

    def list_preferences(self) -> dict[str, list[dict[str, str]]]:
        actor = require_actor(self.session)
        with self._transaction(read_only=True):
            return {
                "items": [
                    {"context_key": value.context_key, "config_id": str(value.config_id)}
                    for value in ModelPreferenceDAO(self.session).list_for_user(actor.user_id)
                ]
            }

    def save_preference(self, context_key: str, config_id: int | None) -> dict[str, bool]:
        require_actor(self.session)
        with self._transaction():
            save_model_preference(self.session, context_key, config_id)
        return {"saved": True}
