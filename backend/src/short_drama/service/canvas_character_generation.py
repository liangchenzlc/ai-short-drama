"""将角色引用冻结到本人任务；不信任前端声称的角色或声音身份。"""

from copy import deepcopy

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import ProjectCanvas
from short_drama.schemas.canvas_task_runtime import CanvasTaskMetadata, CanvasTaskReference

from .canvas_character_catalog_service import CanvasCharacterCatalogService
from .canvas_character_publication_service import CanvasCharacterPublicationService
from .canvas_service import CanvasService


def freeze_canvas_characters(
    canvases: CanvasService,
    canvas: ProjectCanvas,
    document: dict,
    metadata: CanvasTaskMetadata,
    *,
    settings=None,
    audio_references: list[CanvasTaskReference] | None = None,
) -> list[dict]:
    claimed = metadata.resolved_character_versions or []
    voice_claims = metadata.resolved_character_voices or []
    if voice_claims and not claimed:
        raise WorkflowError("canvas_character_reference_changed", "声音引用缺少已保存角色版本", 409)
    if not claimed:
        return []
    CanvasCharacterPublicationService(canvases).validate(canvas, document)
    nodes = [
        node["metadata"]
        for node in document["nodes"]
        if (node.get("metadata") or {}).get("workflowKind") == "character"
        and (node.get("metadata") or {}).get("characterAssetId")
    ]
    catalog = CanvasCharacterCatalogService(canvases.session, settings=settings)
    voice_pairs = {}
    for voice in voice_claims:
        pair = (voice.asset_id, voice.version_id)
        if pair in voice_pairs:
            raise WorkflowError("canvas_character_reference_invalid", "角色声音引用身份重复", 422)
        voice_pairs[pair] = voice
    result = []
    pairs = set()
    for reference in claimed:
        pair = (reference.asset_id, reference.version_id)
        if pair in pairs:
            raise WorkflowError("canvas_character_reference_invalid", "角色生成引用身份重复", 422)
        pairs.add(pair)
        matched = [
            node
            for node in nodes
            if node["characterAssetId"] == str(reference.asset_id)
            and node["characterVersionId"] == str(reference.version_id)
        ]
        if not matched:
            raise WorkflowError(
                "canvas_character_reference_changed", "角色生成引用未保存或版本已变化", 409
            )
        relation = catalog.catalog.relation(canvas.source_project_id, reference.asset_id)
        asset = catalog.catalog.locked_asset(reference.asset_id)
        item = catalog._item(relation, asset, reference.version_id)
        work = {
            "asset_id": str(reference.asset_id),
            "version_id": str(reference.version_id),
            "name": item.name,
            "definition": deepcopy(item.definition),
            "representations": [slot.model_dump(mode="json") for slot in item.representations],
        }
        voice = voice_pairs.pop(pair, None)
        if voice is not None:
            sample = item.voice_sample
            if (
                sample is None
                or voice.voice_key
                or (
                    voice.binding_version != sample.binding_version
                    or voice.sample_resource_id != sample.media_id
                )
            ):
                raise WorkflowError(
                    "canvas_character_voice_changed", "角色已采用样音已变化或不支持该音色身份", 409
                )
            from short_drama.dao.canvas_resource_dao import CanvasResourceDAO

            resources = CanvasResourceDAO(canvases.session)
            identifiers = {
                int(ref.storage_key.removeprefix("resource:"))
                for ref in audio_references or []
                if ref.storage_key
                and ref.storage_key.startswith("resource:")
                and ref.storage_key.removeprefix("resource:").isascii()
                and ref.storage_key.removeprefix("resource:").isdecimal()
            }
            ancestors = resources.copy_ancestors(identifiers, lock=True)
            candidates = [
                identifier
                for identifier in sorted(identifiers)
                if identifier == sample.media_id
                or sample.media_id in ancestors.get(identifier, set())
            ]
            if not candidates:
                raise WorkflowError(
                    "canvas_character_voice_changed", "角色样音尚未加入实际参考音频", 409
                )
            selected = candidates[0]
            media = catalog.catalog.media(selected)
            if (
                media is None
                or media.checksum_sha256 != sample.checksum_sha256
                or not media.format_code.startswith("audio/")
            ):
                raise WorkflowError(
                    "canvas_character_voice_changed", "角色样音副本与采用作品不一致", 409
                )
            work["voice_sample"] = {
                **sample.model_dump(mode="json", exclude={"resource"}),
                "reference_media_id": str(selected),
            }
        result.append(work)
    if voice_pairs:
        raise WorkflowError("canvas_character_reference_changed", "声音引用不属于本次角色版本", 409)
    return result
