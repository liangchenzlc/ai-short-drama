"""Latest per-block snapshots at one event watermark; no additional trace table."""

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from short_drama.domain.assistant.conversations.models import AgentEvent
from short_drama.schemas.assistant.operations.trace import TraceBlock, TraceChannel, TracePage


class AssistantTraceDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    def latest(
        self,
        conversation_id: int,
        run_id: int,
        block_id: int | str,
        cursor: int,
    ) -> AgentEvent | None:
        return self.session.scalar(
            select(AgentEvent)
            .where(
                AgentEvent.conversation_id == conversation_id,
                AgentEvent.run_id == run_id,
                AgentEvent.event_type == "trace.block",
                AgentEvent.seq <= cursor,
                AgentEvent.payload["block_id"].as_string() == str(block_id),
            )
            .order_by(AgentEvent.seq.desc())
            .limit(1)
        )

    def page(
        self,
        conversation_id: int,
        run_id: int | None,
        cursor: int,
        *,
        before_seq: int | None = None,
        limit: int = 50,
    ) -> TracePage:
        conditions = [
            AgentEvent.conversation_id == conversation_id,
            AgentEvent.event_type == "trace.block",
            AgentEvent.seq <= cursor,
        ]
        if run_id is not None:
            conditions.append(AgentEvent.run_id == run_id)
        block_id = AgentEvent.payload["block_id"].as_string()
        grouped = (
            select(
                block_id.label("block_id"),
                func.min(AgentEvent.seq).label("start"),
                func.max(AgentEvent.seq).label("latest"),
            )
            .where(*conditions)
            .group_by(block_id)
            .subquery()
        )
        query = select(grouped.c.latest, grouped.c.start)
        if before_seq is not None:
            query = query.where(grouped.c.start < before_seq)
        selected = self.session.execute(
            query.order_by(grouped.c.start.desc()).limit(limit + 1)
        ).all()
        rows = (
            self.session.scalars(
                select(AgentEvent).where(
                    AgentEvent.conversation_id == conversation_id,
                    AgentEvent.seq.in_([row.latest for row in selected[:limit]]),
                )
            ).all()
            if selected
            else []
        )
        trailing = (
            self.session.scalars(
                select(AgentEvent)
                .where(
                    AgentEvent.conversation_id == conversation_id,
                    AgentEvent.event_type == "trace.delta",
                    AgentEvent.seq <= cursor,
                    or_(
                        *(
                            and_(
                                AgentEvent.payload["block_id"].as_string()
                                == row.payload["block_id"],
                                AgentEvent.seq > row.seq,
                            )
                            for row in rows
                        )
                    ),
                )
                .order_by(AgentEvent.seq)
            ).all()
            if rows
            else []
        )
        deltas = {}
        for delta in trailing:
            deltas.setdefault(delta.payload["block_id"], []).append(delta)
        blocks = {row.seq: self.block(row, deltas.get(row.payload["block_id"], [])) for row in rows}
        return TracePage(
            items=[blocks[row.latest] for row in selected[:limit]],
            has_more=len(selected) > limit,
            next_before_seq=selected[limit - 1].start if len(selected) > limit else None,
            snapshot_cursor=cursor,
        )

    def block(self, snapshot: AgentEvent, deltas: list[AgentEvent]) -> TraceBlock:
        from short_drama.agent.runtime.trace_content import utf8_preview

        block = TraceBlock.model_validate(
            {key: value for key, value in snapshot.payload.items() if not key.startswith("_")}
        )
        # A producer flush normally writes delta+snapshot atomically. Reading a trailing
        # delta is still necessary for a snapshot followed by an in-flight channel update.
        for row in deltas:
            delta = row.payload
            if int(delta["block_version"]) < block.block_version or block.finished_at is not None:
                continue
            channel = block.channels.setdefault(delta["channel"], TraceChannel())
            if delta["offset"] != channel.total_bytes:
                continue
            if not channel.truncated:
                channel.preview = utf8_preview(channel.preview + delta["delta"])
            channel.total_bytes += len(delta["delta"].encode("utf-8"))
            channel.truncated = channel.total_bytes > len(channel.preview.encode("utf-8"))
            block.block_version = int(delta["block_version"])
            block.updated_at = row.created_at
        return block
