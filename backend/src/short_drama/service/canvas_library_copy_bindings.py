"""Keep one private source asset while its project copies retain independent bytes."""

from short_drama.core.exceptions import WorkflowError
from short_drama.dao.canvas_library_dao import CanvasLibraryDAO
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.domain import CanvasBinaryResource, CanvasLibraryAssetReference
from short_drama.utils.snowflake import next_id

from .canvas_document import media_references
from .canvas_library_references import canvas_media_bindings


def bind_canvas_library_copies(session, document: dict) -> None:
    """Called under the actor's library mutex and the canvas transaction."""
    bindings = {(key, identifier) for key, identifier in canvas_media_bindings(document) if key}
    resources = CanvasResourceDAO(session)
    ancestors = resources.copy_ancestors({identifier for _, identifier in bindings})
    if not ancestors:
        return
    library = CanvasLibraryDAO(session)
    for key, identifier in sorted(bindings):
        if identifier not in ancestors:
            continue
        asset = library.asset(key, lock=True)
        # A source snapshot may carry a retired or unloaded private asset key.
        # The source repair flow owns creating its replacement; never borrow a member's asset.
        if asset is None:
            continue
        declared = {value for _, value in media_references(asset.payload_json)}
        if identifier not in declared and not ancestors[identifier].intersection(declared):
            raise WorkflowError(
                "canvas_asset_resource_conflict", "画布副本与原素材不一致，请重新载入素材", 409
            )
        previous = {ref.media_id or ref.binary_id for ref in library.references(asset.id)}
        if identifier in previous:
            continue
        resource = resources.resource(identifier)
        binary = isinstance(resource, CanvasBinaryResource)
        session.add(
            CanvasLibraryAssetReference(
                id=next_id(),
                library_asset_id=asset.id,
                media_id=None if binary else identifier,
                binary_id=identifier if binary else None,
            )
        )
        session.flush()
