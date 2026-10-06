"""按冻结引用的原始索引加载可信存储媒体，保留与 HTTPS 引用混排顺序。"""

import re

from short_drama.ai import GenerationError

from .generation_references import StoredImageReferences


class StoredCanvasVideoReferences:
    def __init__(self, factory, storage, settings, request: dict) -> None:
        self.loaders = {}
        self.positions = {}
        inputs = request["canvas_request"]["input"]
        for kind, group in (
            ("image", "referenceImages"),
            ("video", "referenceVideos"),
            ("audio", "referenceAudios"),
        ):
            identifiers, positions = [], {}
            for index, reference in enumerate(inputs.get(group, [])):
                match = re.fullmatch(r"resource:([1-9][0-9]*)", reference.get("storageKey") or "")
                if match is not None:
                    positions[index] = len(identifiers)
                    identifiers.append(match.group(1))
            if identifiers:
                self.loaders[kind] = StoredImageReferences(factory, storage, settings, identifiers)
                self.positions[kind] = positions

    def __call__(self, kind: str, index: int, max_bytes: int, deadline: float) -> bytes:
        position = self.positions.get(kind, {}).get(index)
        loader = self.loaders.get(kind)
        if position is None or loader is None:
            raise GenerationError("reference_missing")
        return loader(position, max_bytes, deadline)
