import hashlib
import json

from short_drama.core.exceptions import (
    BusinessError,
)
from short_drama.dao.generations.results.records import AIGenerationRecordDAO
from short_drama.dao.generations.runtime.tasks import AsyncTaskDAO
from short_drama.domain import (
    AsyncTask,
)
from short_drama.service.base import BaseService
from short_drama.service.generations.admission.task_policy import (
    ERROR_MESSAGES as ERROR_MESSAGES,
)
from short_drama.service.generations.admission.task_policy import (
    can_reparse_storyboard as can_reparse_storyboard,
)
from short_drama.service.generations.admission.task_policy import (
    can_retry as can_retry,
)
from short_drama.service.generations.admission.task_policy import (
    resume_action as resume_action,
)
from short_drama.service.generations.admission.task_policy import (
    safe_error as safe_error,
)
from short_drama.service.generations.admission.task_policy import (
    validate_idempotency_key,
)


class GenerationServiceBase(BaseService):
    model = AsyncTask

    _key = staticmethod(validate_idempotency_key)

    def __init__(self, session, settings):
        super().__init__(session)
        self.settings = settings
        self.dao = AsyncTaskDAO(session)
        self.record_dao = AIGenerationRecordDAO(session)

    @staticmethod
    def _hash(kind, payload, operation):
        encoded = json.dumps(
            {"kind": kind, "operation": operation, "request": payload},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if len(encoded) > 1048576:
            raise BusinessError("Generation request exceeds 1 MiB")
        return hashlib.sha256(encoded).hexdigest()
