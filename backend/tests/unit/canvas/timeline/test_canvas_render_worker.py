"""画布渲染消息只有持久执行结果后才能确认；失败不暴露异常内容。"""

from types import SimpleNamespace

import pytest
from celery.exceptions import Reject

import short_drama.tasks.generations.worker as worker


def test_canvas_render_task_delegates_original_decimal_strings(monkeypatch):
    calls = []
    monkeypatch.setattr(
        worker,
        "canvas_render_service",
        lambda: SimpleNamespace(
            execute=lambda identifier, version: calls.append((identifier, version)),
        ),
    )
    worker.execute_canvas_render("9007199254740993", "18446744073709551615")
    assert calls == [("9007199254740993", "18446744073709551615")]


def test_canvas_render_task_rejects_when_durable_receipt_cannot_be_written(monkeypatch):
    def unavailable(*_args):
        raise RuntimeError("Injected private connection detail")

    monkeypatch.setattr(
        worker, "canvas_render_service", lambda: SimpleNamespace(execute=unavailable)
    )
    with pytest.raises(Reject) as failure:
        worker.execute_canvas_render("9007199254740993", "1")
    assert failure.value.requeue is False and "private connection" not in str(failure.value)
