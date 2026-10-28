"""Task reads share the caller's conversation lock and never commit."""

from types import SimpleNamespace

from sqlalchemy import JSON, case, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement

from short_drama.domain.assistant.conversations.models import AgentMessage
from short_drama.domain.assistant.execution.models import AgentRun
from short_drama.domain.assistant.tasks.task import (
    AgentOperation,
    AgentTask,
    AgentTaskControl,
    AgentTaskInput,
)

OPEN_OPERATIONS = ("running", "waiting_media", "reconciling")
RESUMABLE_TASKS = ("stopped", "paused_error", "paused_budget")
ACTIVE_TASKS = ("queued", "running", "waiting_media")


class _CatalogArray(FunctionElement):
    """Project JSON array members inside the DB; never transfer their bodies."""

    type = JSON()
    inherit_cache = False

    def __init__(self, value, fields):
        super().__init__(value)
        self.fields = fields


def _catalog_object(element, column):
    # All paths are developer-owned constants, never request parameters.
    return (
        "JSON_OBJECT("
        + ",".join(f"'{key}',JSON_EXTRACT({column},'$.{path}')" for key, path in element.fields)
        + ")"
    )


@compiles(_CatalogArray, "mysql")
def _mysql_catalog(element, compiler, **kwargs):
    value = compiler.process(list(element.clauses)[0], **kwargs)
    item = _catalog_object(element, "entry.value")
    return (
        f"(SELECT COALESCE(JSON_ARRAYAGG({item}),JSON_ARRAY()) "
        f"FROM JSON_TABLE({value}, '$[*]' COLUMNS (value JSON PATH '$')) AS entry "
        "WHERE JSON_TYPE(entry.value) = 'OBJECT')"
    )


@compiles(_CatalogArray)
def _sqlite_catalog(element, compiler, **kwargs):
    value = compiler.process(list(element.clauses)[0], **kwargs)
    return (
        f"(SELECT JSON_GROUP_ARRAY({_catalog_object(element, 'value')}) "
        f"FROM JSON_EACH({value}) WHERE type = 'object')"
    )


SUBJECT_FIELDS = (
    "subject_key",
    "snapshot_id",
    "project_id",
    "project_name",
    "source_kind",
    "source_id",
    "kind",
    "id",
    "revision",
    "content_revision",
    "checksum",
    "name",
    "allowed_fields",
    "source_location",
)
SKILL_FIELDS = ("id", "name", "content_version", "builtin")
ACTIVATION_FIELDS = (
    "id",
    "source",
    "purpose",
    "active",
    "plan_revision",
    "created_at",
    "issue",
)


def _fields(names):
    return tuple((name, name) for name in names)


class AgentTaskDAO:
    def __init__(self, session):
        self.session = session

    def task(self, identifier, *, conversation_id=None, lock=False, refresh=True):
        query = select(AgentTask).where(AgentTask.id == identifier)
        if conversation_id is not None:
            query = query.where(AgentTask.conversation_id == conversation_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=refresh)
        return self.session.scalar(query)

    def result_sources(self, task_id, *, before_id=None, limit=10):
        query = select(AgentOperation.id).where(
            AgentOperation.task_id == task_id,
            AgentOperation.tool_name.in_(("apply_standard_patch", "apply_canvas_patch")),
            AgentOperation.status == "succeeded",
        )
        if before_id is not None:
            query = query.where(AgentOperation.id < before_id)
        ids = list(self.session.scalars(query.order_by(AgentOperation.id.desc()).limit(limit + 1)))
        rows = self.session.execute(
            select(AgentOperation.id, AgentOperation.tool_name, AgentOperation.result_refs).where(
                AgentOperation.task_id == task_id, AgentOperation.id.in_(ids[:limit])
            )
        ).all()
        return sorted(rows, key=lambda row: row.id, reverse=True), len(ids) > limit

    def page_ids(self, conversation_id, *, before_id=None, limit=21):
        query = select(AgentTask.id).where(AgentTask.conversation_id == conversation_id)
        if before_id is not None:
            query = query.where(AgentTask.id < before_id)
        return list(self.session.scalars(query.order_by(AgentTask.id.desc()).limit(limit)))

    def projections(self, conversation_id, task_ids):
        excluded = {"context_snapshot", "selected_skills", "effective_state"}
        columns = [column for column in AgentTask.__table__.columns if column.name not in excluded]
        modern = AgentTask.context_snapshot["schema_version"].as_integer() == 2
        rows = self.session.execute(
            select(
                *columns,
                _CatalogArray(
                    AgentTask.context_snapshot["subjects"], _fields(SUBJECT_FIELDS)
                ).label("subjects"),
                case((modern, None), else_=AgentTask.context_snapshot).label("legacy_snapshot"),
                _CatalogArray(AgentTask.selected_skills, _fields(SKILL_FIELDS)).label("skills"),
                AgentTask.effective_state["engine"].as_string().label("engine"),
                _CatalogArray(
                    AgentTask.effective_state["skill_activations"],
                    (
                        *_fields(ACTIVATION_FIELDS),
                        *(
                            (f"skill_{name}", f"skill.{name}")
                            for name in ("id", "name", "content_version", "package_checksum")
                        ),
                    ),
                ).label("activations"),
            ).where(AgentTask.conversation_id == conversation_id, AgentTask.id.in_(task_ids))
        )
        result = {}
        for row in rows:
            values = dict(row._mapping)
            # Absent optional fields must remain absent, not override DTO defaults.
            subjects = [
                {key: value for key, value in item.items() if value is not None}
                for item in values.pop("subjects") or []
            ]
            snapshot = values.pop("legacy_snapshot") or {"schema_version": 2, "subjects": subjects}
            records = []
            for item in values.pop("activations") or []:
                item["skill"] = {
                    name: item.pop(f"skill_{name}")
                    for name in ("id", "name", "content_version", "package_checksum")
                }
                records.append(item)
            values.update(
                context_snapshot=snapshot,
                selected_skills=values.pop("skills") or [],
                effective_state={"engine": values.pop("engine"), "skill_activations": records},
            )
            result[row.id] = SimpleNamespace(**values)
        return [result[identifier] for identifier in task_ids if identifier in result]

    def operation_projections(self, task_ids, *, after_id=None, limit=None, active=False):
        query = select(AgentOperation.id).where(AgentOperation.task_id.in_(task_ids))
        if after_id is not None:
            query = query.where(AgentOperation.id > after_id)
        if active:
            query = query.where(AgentOperation.status.in_(OPEN_OPERATIONS))
        query = query.order_by(AgentOperation.id)
        if limit is not None:
            query = query.limit(limit)
        ids = list(self.session.scalars(query))
        if not ids:
            return []
        columns = [
            column
            for column in AgentOperation.__table__.columns
            if column.name not in {"arguments", "result", "changes"}
        ]
        rows = self.session.execute(
            select(
                *columns,
                AgentOperation.result["skill_activations"].label("skill_activations"),
            ).where(AgentOperation.id.in_(ids))
        )
        by_id = {}
        for row in rows:
            values = dict(row._mapping)
            values["result"] = {"skill_activations": values.pop("skill_activations") or []}
            by_id[row.id] = SimpleNamespace(**values)
        return [by_id[identifier] for identifier in ids]

    def selected_task(self, conversation_id):
        return self.session.scalar(
            select(AgentTask)
            .where(
                AgentTask.conversation_id == conversation_id,
                AgentTask.status.in_((*ACTIVE_TASKS, *RESUMABLE_TASKS)),
            )
            .order_by(
                case((AgentTask.status.in_(("running", "waiting_media")), 0), else_=1),
                AgentTask.id.desc(),
            )
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def latest_run(self, task_id):
        return self.session.scalar(
            select(AgentRun)
            .where(AgentRun.task_id == task_id)
            .order_by(AgentRun.id.desc())
            .limit(1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def active_operation(self, task_id, *, lock=True):
        query = select(AgentOperation).where(
            AgentOperation.task_id == task_id, AgentOperation.status.in_(OPEN_OPERATIONS)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def inputs(self, task):
        return self.session.scalars(
            select(AgentTaskInput)
            .where(
                AgentTaskInput.task_id == task.id,
                AgentTaskInput.input_revision > task.applied_input_revision,
                AgentTaskInput.input_revision <= task.input_revision,
            )
            .order_by(AgentTaskInput.input_revision)
            .with_for_update()
        ).all()

    def controls(self, task):
        return self.session.scalars(
            select(AgentTaskControl)
            .where(AgentTaskControl.task_id == task.id, AgentTaskControl.applied_at.is_(None))
            .order_by(AgentTaskControl.seq)
            .with_for_update()
        ).all()

    def input_message(self, task_id, message_id):
        return self.session.scalar(
            select(AgentMessage)
            .join(AgentTaskInput, AgentTaskInput.message_id == AgentMessage.id)
            .where(
                AgentMessage.id == message_id,
                AgentMessage.task_id == task_id,
                AgentMessage.role == "user",
                AgentTaskInput.task_id == task_id,
            )
            .with_for_update()
        )
