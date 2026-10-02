from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.schemas.generation_batch import BatchPreflight
from short_drama.service.generation_batch_service import batch_status, item_status


def test_batch_unknown_never_reports_completion_or_releases_as_normal_failure():
    task = SimpleNamespace(status="failed", error={"code": "provider_acceptance_unknown"})
    assert item_status(task, SimpleNamespace(status="unknown")) == "needs_review"
    assert batch_status(["succeeded", "needs_review"], "running") == "needs_review"
    assert batch_status(["succeeded", "failed"], "running") == "partial"
    assert batch_status(["succeeded", "waiting"], "paused") == "paused"
    assert batch_status(["succeeded", "succeeded"], "running") == "succeeded"


def test_batch_scope_and_limits_are_server_validated():
    base = {
        "scene": "shot_image",
        "config_id": "1",
        "scope": {"library": "episode", "project_id": "2", "episode_id": "3"},
    }
    assert BatchPreflight.model_validate(base).count == 1
    for patch in [
        {"source_ids": ["4", "4"]},
        {"source_ids": list(map(str, range(1, 102)))},
        {"scope": {"library": "global"}},
        {"count": 5},
        {"scene": "shot_video", "count": 2},
    ]:
        with pytest.raises(ValidationError):
            BatchPreflight.model_validate({**base, **patch})
