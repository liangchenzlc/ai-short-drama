"""画布节点与独立模型测试共用原任务阶段投影。"""

from short_drama.domain import AIGenerationRecord, AsyncTask


def canvas_task_stage(task: AsyncTask, latest: AIGenerationRecord) -> str:
    if task.status == "failed" and latest.status == "unknown":
        return "submission_unknown"
    return {
        "queued": "queued",
        "running": {"submit": "submitting", "poll": "generating", "save": "saving"}.get(
            task.next_action, "processing"
        ),
        "succeeded": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }[task.status]
