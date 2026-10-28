from __future__ import annotations

from typing import TypeVar

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from short_drama.domain import CanvasToolRun, CanvasToolSubmission

Row = TypeVar("Row")


class CanvasToolRunDAO:
    def __init__(self, session: Session) -> None:
        self.session = session

    @staticmethod
    def _current(query: Select[tuple[Row]], lock: bool) -> Select[tuple[Row]]:
        return query.with_for_update().execution_options(populate_existing=True) if lock else query

    def run(self, identifier: int, *, lock: bool = False) -> CanvasToolRun | None:
        return self.session.scalar(
            self._current(select(CanvasToolRun).where(CanvasToolRun.id == identifier), lock)
        )

    def by_key(self, user_id: int, key: str) -> CanvasToolRun | None:
        return self.session.scalar(
            self._current(
                select(CanvasToolRun).where(
                    CanvasToolRun.user_id == user_id, CanvasToolRun.client_key == key
                ),
                True,
            )
        )

    def list(self, user_id: int, *, limit: int = 100) -> list[CanvasToolRun]:
        return list(
            self.session.scalars(
                select(CanvasToolRun)
                .where(CanvasToolRun.user_id == user_id)
                .order_by(CanvasToolRun.updated_at.desc(), CanvasToolRun.id.desc())
                .limit(limit)
            )
        )

    def submissions(self, run_id: int) -> list[CanvasToolSubmission]:
        return list(
            self.session.scalars(
                self._current(
                    select(CanvasToolSubmission)
                    .where(CanvasToolSubmission.run_id == run_id)
                    .order_by(CanvasToolSubmission.id),
                    True,
                )
            )
        )
