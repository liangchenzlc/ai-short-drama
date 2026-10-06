"""首次发布画布时可携带完整绘图子文档。"""

import json

from pydantic import Field, model_validator

from .canvas import CanvasCreateRequest
from .canvas_drawing import CanvasDrawingWrite


class CanvasCreationRequest(CanvasCreateRequest):
    drawing_documents: list[CanvasDrawingWrite] = Field(default_factory=list, max_length=20000)

    @model_validator(mode="after")
    def validate_drawings(self):
        drawings = {drawing.drawing_id: drawing for drawing in self.drawing_documents}
        if len(drawings) != len(self.drawing_documents):
            raise ValueError("duplicate initial drawing keys")
        nodes = (
            [node for node in self.source_document.nodes if node.type == "drawing"]
            if self.source_document
            else []
        )
        if any(not isinstance(node.metadata.get("drawingId"), str) for node in nodes):
            raise ValueError("drawing node requires a string drawing key")
        referenced = {node.metadata["drawingId"] for node in nodes}
        if set(drawings) - referenced:
            raise ValueError("initial drawing is not referenced by the canvas")
        for drawing in drawings.values():
            if drawing.revision != 0:
                raise ValueError("initial drawing revision must be zero")
        for node in nodes:
            identifier = node.metadata.get("drawingId")
            version = node.metadata.get("drawingRevision")
            if identifier in drawings:
                if version != "1":
                    raise ValueError("initial drawing node must reference version one")
            elif not (version is None or version == "0" or version == 0) or node.metadata.get(
                "drawingShapeCount"
            ):
                raise ValueError("canvas creation is missing a drawing document")
        if (
            drawings
            and len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode())
            > 32 * 1024**2
        ):
            raise ValueError("complete canvas creation exceeds 32 MiB")
        return self


def canvas_creation_payload(request: CanvasCreateRequest) -> dict:
    payload = request.model_dump(mode="json")
    # Existing immutable requests and receipts predate the optional drawing list.
    # Absence and an empty list retain exactly their original digest input.
    if not payload.get("drawing_documents"):
        payload.pop("drawing_documents", None)
    return payload
