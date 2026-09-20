from datetime import datetime, timedelta
from types import SimpleNamespace


def task(**values):
    return SimpleNamespace(
        **{
            "status": "running",
            "message_status": "pending",
            "next_action": "poll",
            "next_run_at": datetime(2026, 1, 1),
            **values,
        }
    )


def test_only_due_pending_actions_are_published():
    from short_drama.tasks.state import publish_due

    now = datetime(2026, 1, 2)
    assert publish_due(task(), now)
    for delivery in ("published", "publishing", "idle"):
        assert not publish_due(task(message_status=delivery), now)
    assert not publish_due(task(status="succeeded"), now)
    assert not publish_due(task(next_run_at=now + timedelta(seconds=1)), now)


def test_recovery_prefers_existing_result_and_never_resubmits_sent():
    from short_drama.tasks.state import recovery_action

    def record(**values):
        return SimpleNamespace(
            **{
                "status": "sent",
                "provider_task_id": None,
                "response_data": None,
                "text_content": None,
                **values,
            }
        )

    assert recovery_action(record()) is None
    assert recovery_action(record(status="unknown")) is None
    assert recovery_action(record(status="prepared")) == "submit"
    assert recovery_action(record(provider_task_id="upstream-1")) == "poll"
    assert (
        recovery_action(
            record(
                status="succeeded",
                provider_task_id="upstream-1",
                response_data={"media_manifest": [{"asset_id": "123"}]},
            )
        )
        == "save"
    )
    assert recovery_action(record(status="succeeded", text_content="ready")) == "save"


def test_archive_budget_is_independent_of_generation_start():
    from short_drama.tasks.state import archive_due

    now = datetime(2026, 1, 3)
    response = {"archive_started_at": (now - timedelta(seconds=20)).isoformat()}
    assert archive_due(response, now, 86400)
    assert not archive_due(response, now + timedelta(days=2), 86400)
    assert not archive_due({}, now, 86400)


def test_broker_url_escapes_credentials_and_virtual_host():
    from short_drama.core.config import Settings

    settings = Settings(
        _env_file=None, rabbitmq_user="a@b", rabbitmq_password="p:/", rabbitmq_vhost="studio/a"
    )
    assert settings.rabbitmq_url.get_secret_value().endswith("/studio%2Fa")
    assert "a%40b:p%3A%2F@" in settings.rabbitmq_url.get_secret_value()
    assert "p:/" not in repr(settings)
