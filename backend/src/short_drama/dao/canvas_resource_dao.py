from sqlalchemy import func, select
from sqlalchemy.orm import Session

from short_drama.db.canvas_resource_scope import copy_ancestors
from short_drama.domain import (
    CanvasBinaryResource,
    CanvasResourceChunk,
    CanvasResourceCopySource,
    CanvasResourceUpload,
    MediaFile,
    ProjectCanvas,
)


class CanvasResourceDAO:
    def __init__(self, session: Session):
        self.session = session

    def canvas(self, source_key: str):
        return self.session.scalar(
            select(ProjectCanvas).where(
                ProjectCanvas.source_key == source_key, ProjectCanvas.archived_at.is_(None)
            )
        )

    def by_key(self, user_id: int, key: str, *, lock=True):
        query = select(CanvasResourceUpload).where(
            CanvasResourceUpload.user_id == user_id,
            CanvasResourceUpload.idempotency_hash == key,
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def copy_source(self, upload_id: int, *, lock=True):
        query = select(CanvasResourceCopySource).where(
            CanvasResourceCopySource.upload_id == upload_id
        )
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def upload(self, identifier: int, *, lock=True):
        query = select(CanvasResourceUpload).where(CanvasResourceUpload.id == identifier)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(query)

    def chunks(self, identifier: int):
        return list(
            self.session.scalars(
                select(CanvasResourceChunk)
                .where(CanvasResourceChunk.upload_id == identifier)
                .order_by(CanvasResourceChunk.chunk_index)
            )
        )

    def pending_count(self, user_id: int, now):
        return (
            self.session.scalar(
                select(func.count(CanvasResourceUpload.id)).where(
                    CanvasResourceUpload.user_id == user_id,
                    CanvasResourceUpload.status == "pending",
                    CanvasResourceUpload.expires_at > now,
                )
            )
            or 0
        )

    def resource(self, identifier: int, *, lock=False):
        for model in (MediaFile, CanvasBinaryResource):
            query = select(model).where(model.id == identifier)
            if lock:
                query = query.with_for_update().execution_options(populate_existing=True)
            value = self.session.scalar(query)
            if value is not None:
                return value
        return None

    def resource_map(self, identifiers: set[int], *, lock=False):
        result = {}
        if identifiers:
            for model in (MediaFile, CanvasBinaryResource):
                query = select(model).where(model.id.in_(identifiers))
                if lock:
                    query = query.with_for_update()
                result.update({row.id: row for row in self.session.scalars(query)})
        return result

    def copy_ancestors(self, identifiers: set[int], *, lock=False) -> dict[int, set[int]]:
        return copy_ancestors(self.session, identifiers, lock=lock)
