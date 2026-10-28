"""Scoped checkpoint access. JSON snapshots are never involved in SQL sorting."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain.agent_graph import AgentGraphCheckpoint, AgentGraphWrite


class AgentGraphDAO:
    def __init__(self, session: Session, owner: int, task: int, run: int, namespace: str):
        self.session = session
        self.owner, self.task, self.run, self.namespace = owner, task, run, namespace

    def _scope(self, model):
        return select(model).where(
            model.owner_user_id == self.owner,
            model.task_id == self.task,
            model.run_id == self.run,
            model.namespace == self.namespace,
        )

    def checkpoints(
        self, checkpoint_id=None, *, before=None, limit=100, metadata_filter=None, lock=False
    ):
        model = AgentGraphCheckpoint
        ids = self._scope(model).with_only_columns(model.id)
        if checkpoint_id is not None:
            ids = ids.where(model.checkpoint_id == checkpoint_id)
        if before is not None:
            ids = ids.where(model.checkpoint_id < before)
        for key, value in (metadata_filter or {}).items():
            field = model.checkpoint_metadata[key]
            if isinstance(value, bool):
                field = field.as_boolean()
            elif isinstance(value, int):
                field = field.as_integer()
            elif isinstance(value, str):
                field = field.as_string()
            ids = ids.where(field == value)
        ids = ids.order_by(model.checkpoint_id.desc()).limit(limit)
        if lock:
            ids = ids.with_for_update()
        identifiers = self.session.scalars(ids).all()
        if not identifiers:
            return []
        query = self._scope(model).where(model.id.in_(identifiers))
        if lock:
            query = query.with_for_update()
        rows = self.session.scalars(query).all()
        by_id = {row.id: row for row in rows}
        return [by_id[identifier] for identifier in identifiers]

    def writes(self, checkpoint_id):
        rows = self.session.scalars(
            self._scope(AgentGraphWrite).where(AgentGraphWrite.checkpoint_id == checkpoint_id)
        ).all()
        return sorted(rows, key=lambda row: (row.task_key, row.write_index))

    def write(self, checkpoint_id, task_key, index):
        return self.session.scalar(
            self._scope(AgentGraphWrite)
            .where(
                AgentGraphWrite.checkpoint_id == checkpoint_id,
                AgentGraphWrite.task_key == task_key,
                AgentGraphWrite.write_index == index,
            )
            .with_for_update()
        )
