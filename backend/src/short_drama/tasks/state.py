"""Evidence-based decisions shared by publisher, recovery, and worker."""

from datetime import datetime

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})
ACTIONS = frozenset({"submit", "poll", "save"})


def publish_due(task, now):
    return (
        task.status not in TERMINAL
        and task.message_status == "pending"
        and task.next_action in ACTIONS
        and task.next_run_at is not None
        and task.next_run_at <= now
    )


def recovery_action(record):
    response = record.response_data or {}
    if response.get("media_manifest") or (
        record.status == "succeeded" and record.text_content is not None
    ):
        return "save"
    if record.provider_task_id:
        return "poll"
    if record.status == "prepared":
        return "submit"
    return None


def archive_due(response, now, budget):
    try:
        started = datetime.fromisoformat(response["archive_started_at"])
        return 0 <= (now - started).total_seconds() < budget
    except (KeyError, ValueError, TypeError):
        return False
