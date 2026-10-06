"""保存前归一化：同项目保留原资源，跨范围副本按稳定身份恢复。"""

from short_drama.core.exceptions import NotFound
from short_drama.schemas.canvas_resource import (
    CanvasResourceCopyRequest,
    CanvasResourceNormalizeRequest,
)

from .canvas_resource_copy import CanvasResourceCopyService


class CanvasResourceNormalizationService(CanvasResourceCopyService):
    def normalize(self, payload: CanvasResourceNormalizeRequest) -> dict:
        identifiers = set(payload.resource_ids)
        with self._transaction(read_only=True):
            canvas = self._target(payload.canvas_key)
            direct = {
                identifier
                for identifier, resource in self.resources.resource_map(identifiers).items()
                if resource.project_id == canvas.project_id
            }
        mapping = {}
        for identifier in sorted(identifiers):
            if identifier in direct:
                mapping[identifier] = identifier
                continue
            # The per-file identity survives lost responses, changed document
            # batches and browser restarts. Never rotate it to bypass a tombstone.
            # copy() can replay an acknowledged independent file after its origin
            # becomes inaccessible, but always rechecks access to the target.
            copied = self.copy(
                CanvasResourceCopyRequest(
                    canvas_key=payload.canvas_key, source_resource_id=identifier
                ),
                f"canvas-normalize:v1:{payload.canvas_key}:{identifier}",
            )
            mapping[identifier] = copied.id
        with self._transaction(read_only=True):
            canvas = self._target(payload.canvas_key)
            targets = set(mapping.values())
            resources = self.resources.resource_map(targets)
            if set(resources) != targets or any(
                item.project_id != canvas.project_id for item in resources.values()
            ):
                raise NotFound("A normalized resource is no longer available in this canvas")
            aliases = self.resources.copy_ancestors(targets)
            return {
                "resource_map": {str(key): str(value) for key, value in mapping.items()},
                "resource_aliases": {
                    str(key): [str(value) for value in sorted(values)]
                    for key, values in aliases.items()
                },
            }

    def _target(self, key: str):
        canvas = self.resources.canvas(key)
        if canvas is None:
            raise NotFound("Canvas does not exist")
        return self.canvases.require_canvas(canvas.project_id, canvas.id)
