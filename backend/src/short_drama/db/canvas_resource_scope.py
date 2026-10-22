"""Visible copy ancestry used by resource reads and cross-scope reference guards."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.domain import CanvasResourceCopySource, CanvasResourceUpload


def copy_ancestors(session: Session, identifiers: set[int], *, lock=False) -> dict[int, set[int]]:
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
            query = (
                select(CanvasResourceCopySource, CanvasResourceUpload.reserved_resource_id)
                .join(
                    CanvasResourceUpload,
                    CanvasResourceUpload.id == CanvasResourceCopySource.upload_id,
                )
                .where(
                    CanvasResourceUpload.reserved_resource_id.in_(frontier[offset : offset + 200]),
                    CanvasResourceUpload.mode == "copy",
                    CanvasResourceUpload.status == "ready",
                )
            )
            if lock:
                query = query.with_for_update(read=True)
            rows = session.execute(query)
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
