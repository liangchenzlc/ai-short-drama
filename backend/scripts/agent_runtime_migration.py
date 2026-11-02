"""离线终结旧助手编排、归档审计并应用006；不删除业务数据或重放旧循环。"""

import argparse
import hashlib
import json
import logging
import re
from pathlib import Path

from kombu import Connection
from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from short_drama.agent.core import ENGINE_VERSION
from short_drama.agent.core.events import append_event
from short_drama.agent.core.state import append_failure_message
from short_drama.core.config import Settings
from short_drama.db.readiness import _normalize_sql, _type_signature, inspect_agent_schema
from short_drama.db.session import build_engine
from short_drama.domain import AGENT_TABLES
from short_drama.domain.assistant.conversations.models import AgentConversation
from short_drama.domain.assistant.execution.models import AgentRun

logger = logging.getLogger(__name__)
SQL_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs/database/migrations/2026-10-29-assistant-runtime/006-retire-harness.sql"
)
# 冻结119表依次执行001–005及006每个DDL后的结构摘要；只接受已审阅的阶段。
SCHEMA_PHASES = (
    "d92e7536932f1c042f7d829efcd40d89f9146f3a23ba2ff29b92a92e35cee96b",
    "a9f8627511cfd21028823ebaae9fb6c4c4e0596d61f75706a7aec4d6cdb25c84",
    "3ed86501588d24d5b568cb45ddd8fd707638062badda2f0a96d7d9516e878054",
    "c5313e304564aef5455a847839be1486ec8cdc5ce846a925584db80155677d03",
    "cf5843234e4dc1ac5361871a9f04ad6bfe57a9fa1894a3d247de41f484bd02e8",
    "c38340269dbf79114761fdfed2880967ddb7aeb714d936a20ca444252befd317",
)
OLD_RUN = "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(checkpoint,'$.engine')),'') <> :engine"
ERROR = {"code": "assistant_engine_updated"}
AUDIT_KEY = "historical_runtime_audit"
CONSUMED_TOOL_NAMES = {
    "submit_generation_batch": "workbench__submit_generation_batch",
    "apply_standard_patch": "workbench__apply_standard_patch",
    "apply_canvas_patch": "workbench__apply_canvas_patch",
    "set_image_group_primary": "workbench__set_image_group_primary",
}


def schema_digest(connection):
    inspector = inspect(connection)
    names = set(inspector.get_table_names())
    names = (names & AGENT_TABLES) | {name for name in names if name.startswith("agent_")}
    shape = {}
    for name in sorted(names):
        columns = {}
        for column in inspector.get_columns(name):
            computed = column.get("computed")
            columns[column["name"]] = (
                _type_signature(column["type"]),
                column["nullable"],
                _normalize_sql(column.get("default")) if column.get("default") else None,
                (_normalize_sql(computed["sqltext"]), computed["persisted"]) if computed else None,
            )
        checks = {
            row["name"]: _normalize_sql(row["sqltext"])
            for row in inspector.get_check_constraints(name)
        }
        enforced = sorted(
            connection.scalars(
                text(
                    "SELECT CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table "
                    "AND CONSTRAINT_TYPE='CHECK' AND ENFORCED='YES'"
                ),
                {"table": name},
            )
        )
        shape[name] = {
            "columns": columns,
            "primary": inspector.get_pk_constraint(name)["constrained_columns"],
            "indexes": {
                row["name"]: (row["column_names"], bool(row["unique"]))
                for row in inspector.get_indexes(name)
            },
            "foreign_keys": {
                row["name"]: (
                    row["constrained_columns"],
                    row["referred_table"],
                    row["referred_columns"],
                    (row.get("options") or {}).get("ondelete", "RESTRICT"),
                    (row.get("options") or {}).get("onupdate", "RESTRICT"),
                )
                for row in inspector.get_foreign_keys(name)
            },
            "checks": checks,
            "enforced": enforced,
            "options": inspector.get_table_options(name),
        }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()


def worker_consumers(settings):
    # Worker禁用了remote control；inspect/ping没有响应不能证明消费者已停止。
    name = f"{settings.generation_queue_namespace}.tasks.agent"
    with Connection(
        settings.rabbitmq_url.get_secret_value(),
        connect_timeout=5,
        ssl=True if settings.rabbitmq_tls else False,
    ) as broker:
        channel = broker.channel()
        try:
            return channel.queue_declare(queue=name, passive=True).consumer_count
        except Exception as error:
            if getattr(error, "reply_code", None) == 404:
                return 0
            raise
        finally:
            channel.close()


def precheck(engine, settings, *, worker_probe=worker_consumers):
    blockers = ["agent_enabled"] if settings.agent_enabled else []
    try:
        consumers = worker_probe(settings)
        if consumers:
            blockers.append("agent_workers_running")
    except Exception as error:
        consumers = None
        blockers.append("agent_workers_unverified")
        logger.warning("Agent queue probe unavailable: %s", type(error).__name__)
    with engine.connect() as connection:
        digest = schema_digest(connection)
        phase = SCHEMA_PHASES.index(digest) if digest in SCHEMA_PHASES else None
        if phase is None:
            return {
                "status": "blocked",
                "phase": None,
                "schema_digest": digest,
                "blockers": [*blockers, "unknown_schema"],
            }
        live_leases = connection.scalar(
            text(
                "SELECT (SELECT COUNT(*) FROM agent_runs WHERE lease_until>UTC_TIMESTAMP(6)) + "
                "(SELECT COUNT(*) FROM agent_memory_jobs WHERE lease_until>UTC_TIMESTAMP(6) "
                "OR publish_until>UTC_TIMESTAMP(6))"
            )
        )
        if live_leases:
            blockers.append("live_leases")
        old_active = connection.scalar(
            text(
                f"SELECT COUNT(*) FROM agent_runs WHERE {OLD_RUN} "
                "AND status NOT IN ('succeeded','failed','cancelled')"
            ),
            {"engine": ENGINE_VERSION},
        )
        expired = connection.scalar(
            text("SELECT COUNT(*) FROM agent_runs WHERE lease_until<=UTC_TIMESTAMP(6)")
        )
    return {
        "status": "blocked" if blockers else "ready",
        "phase": phase,
        "blockers": blockers,
        "worker_consumers": consumers,
        "live_leases": live_leases,
        "old_active_runs": old_active,
        "expired_run_leases": expired,
    }


def _json(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def _save_audit(connection, table, identifier, audit):
    result = _json(
        connection.scalar(text(f"SELECT result FROM {table} WHERE id=:id"), {"id": identifier})
    )
    result[AUDIT_KEY] = {**result.get(AUDIT_KEY, {}), **audit}
    connection.execute(
        text(f"UPDATE {table} SET result=:result WHERE id=:id"),
        {"id": identifier, "result": json.dumps(result, ensure_ascii=False)},
    )


def _archive_audit(connection, phase):
    if phase == 0:
        rows = (
            connection.execute(
                text(
                    "SELECT id,review_payload,review_hash,review_version,review_decision,"
                    "reviewed_by,reviewed_at FROM agent_tool_calls WHERE review_payload IS NOT NULL"
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            review = {key: value for key, value in row.items() if key != "id"}
            review["review_payload"] = _json(review["review_payload"])
            review["reviewed_at"] = (
                review["reviewed_at"].isoformat() if review["reviewed_at"] else None
            )
            _save_audit(connection, "agent_tool_calls", row["id"], {"human_review": review})
    if phase <= 1:
        for row in (
            connection.execute(
                text(
                    "SELECT id,result_digest,evidence_kind FROM agent_operations "
                    "WHERE result_digest IS NOT NULL OR evidence_kind IS NOT NULL"
                )
            )
            .mappings()
            .all()
        ):
            _save_audit(
                connection,
                "agent_operations",
                row["id"],
                {
                    "result_digest": row["result_digest"],
                    "evidence_kind": row["evidence_kind"],
                },
            )
    for row in (
        connection.execute(
            text(
                "SELECT id,effective_state FROM agent_tasks WHERE "
                "JSON_CONTAINS_PATH(effective_state,'one','$.media_evidence','$.resource_outcomes')"
            )
        )
        .mappings()
        .all()
    ):
        state = _json(row["effective_state"])
        evidence, outcomes = state.get("media_evidence", {}), state.get("resource_outcomes", {})
        for identifier in evidence.keys() | outcomes.keys():
            operation = (
                connection.execute(
                    text(
                        "SELECT id,tool_call_id FROM agent_operations "
                        "WHERE id=:id AND task_id=:task"
                    ),
                    {"id": identifier, "task": row["id"]},
                )
                .mappings()
                .one_or_none()
            )
            if operation is None:
                raise RuntimeError("Offline cutover refused: media audit has no owning operation")
            audit = {
                key: value
                for key, value in {
                    "media_evidence": evidence.get(identifier),
                    "resource_outcome": outcomes.get(identifier),
                }.items()
                if value is not None
            }
            _save_audit(connection, "agent_operations", operation["id"], audit)
            _save_audit(connection, "agent_tool_calls", operation["tool_call_id"], audit)


def _cutover_data(connection, phase):
    _archive_audit(connection, phase)
    _normalize_consumed_tool_names(connection)
    values = {"engine": ENGINE_VERSION, "error": json.dumps(ERROR)}
    old_ids = f"SELECT id FROM agent_runs WHERE {OLD_RUN}"
    with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
        with session.begin():
            active = session.scalars(
                select(AgentRun)
                .where(text(OLD_RUN), AgentRun.status.not_in(("succeeded", "failed", "cancelled")))
                .params(engine=ENGINE_VERSION)
            ).all()
            for run in active:
                conversation = session.get(AgentConversation, run.conversation_id)
                append_failure_message(session, conversation, run, ERROR)
                append_event(
                    session,
                    conversation,
                    "run.finished",
                    {
                        "run_id": str(run.id),
                        "status": "cancelled",
                        "error": ERROR,
                    },
                    run_id=run.id,
                )
    for table in ("agent_graph_writes", "agent_graph_checkpoints"):
        connection.execute(text(f"DELETE FROM {table} WHERE run_id IN ({old_ids})"), values)
    connection.execute(
        text(
            "UPDATE agent_turns SET status=IF(status='sent','unknown','failed'),"
            "error=:error,finished_at=UTC_TIMESTAMP(6),updated_at=UTC_TIMESTAMP(6) "
            f"WHERE run_id IN ({old_ids}) AND status IN ('prepared','sent')"
        ),
        values,
    )
    connection.execute(
        text(
            "UPDATE agent_operations o JOIN agent_tool_calls c ON c.id=o.tool_call_id "
            f"JOIN agent_runs r ON r.id=o.run_id SET o.status='failed',o.phase='terminal',"
            "o.error=:error,o.finished_at=UTC_TIMESTAMP(6),o.updated_at=UTC_TIMESTAMP(6),"
            "o.row_version=o.row_version+1 "
            f"WHERE {OLD_RUN.replace('checkpoint', 'r.checkpoint')} "
            "AND o.status NOT IN ('succeeded','failed','conflict') "
            "AND c.generation_task_id IS NULL AND NOT EXISTS "
            "(SELECT 1 FROM agent_media_requests m WHERE m.operation_id=o.id "
            "AND m.generation_task_id IS NOT NULL)"
        ),
        values,
    )
    connection.execute(
        text(
            "UPDATE agent_tool_calls c JOIN agent_runs r ON r.id=c.run_id "
            "SET c.status='cancelled',c.error=:error,c.updated_at=UTC_TIMESTAMP(6) "
            f"WHERE {OLD_RUN.replace('checkpoint', 'r.checkpoint')} "
            "AND c.status IN ('prepared','ready','waiting_review','running','waiting_input') "
            "AND c.generation_task_id IS NULL AND NOT EXISTS (SELECT 1 FROM agent_media_requests m "
            "WHERE m.operation_id IN "
            "(SELECT o.id FROM agent_operations o WHERE o.tool_call_id=c.id) "
            "AND m.generation_task_id IS NOT NULL)"
        ),
        values,
    )
    connection.execute(
        text(
            f"UPDATE agent_runs SET status='cancelled',phase='wait',error=:error,"
            "finished_at=UTC_TIMESTAMP(6),updated_at=UTC_TIMESTAMP(6),next_run_at=NULL,"
            "message_status='idle',message_version=message_version+1,row_version=row_version+1,"
            "lease_token=NULL,lease_until=NULL,cancel_requested=1 "
            f"WHERE {OLD_RUN} AND status NOT IN ('succeeded','failed','cancelled')"
        ),
        values,
    )
    connection.execute(
        text(
            f"UPDATE agent_runs SET lease_token=NULL,lease_until=NULL WHERE {OLD_RUN} "
            "AND lease_until IS NOT NULL"
        ),
        values,
    )
    for row in (
        connection.execute(
            text(
                "SELECT id,effective_state FROM agent_tasks WHERE "
                "COALESCE(JSON_UNQUOTE(JSON_EXTRACT(effective_state,'$.engine')),'')<>:engine"
            ),
            values,
        )
        .mappings()
        .all()
    ):
        state = _json(row["effective_state"])
        state = {
            key: value
            for key, value in state.items()
            if key
            not in {
                "contract",
                "progress",
                "completion_candidate",
                "completion_review",
                "stall",
                "task_constraint_state",
                "task_constraint_job",
                "task_constraint_stale",
                "media_evidence",
                "resource_outcomes",
            }
        }
        state.update(engine=ENGINE_VERSION, agent_policy={"stop": "boundary.v1"})
        connection.execute(
            text(
                "UPDATE agent_tasks SET effective_state=:state,"
                "status=IF(status='succeeded','succeeded','stopped'),phase='terminal',"
                "error=IF(status='succeeded',error,:error),"
                "control_state='none',row_version=row_version+1,"
                "updated_at=UTC_TIMESTAMP(6) WHERE id=:id"
            ),
            {**values, "id": row["id"], "state": json.dumps(state, ensure_ascii=False)},
        )
    connection.execute(text("DELETE FROM agent_memory_jobs WHERE kind='task_state'"))
    connection.execute(
        text(
            "UPDATE agent_memory_jobs SET lease_token=NULL,lease_until=NULL,"
            "publish_token=NULL,publish_until=NULL WHERE lease_until<=UTC_TIMESTAMP(6) "
            "OR publish_until<=UTC_TIMESTAMP(6)"
        )
    )


def _normalize_consumed_tool_names(connection):
    # 仅适配仍由当前媒体结算或撤销消费的旧身份；原名保留在各自审计中。
    for table in ("agent_operations", "agent_tool_calls"):
        rows = (
            connection.execute(
                text(
                    f"SELECT t.id,t.tool_name FROM {table} t "
                    "JOIN agent_runs r ON r.id=t.run_id WHERE "
                    f"{OLD_RUN.replace('checkpoint', 'r.checkpoint')} "
                    "AND t.tool_name IN ('submit_generation_batch','apply_standard_patch',"
                    "'apply_canvas_patch','set_image_group_primary')"
                ),
                {"engine": ENGINE_VERSION},
            )
            .mappings()
            .all()
        )
        for row in rows:
            _save_audit(connection, table, row["id"], {"tool_name": row["tool_name"]})
            connection.execute(
                text(f"UPDATE {table} SET tool_name=:name WHERE id=:id"),
                {"id": row["id"], "name": CONSUMED_TOOL_NAMES[row["tool_name"]]},
            )


def apply(engine, settings, *, worker_probe=worker_consumers):
    state = precheck(engine, settings, worker_probe=worker_probe)
    if state["blockers"]:
        raise RuntimeError("Offline cutover refused: " + ", ".join(state["blockers"]))
    phase = state["phase"]
    with engine.begin() as connection:
        _cutover_data(connection, phase)
    source = re.sub(r"(?m)^\s*--.*$", "", SQL_PATH.read_text(encoding="utf-8"))
    statements = [statement.strip() for statement in source.split(";") if statement.strip()]
    for index in range(phase, len(statements)):
        with engine.begin() as connection:
            connection.exec_driver_sql(statements[index])
        with engine.connect() as connection:
            if schema_digest(connection) != SCHEMA_PHASES[index + 1]:
                raise RuntimeError("Offline cutover refused: unexpected schema after DDL")
        logger.info("006 DDL %s/%s verified", index + 1, len(statements))
    with engine.connect() as connection:
        ready = inspect_agent_schema(connection)
    if ready["status"] != "ready":
        raise RuntimeError("Offline cutover refused: runtime schema not ready")
    return {**state, "status": "ready", "phase": len(statements)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--precheck", action="store_true")
    actions.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    engine = None
    try:
        settings = Settings()
        engine = build_engine(settings)
        result = apply(engine, settings) if args.apply else precheck(engine, settings)
        logger.info("%s", json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ready" else 1
    except Exception as error:
        logger.error(
            "Runtime cutover unavailable (%s); database details suppressed", type(error).__name__
        )
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
