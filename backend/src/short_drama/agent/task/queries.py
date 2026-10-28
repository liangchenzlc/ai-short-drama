"""Task lookup under the coordinator's existing task lock."""

from short_drama.dao.assistant.tasks.task import AgentTaskDAO


def task_for_run(session, run):
    task_id = getattr(run, "task_id", None)
    # lock_run already refreshed the task at transaction entry. Repeated checks
    # must retain changes made under that lock when Session.autoflush is disabled.
    return (
        AgentTaskDAO(session).task(task_id, lock=True, refresh=False)
        if task_id is not None
        else None
    )
