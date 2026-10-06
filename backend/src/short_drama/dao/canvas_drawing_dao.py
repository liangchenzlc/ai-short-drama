from sqlalchemy import and_, select, tuple_

from short_drama.domain import (
    CanvasDrawing,
    CanvasDrawingVersion,
    CanvasRevisionDrawingReference,
    ProjectCanvas,
)


class CanvasDrawingDAO:
    def __init__(self, session):
        self.session = session

    def canvas(self, key):
        return self.session.scalar(
            select(ProjectCanvas).where(
                ProjectCanvas.source_key == key, ProjectCanvas.archived_at.is_(None)
            )
        )

    def drawing(self, canvas_id, key, *, lock=False):
        query = select(CanvasDrawing).where(
            CanvasDrawing.canvas_id == canvas_id, CanvasDrawing.source_key == key
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def version(self, drawing_id, revision, *, lock=False):
        query = select(CanvasDrawingVersion).where(
            CanvasDrawingVersion.drawing_id == drawing_id,
            CanvasDrawingVersion.row_version == revision,
        )
        return self.session.scalar(query.with_for_update() if lock else query)

    def active(self, canvas_id):
        return self.session.execute(
            select(CanvasDrawing, CanvasDrawingVersion)
            .join(
                CanvasDrawingVersion,
                and_(
                    CanvasDrawingVersion.drawing_id == CanvasDrawing.id,
                    CanvasDrawingVersion.row_version == CanvasDrawing.row_version,
                ),
            )
            .where(CanvasDrawing.canvas_id == canvas_id, CanvasDrawing.archived_at.is_(None))
            .order_by(CanvasDrawing.updated_at.desc(), CanvasDrawing.id)
        ).all()

    def heads(self, canvas_id, *, lock=False):
        query = select(CanvasDrawing).where(CanvasDrawing.canvas_id == canvas_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return {
            row.source_key: {
                "revision": str(row.row_version),
                "deleted": row.archived_at is not None,
            }
            for row in self.session.scalars(query.order_by(CanvasDrawing.id))
        }

    def document_versions(self, canvas_id, versions, *, lock=False):
        query = (
            select(CanvasDrawing, CanvasDrawingVersion)
            .join(CanvasDrawingVersion, CanvasDrawingVersion.drawing_id == CanvasDrawing.id)
            .where(CanvasDrawing.canvas_id == canvas_id)
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        pairs = list(versions.items())
        rows = []
        for start in range(0, len(pairs), 400):
            rows.extend(
                self.session.execute(
                    query.where(
                        tuple_(CanvasDrawing.source_key, CanvasDrawingVersion.row_version).in_(
                            pairs[start : start + 400]
                        )
                    )
                ).all()
            )
        return {drawing.source_key: (drawing, version) for drawing, version in rows}

    def revision_bindings(self, canvas_id, revision_id):
        return list(
            self.session.scalars(
                select(CanvasRevisionDrawingReference).where(
                    CanvasRevisionDrawingReference.canvas_id == canvas_id,
                    CanvasRevisionDrawingReference.revision_id == revision_id,
                )
            )
        )
