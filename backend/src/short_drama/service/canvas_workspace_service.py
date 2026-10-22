from copy import deepcopy

from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_workspace_dao import CanvasWorkspaceDAO
from short_drama.domain.canvas import CanvasWorkspaceUserState
from short_drama.schemas.canvas_workspace import (
    PREFERENCE_FIELDS,
    CanvasWorkspacePreferencesRequest,
)
from short_drama.utils.snowflake import next_id

from .base import BaseService, utcnow
from .canvas_model_catalog_service import CanvasModelCatalogService
from .model_runtime_config import canvas_model_default_capability_config, model_runtime_public


class CanvasWorkspaceService(BaseService):
    model = CanvasWorkspaceUserState

    def __init__(self, session: Session, settings=None, cipher=None):
        super().__init__(session)
        self.workspace_dao = CanvasWorkspaceDAO(session)
        self.catalog = CanvasModelCatalogService(session, settings=settings, cipher=cipher)

    @property
    def actor_id(self) -> int:
        actor = self.session.info.get("actor")
        if actor is None:
            raise WorkflowError("authentication_required", "Sign in to use canvases", 401)
        return actor.user_id

    @staticmethod
    def _preferences(state: CanvasWorkspaceUserState | None) -> dict:
        return {
            "row_version": str(state.row_version) if state else "0",
            "preferences": {
                key: deepcopy(value)
                for key, value in (state.preferences_json if state else {}).items()
                if key in PREFERENCE_FIELDS
            },
        }

    def read_models(self) -> dict:
        with self._transaction(read_only=True):
            state = self.workspace_dao.preferences(self.actor_id)
            return self._read_models_locked(state)

    def _read_models_locked(self, state):
        return {
            **self._preferences(state),
            "models": self._models(),
            "channels": [],
        }

    def _models(self):
        records, bindings = self.workspace_dao.models()
        return [
            {
                "id": str(value.id),
                "name": value.name,
                "model_key": value.model_key,
                "provider": value.provider,
                "service_type": value.service_type,
                "enabled": bool(value.enabled),
                "has_api_key": bool(value.apikey),
                "is_default": bool(value.is_default),
                "default_capability_config": canvas_model_default_capability_config(value),
                "selection_aliases": [
                    f"{bindings[value.id].channel_key}::{bindings[value.id].model_key}"
                ]
                if value.id in bindings
                else [],
                **model_runtime_public(
                    value,
                    self.catalog.configs._key_cipher()
                    if value.runtime_credentials_cipher
                    else None,
                    bindings.get(value.id),
                ),
            }
            for value in records
        ]

    def save_preferences(self, payload: CanvasWorkspacePreferencesRequest) -> dict:
        payload = CanvasWorkspacePreferencesRequest.model_validate(
            payload.model_dump(exclude_unset=True)
        )
        if "channels" in payload.model_fields_set:
            raise WorkflowError(
                "canvas_model_catalog_read_only", "模型配置已统一，请在宿主 AI 配置中维护", 409
            )
        with self._transaction():
            if self.workspace_dao.lock_user(self.actor_id) is None:
                raise NotFound("Account does not exist")
            state = self.workspace_dao.preferences(self.actor_id, lock=True)
            current = state.row_version if state else 0
            if current != payload.expected_row_version:
                raise WorkflowError(
                    "canvas_model_preferences_conflict",
                    "Canvas model preferences changed",
                    409,
                    {"current_version": str(current)},
                )
            if state:
                preferences = deepcopy(payload.preferences)
                if state.preferences_json != preferences:
                    state.preferences_json = preferences
                    state.row_version += 1
                    state.updated_at = utcnow()
            else:
                state = CanvasWorkspaceUserState(
                    id=next_id(),
                    user_id=self.actor_id,
                    preferences_json=deepcopy(payload.preferences),
                    row_version=1,
                    created_at=utcnow(),
                    updated_at=utcnow(),
                    created_by=self.actor_id,
                    updated_by=self.actor_id,
                )
                self.session.add(state)
            self.session.flush()
            return self._read_models_locked(state)
