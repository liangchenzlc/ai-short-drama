"""Public waiting facts derived from leaf operations, without scheduling state."""

from short_drama.core.time import utcnow
from short_drama.dao.assistant.execution.waiting import AgentWaitingDAO


def waiting_summaries(session, run_ids):
    grouped = {}
    for row in AgentWaitingDAO(session).pending_projections(run_ids):
        grouped.setdefault(row.run_id, []).append(row)
    result = {}
    for run_id, rows in grouped.items():
        questions = sum(row.wait_state["kind"] == "user_input" for row in rows)
        batches = {
            tuple(sorted(row.wait_state["task_ids"]))
            for row in rows
            if row.wait_state["kind"] == "generation"
        }
        parts = []
        if questions:
            parts.append(f"{questions} 项待答问题")
        if batches:
            parts.append(f"{len(batches)} 批生成结果")
        since = min(row.created_at for row in rows)
        result[run_id] = {
            "pending_questions": questions,
            "media_batches": len(batches),
            "generation_tasks": len({identifier for batch in batches for identifier in batch}),
            "since": since,
            "elapsed_seconds": max(0, int((utcnow() - since).total_seconds())),
            "message": "等待" + "、".join(parts),
        }
    return result
