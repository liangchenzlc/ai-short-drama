from sqlalchemy import func, select
from sqlalchemy.orm import Session

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

    def resource_map(self, identifiers: set[int]):
        result = {}
        if identifiers:
            for model in (MediaFile, CanvasBinaryResource):
                result.update(
                    {
                        row.id: row
                        for row in self.session.scalars(
                            select(model).where(model.id.in_(identifiers))
                        )
                    }
                )
        return result

    def copy_ancestors(self, identifiers: set[int]) -> dict[int, set[int]]:
        """Resolve only this actor's visible, completed copy provenance in batches."""
        parents: dict[int, set[int]] = {}
        pending, seen = set(identifiers), set()
        while pending:
            frontier = sorted(pending - seen)
            if not frontier:
                break
            seen.update(frontier)
            pending = set()
            for offset in range(0, len(frontier), 200):
                rows = self.session.execute(
                    select(CanvasResourceCopySource, CanvasResourceUpload.reserved_resource_id)
                    .join(
                        CanvasResourceUpload,
                        CanvasResourceUpload.id == CanvasResourceCopySource.upload_id,
                    )
                    .where(
                        CanvasResourceUpload.reserved_resource_id.in_(
                            frontier[offset : offset + 200]
                        ),
                        CanvasResourceUpload.mode == "copy",
                        CanvasResourceUpload.status == "ready",
                    )
                )
                for source, target in rows:
                    parents[target] = {source.original_resource_id}
                    frozen = source.snapshot_json.get("resource_ancestors")
                    if frozen is None:
                        # Earlier copies have only one-hop provenance. New copies
                        # freeze the full visible lineage before any source is released.
                        pending.add(source.original_resource_id)
                    else:
                        from short_drama.schemas.base import parse_identifier

                        parents[target].update(parse_identifier(value) for value in frozen)
        result: dict[int, set[int]] = {}
        for identifier in identifiers:
            ancestors, frontier = set(), set(parents.get(identifier, set()))
            while frontier:
                current = frontier.pop()
                if current == identifier or current in ancestors:
                    continue
                ancestors.add(current)
                frontier.update(parents.get(current, set()) - ancestors)
            if ancestors:
                result[identifier] = ancestors
        return result
