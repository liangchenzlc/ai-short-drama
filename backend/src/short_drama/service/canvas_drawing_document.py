"""精确笔画编码在绘图保存与整图历史之间共用。"""

import json
from copy import deepcopy

from .canvas_document import media_references, remap_resource_references, resource_identifier


def drawing_resource_ids(document: dict) -> set[int]:
    identifiers = {identifier for _, identifier in media_references(document)}
    if document.get("previewResourceId"):
        identifiers.add(int(document["previewResourceId"]))
    snapshot = document.get("snapshot")
    if isinstance(snapshot, dict) and isinstance(snapshot.get("files"), dict):
        for file in snapshot["files"].values():
            if isinstance(file, dict) and (identifier := resource_identifier(file.get("dataURL"))):
                identifiers.add(identifier)
    return identifiers


def remap_drawing_resources(document: dict, mapping: dict[int, int]) -> dict:
    result = remap_resource_references(document, mapping)
    preview = document.get("previewResourceId")
    if preview:
        result["previewResourceId"] = str(mapping.get(int(preview), int(preview)))
    snapshot = document.get("snapshot")
    if isinstance(snapshot, dict) and isinstance(snapshot.get("files"), dict):
        for key, file in snapshot["files"].items():
            if isinstance(file, dict) and resource_identifier(file.get("dataURL")):
                result["snapshot"]["files"][key]["dataURL"] = remap_resource_references(
                    {"url": file["dataURL"]}, mapping
                )["url"]
    return result


def drawing_document(version):
    document = deepcopy(version.document_json)
    if document.pop("$snapshot_encoding", None) == "json":
        document["snapshot"] = json.loads(document["snapshot"])
    return document


def stored_drawing_document(document):
    # MySQL JSON double serialization can change a coordinate by one ULP.
    # Keep one exact snapshot encoding, not a second independently editable copy.
    return {
        **document,
        "$snapshot_encoding": "json",
        "snapshot": json.dumps(
            document["snapshot"], ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ),
    }
