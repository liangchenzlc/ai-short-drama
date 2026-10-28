"""批量任务共用依赖与状态聚合规则。"""

import hashlib
import json
from collections import Counter

from short_drama.core.exceptions import (
    WorkflowError,
)
from short_drama.domain import (
    GenerationBatchJob,
)
from short_drama.service.base import BaseService
from short_drama.service.generations.admission.generation import AIGenerationService


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def item_status(task, record):
    if record.status in {"unknown", "sent"} and task.status == "failed":
        return "needs_review"
    if (task.error or {}).get("code") in {
        "provider_acceptance_unknown",
        "acceptance_unknown",
        "message_delivery_unknown",
    }:
        return "needs_review"
    return task.status if task.status in {"succeeded", "failed", "cancelled"} else "active"


def batch_status(states, control):
    counts = Counter(states)
    if counts["needs_review"]:
        return "needs_review"
    if counts["waiting"] or counts["active"] or counts["blocked"]:
        return control if control in {"paused", "cancelled", "needs_review"} else "running"
    if counts["succeeded"] == len(states):
        return "succeeded"
    if counts["succeeded"]:
        return "partial"
    if counts["failed"]:
        return "failed"
    return "cancelled"


class GenerationBatchServiceBase(BaseService):
    model = GenerationBatchJob

    def __init__(self, session, settings):
        super().__init__(session)
        self.settings = settings
        self.generations = AIGenerationService(session, settings)

    def _enabled(self):
        if not self.settings.generation_batches_enabled:
            raise WorkflowError("batch_disabled", "批量生成尚未启用", 503)
