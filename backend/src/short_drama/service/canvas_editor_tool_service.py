"""内置编辑器工具的个人启用状态，以现有私人工作区偏好为准。"""

from copy import deepcopy

from sqlalchemy.orm import Session

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_workspace_dao import CanvasWorkspaceDAO
from short_drama.domain.canvas import CanvasWorkspaceUserState
from short_drama.schemas.canvas_editor_tools import (
    EDITOR_TOOL_IDS,
    EDITOR_TOOL_PREFERENCES_KEY,
)
from short_drama.utils.snowflake import next_id

from .base import BaseService, utcnow


class CanvasEditorToolService(BaseService):
    model = CanvasWorkspaceUserState

    def __init__(self, session: Session):
        super().__init__(session)
        self.workspace = CanvasWorkspaceDAO(session)

    @property
    def actor_id(self) -> int:
        actor = self.session.info.get("actor")
        if actor is None:
            raise WorkflowError("authentication_required", "请登录后使用画布工具", 401)
        return actor.user_id

    @staticmethod
    def _require_known(plugin_id: str):
        if plugin_id not in EDITOR_TOOL_IDS:
            raise NotFound("内置编辑器工具不存在")

    @staticmethod
    def _states(state: CanvasWorkspaceUserState | None) -> dict:
        configured = (state.preferences_json if state else {}).get(EDITOR_TOOL_PREFERENCES_KEY, {})
        if not isinstance(configured, dict):
            configured = {}
        return {
            plugin_id: {
                "pluginId": plugin_id,
                "platformAvailable": True,
                "userEnabled": configured.get(plugin_id) is True,
                "userConfigured": type(configured.get(plugin_id)) is bool,
                "effectiveEnabled": configured.get(plugin_id) is True,
                "canToggle": True,
                "canConfigure": False,
            }
            for plugin_id in EDITOR_TOOL_IDS
        }

    def read(self) -> dict:
        with self._transaction(read_only=True):
            states = self._states(self.workspace.preferences(self.actor_id))
            return {
                "states": states,
                "statuses": {
                    key: "enabled" if value["effectiveEnabled"] else "disabled"
                    for key, value in states.items()
                },
            }

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict:
        self._require_known(plugin_id)
        with self._transaction():
            actor_id = self.actor_id
            if self.workspace.lock_user(actor_id) is None:
                raise NotFound("账号不存在")
            state = self.workspace.preferences(actor_id, lock=True)
            preferences = deepcopy(state.preferences_json) if state else {}
            configured = {**preferences.get(EDITOR_TOOL_PREFERENCES_KEY, {}), plugin_id: enabled}
            preferences[EDITOR_TOOL_PREFERENCES_KEY] = configured
            now = utcnow()
            if state is None:
                state = CanvasWorkspaceUserState(
                    id=next_id(),
                    user_id=actor_id,
                    preferences_json=preferences,
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
                self.session.add(state)
            elif state.preferences_json != preferences:
                state.preferences_json = preferences
                state.row_version += 1
                state.updated_at, state.updated_by = now, actor_id
            self.session.flush()
            return {"state": self._states(state)[plugin_id]}

    def require_enabled_locked(self, plugin_id: str) -> None:
        self._require_known(plugin_id)
        # 授权随准入快照冻结，不让个人设置锁进入项目与模型准入的锁链。
        state = self.workspace.preferences(self.actor_id)
        if not self._states(state)[plugin_id]["effectiveEnabled"]:
            raise WorkflowError(
                "canvas_editor_tool_disabled", "请先在工作台AI配置中启用此画布工具", 409
            )
