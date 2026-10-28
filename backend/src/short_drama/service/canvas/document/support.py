import re

from short_drama.core.exceptions import WorkflowError
from short_drama.service.canvas.document.audit import iso as iso

"""Atomic canvas commits; private drafts never become shared document fields."""


def validate_key(key: str | None) -> str:
    if not key or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
        raise WorkflowError(
            "canvas_idempotency_required", "A stable Idempotency-Key is required", 422
        )
    return key
