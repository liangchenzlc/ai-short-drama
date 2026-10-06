from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.domain import Project, ProjectCanvas, ProjectCanvasSettings
from short_drama.domain.canvas import CanvasWriteReceipt
from short_drama.domain.collaboration import User


class CanvasDAO:
    def __init__(self, session: Session):
        self.session = session

    def lock_actor(self, user_id: int):
        return self.session.scalar(select(User).where(User.id == user_id).with_for_update())

    def project(self, project_id: int, *, lock: bool = False):
        query = select(Project).where(Project.id == project_id, Project.archived_at.is_(None))
        return self.session.scalar(query.with_for_update() if lock else query)

    def canvas(self, project_id: int, canvas_id: int, *, lock: bool = False):
        query = select(ProjectCanvas).where(
            ProjectCanvas.project_id == project_id,
            ProjectCanvas.id == canvas_id,
            ProjectCanvas.archived_at.is_(None),
        )
        return self.session.scalar(query.with_for_update() if lock else query)

    def settings(self, project_id: int):
        return self.session.scalar(
            select(ProjectCanvasSettings).where(ProjectCanvasSettings.project_id == project_id)
        )

    def receipt(self, user_id: int, key: str, *, lock: bool = False):
        statement = select(CanvasWriteReceipt).where(
            CanvasWriteReceipt.actor_user_id == user_id,
            CanvasWriteReceipt.idempotency_key == key,
        )
        if lock:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def recycle_target(self, project_id: int, canvas_id: int, *, lock: bool = True):
        project_query = select(Project).where(Project.id == project_id)
        canvas_query = select(ProjectCanvas).where(
            ProjectCanvas.project_id == project_id, ProjectCanvas.id == canvas_id
        )
        if lock:
            project_query = project_query.with_for_update()
            canvas_query = canvas_query.with_for_update()
        project = self.session.scalar(project_query.execution_options(populate_existing=True))
        canvas = self.session.scalar(canvas_query.execution_options(populate_existing=True))
        return project, canvas

    def recycle_disposal(self, actor_id: int, canvas_id: int, archived_version: int, *, lock=False):
        query = (
            select(CanvasWriteReceipt)
            .where(
                CanvasWriteReceipt.actor_user_id == actor_id,
                CanvasWriteReceipt.canvas_id == canvas_id,
                CanvasWriteReceipt.operation_kind == "canvas.recycle.purge",
                CanvasWriteReceipt.expected_row_version == archived_version,
            )
            .limit(1)
        )
        return self.session.scalar(query.with_for_update() if lock else query)

    def recycle_receipts(self, actor_id: int):
        # The ORM receipt predicate checks the author and current membership,
        # including archived parents. Core aliases only filter archive generations.
        canvas = ProjectCanvas.__table__.alias("recycle_canvas")
        disposed = CanvasWriteReceipt.__table__.alias("recycle_disposed")
        query = (
            select(CanvasWriteReceipt)
            .join(
                canvas,
                (canvas.c.id == CanvasWriteReceipt.canvas_id)
                & (canvas.c.project_id == CanvasWriteReceipt.project_id),
            )
            .where(
                CanvasWriteReceipt.actor_user_id == actor_id,
                CanvasWriteReceipt.operation_kind == "canvas.archive",
                canvas.c.archived_at.is_not(None),
                canvas.c.row_version == CanvasWriteReceipt.committed_row_version,
                ~select(disposed.c.id)
                .where(
                    disposed.c.actor_user_id == actor_id,
                    disposed.c.canvas_id == canvas.c.id,
                    disposed.c.operation_kind == "canvas.recycle.purge",
                    disposed.c.expected_row_version == CanvasWriteReceipt.committed_row_version,
                )
                .exists(),
            )
            .order_by(CanvasWriteReceipt.created_at.desc(), CanvasWriteReceipt.id.desc())
            .limit(200)
        )
        return list(self.session.scalars(query))

    def children(self, model, canvas_id: int, *, active=False, lock=False):
        query = select(model).where(model.canvas_id == canvas_id)
        if active:
            query = query.where(model.archived_at.is_(None))
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return list(self.session.scalars(query))

    def canvases(self, project_id: int | None = None, *, lock: bool = False):
        query = select(ProjectCanvas).where(ProjectCanvas.archived_at.is_(None))
        if project_id is not None:
            query = query.where(ProjectCanvas.project_id == project_id)
        query = query.order_by(ProjectCanvas.position, ProjectCanvas.id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return list(self.session.scalars(query))

    def workspace_page(
        self, *, page: int, page_size: int, query: str, project_id: int | None, sort: str
    ) -> tuple[list[ProjectCanvas], int]:
        conditions = [
            ProjectCanvas.archived_at.is_(None),
            Project.archived_at.is_(None),
            Project.workspace_mode == "infinite_canvas",
        ]
        if project_id is not None:
            conditions.append(ProjectCanvas.project_id == project_id)
        if query:
            conditions.append(ProjectCanvas.title.contains(query, autoescape=True))
        total = (
            self.session.scalar(
                select(func.count(ProjectCanvas.id))
                .join(Project, Project.id == ProjectCanvas.project_id)
                .where(*conditions)
            )
            or 0
        )
        order = ProjectCanvas.created_at if sort == "created" else ProjectCanvas.updated_at
        rows = self.session.scalars(
            select(ProjectCanvas)
            .join(Project, Project.id == ProjectCanvas.project_id)
            .where(*conditions)
            .order_by(order.desc(), ProjectCanvas.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows), total

    def workspace_children(
        self, canvases: list[ProjectCanvas], models: tuple, *, lock: bool = False
    ) -> dict:
        grouped = {canvas.id: {model: [] for model in models} for canvas in canvases}
        if not grouped:
            return grouped
        for model in models:
            query = select(model).where(model.canvas_id.in_(grouped))
            if hasattr(model, "archived_at"):
                query = query.where(model.archived_at.is_(None))
            if lock:
                query = query.with_for_update().execution_options(populate_existing=True)
            for row in self.session.scalars(query):
                grouped[row.canvas_id][model].append(row)
        return grouped

    def summaries(self, project_ids: list[int]):
        if not project_ids:
            return {}
        counts = (
            select(ProjectCanvas.project_id, func.count(ProjectCanvas.id).label("canvas_count"))
            .where(ProjectCanvas.project_id.in_(project_ids), ProjectCanvas.archived_at.is_(None))
            .group_by(ProjectCanvas.project_id)
            .subquery()
        )
        rows = self.session.execute(
            select(
                ProjectCanvasSettings.project_id,
                ProjectCanvasSettings.primary_canvas_id,
                func.coalesce(counts.c.canvas_count, 0),
            )
            .outerjoin(counts, counts.c.project_id == ProjectCanvasSettings.project_id)
            .where(ProjectCanvasSettings.project_id.in_(project_ids))
        )
        return {
            project: {"primary_canvas_id": primary, "canvas_count": count}
            for project, primary, count in rows
        }
