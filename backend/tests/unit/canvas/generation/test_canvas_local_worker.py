"""本地执行必须持久化终态后确认消息；执行失败交数据库租约恢复。"""

from types import SimpleNamespace

import pytest
from celery.exceptions import Reject

import short_drama.tasks.generations.worker as worker


def test_local_worker_preserves_decimal_identity_and_confirms_only_after_execute(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker,
        "canvas_local_service",
        lambda: SimpleNamespace(
            execute=lambda identifier, version: calls.append((identifier, version))
        ),
    )
    worker.execute_canvas_local("9007199254740993", "18446744073709551615")
    assert calls == [("9007199254740993", "18446744073709551615")]
    assert (
        worker.execute_canvas_local.acks_late and worker.execute_canvas_local.reject_on_worker_lost
    )


def test_missing_durable_receipt_rejects_message_without_leaking_private_error(monkeypatch):
    def unavailable(*_):
        raise RuntimeError("Injected private database connection detail")

    monkeypatch.setattr(
        worker, "canvas_local_service", lambda: SimpleNamespace(execute=unavailable)
    )
    with pytest.raises(Reject) as failure:
        worker.execute_canvas_local("9007199254740993", "1")
    assert failure.value.requeue is False
    assert "private database" not in str(failure.value)
