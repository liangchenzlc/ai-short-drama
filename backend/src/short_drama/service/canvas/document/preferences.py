import json
from copy import deepcopy

from sqlalchemy import JSON, cast, literal, select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import ProjectCanvas
from short_drama.domain.canvas.document.canvas import (
    CanvasUserState,
)
from short_drama.schemas.canvas.document.canvas import (
    CanvasUserStateRequest,
    CanvasViewportRequest,
    CanvasViewPreferences,
    CanvasViewPreferencesRequest,
)
from short_drama.service.base import utcnow
from short_drama.service.canvas.document.audit import iso as iso
from short_drama.service.canvas.document.base import CanvasServiceBase


class CanvasPreferences(CanvasServiceBase):
    def read_user_state(self, project_id, canvas_id):
        with self._transaction(read_only=True):
            canvas = self.require_canvas(project_id, canvas_id)
            states = self.canvas_dao.children(CanvasUserState, canvas.id)
            state = states[0] if states else None
            return {
                "row_version": str(state.row_version) if state else "0",
                "viewport": state.viewport_json if state else {"x": 0, "y": 0, "k": 1},
                "preferences": state.preferences_json if state else {},
            }

    def update_user_state(self, project_id, canvas_id, payload):
        request = CanvasUserStateRequest.model_validate(
            payload.model_dump(exclude_unset=True)
            if isinstance(payload, CanvasUserStateRequest)
            else payload
        )
        with self._transaction():
            canvas = self.require_canvas(project_id, canvas_id, lock=True)
            states = self.canvas_dao.children(CanvasUserState, canvas_id, lock=True)
            state = states[0] if states else None
            if request.expected_row_version != (state.row_version if state else 0):
                raise WorkflowError(
                    "canvas_user_state_conflict", "Personal canvas preferences changed", 409
                )
            private = self._private_state(canvas, lock=True)
            if request.viewport is not None:
                private["root"]["viewport"] = request.viewport.model_dump()
            if request.preferences is not None:
                private["root"].update(
                    {
                        field: value
                        for field, value in request.preferences.items()
                        if field != "projectId"
                    }
                )
            self._save_private(canvas, private)
            self.session.flush()
            state = self.canvas_dao.children(CanvasUserState, canvas_id, lock=True)[0]
            return {
                "row_version": str(state.row_version),
                "viewport": state.viewport_json,
                "preferences": state.preferences_json,
            }

    def update_viewport(
        self, project_id: int, canvas_id: int, payload: CanvasViewportRequest
    ) -> dict:
        with self._transaction():
            canvas = self.require_canvas(project_id, canvas_id, lock=True)
            states = self.canvas_dao.children(CanvasUserState, canvas_id, lock=True)
            state = states[0] if states else None
            current = state.viewport_json if state else {"x": 0, "y": 0, "k": 1}
            desired = payload.viewport.model_dump()
            # Binary JSON can print an IEEE double with a different last digit. Compare
            # the database's exact JSON representation, not a floating-point tolerance.
            normalized = self.session.scalar(
                select(
                    cast(
                        literal(
                            json.dumps(
                                {
                                    "expected": payload.expected_viewport.model_dump(),
                                    "desired": desired,
                                }
                            )
                        ),
                        JSON,
                    )
                )
                .select_from(ProjectCanvas)
                .where(ProjectCanvas.id == canvas.id)
            )
            stored_viewport = normalized["desired"]
            # Equality recovers a lost acknowledgement without another write. CAS only the
            # viewport field so unrelated personal preferences do not create false conflicts.
            if current != stored_viewport:
                if current != normalized["expected"]:
                    raise WorkflowError(
                        "canvas_viewport_conflict",
                        "视口已在其他窗口更新，本机位置已保留；请重新加载后继续保存",
                        409,
                    )
                if state is None:
                    state = CanvasUserState(
                        **self.child(canvas),
                        user_id=self.actor_id,
                        viewport_json=stored_viewport,
                        preferences_json={},
                        row_version=1,
                    )
                    self.session.add(state)
                else:
                    state.viewport_json = stored_viewport
                    state.row_version += 1
                    state.updated_at = utcnow()
                    state.updated_by = self.actor_id
                self.session.flush()
            return {
                "row_version": str(state.row_version) if state else "0",
                "viewport": desired,
                "preferences": state.preferences_json if state else {},
            }

    def update_view_preferences(
        self, project_id: int, canvas_id: int, payload: CanvasViewPreferencesRequest
    ) -> dict:
        with self._transaction():
            canvas = self.require_canvas(project_id, canvas_id, lock=True)
            states = self.canvas_dao.children(CanvasUserState, canvas.id, lock=True)
            state = states[0] if states else None
            previous = state.preferences_json if state else {}
            keys = {"appearance", "backgroundMode", "showImageInfo"}
            current = CanvasViewPreferences.model_validate(
                {key: value for key, value in previous.items() if key in keys}
            )
            desired = payload.preferences
            if current != desired:
                if current != payload.expected_preferences:
                    raise WorkflowError(
                        "canvas_view_preferences_conflict",
                        "外观已在其他窗口更新，本机设置已保留；请重新加载后继续保存",
                        409,
                    )
                preferences = {
                    key: deepcopy(value) for key, value in previous.items() if key not in keys
                }
                preferences.update(
                    desired.model_dump(mode="json", by_alias=True, exclude_none=True)
                )
                if state is None:
                    state = CanvasUserState(
                        **self.child(canvas),
                        user_id=self.actor_id,
                        viewport_json={"x": 0, "y": 0, "k": 1},
                        preferences_json=preferences,
                        row_version=1,
                    )
                    self.session.add(state)
                else:
                    state.preferences_json = preferences
                    state.row_version += 1
                    state.updated_at = utcnow()
                    state.updated_by = self.actor_id
                self.session.flush()
            return {
                "row_version": str(state.row_version) if state else "0",
                "preferences": desired,
            }
