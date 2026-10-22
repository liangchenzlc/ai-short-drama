"""Canvas actor and child audit identity shared by document and drawing writes."""

from datetime import UTC

from short_drama.core.exceptions import WorkflowError
from short_drama.utils.snowflake import next_id

from .base import BaseService, utcnow


def iso(value):
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


class CanvasAuditService(BaseService):
    @property
    def actor_id(self) -> int:
        actor = self.session.info.get("actor")
        if actor is None:
            raise WorkflowError("authentication_required", "Sign in to use canvases", 401)
        return actor.user_id

    def audit(self) -> dict:
        now = utcnow()
        return dict(
            id=next_id(),
            created_at=now,
            updated_at=now,
            created_by=self.actor_id,
            updated_by=self.actor_id,
        )

    def child(self, canvas) -> dict:
        return {**self.audit(), "project_id": canvas.project_id, "canvas_id": canvas.id}
