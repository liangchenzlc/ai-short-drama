"""The entity boundary: verified managed objects become request-local content.

Processed images are frozen back to MinIO before sending. The durable request
stores their references, so replay never depends on mutable transforms or URLs.
"""

import base64
import hashlib
import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from short_drama.agent.context.media import materialize_prompt
from short_drama.agent.resources.contracts import HydrationIntent, ResourceRef
from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.utils.json_digest import digest

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 20 * 1024**2
MAX_HYDRATED_BYTES = 4 * 1024**2


@dataclass(frozen=True, repr=False)
class HydratedResource:
    reference: ResourceRef
    content: str
    mime_type: str
    injection: str
    persistence: str


def attachment_ref(attachment: dict, *, source: str = "task_attachment") -> ResourceRef:
    return ResourceRef(
        kind=attachment["kind"],
        ref_id=attachment["id"],
        uri=attachment.get("storage_locator") or f"attachment://{attachment['id']}",
        source=source,
        checksum=attachment.get("checksum_sha256"),
        metadata={
            key: attachment[key]
            for key in ("name", "media_id", "mime_type", "byte_size")
            if key in attachment
        },
    )


def skill_resource_ref(manifest: dict, ref_id: str) -> ResourceRef:
    return ResourceRef(
        kind=manifest["kind"] if manifest["kind"] in {"image", "script"} else "text",
        ref_id=ref_id,
        uri=f"minio://{manifest['bucket']}/{manifest['object_name']}",
        source="skill_resource",
        checksum=manifest["checksum_sha256"],
        metadata={"path": manifest["path"], "byte_size": manifest["byte_size"]},
    )


class ResourceResolver:
    def __init__(self, storage, settings=None):
        self.storage = storage
        self.settings = settings

    def _bytes(self, reference: ResourceRef, limit: int) -> bytes:
        if reference.availability != "available" or not reference.checksum:
            raise WorkflowError("agent_resource_unavailable", "资源不可用或缺少校验值", 409)
        try:
            with self.storage.open(reference.uri) as stream:
                data = stream.read(limit + 1)
        except (OSError, BusinessError) as error:
            logger.warning(
                "agent_resource_read_failed",
                extra={"resource_id": reference.ref_id, "error_type": type(error).__name__},
            )
            raise WorkflowError("agent_resource_unavailable", "受管资源暂时无法读取", 422) from None
        if not data or len(data) > limit or hashlib.sha256(data).hexdigest() != reference.checksum:
            raise WorkflowError("agent_resource_integrity", "受管资源的内容校验失败", 409)
        return data

    def _freeze_bytes(self, intent, data, *, kind, mime, metadata, index=0):
        stored = self.storage.freeze_agent_resource(
            data, invocation_id=intent.invocation_id, content_type=mime
        )
        reference = ResourceRef(
            kind=kind,
            ref_id="view:"
            + digest(
                [intent.resource.ref_id, intent.invocation_id, intent.detail, intent.crop, index]
            ),
            uri=stored.storage_locator,
            source=f"hydrated_{kind}",
            version="media.1",
            checksum=hashlib.sha256(data).hexdigest(),
            metadata={
                **metadata,
                "mime_type": mime,
                "byte_size": len(data),
                "original_ref": intent.resource.model_dump(mode="json"),
                "detail": intent.detail,
                "crop": list(intent.crop) if intent.crop else None,
            },
        )
        return intent.model_copy(update={"resource": reference})

    def freeze(self, intent: HydrationIntent) -> list[HydrationIntent]:
        if intent.resource.source in {"hydrated_image", "hydrated_audio"}:
            return [intent]
        if intent.resource.kind == "image":
            return [self.freeze_image(intent)]
        if intent.resource.kind not in {"audio", "video"}:
            raise WorkflowError("agent_resource_kind", "此资源不能作为媒体水合", 422)
        parts = materialize_prompt(
            {
                "codec": "agent.attachments",
                "content": "",
                "video_audio": intent.video_audio,
                "attachments": [
                    {
                        "id": intent.resource.ref_id,
                        "kind": intent.resource.kind,
                        "name": intent.resource.metadata.get("name", intent.resource.ref_id),
                        "storage_locator": intent.resource.uri,
                        "checksum_sha256": intent.resource.checksum,
                    }
                ],
            },
            self.settings,
            self.storage,
        )
        frozen, captions = [], []
        for part in parts:
            if isinstance(part, str):
                captions.append(part)
                continue
            mime = part["media_type"]
            frozen.append(
                self._freeze_bytes(
                    intent,
                    base64.urlsafe_b64decode(part["data"]),
                    kind="image" if mime.startswith("image/") else "audio",
                    mime=mime,
                    metadata={"caption": "\n".join(captions)},
                    index=len(frozen),
                )
            )
            captions = []
        if captions and frozen:
            reference = frozen[-1].resource
            reference = reference.model_copy(
                update={
                    "metadata": {
                        **reference.metadata,
                        "caption": reference.metadata.get("caption", "")
                        + "\n"
                        + "\n".join(captions),
                    }
                }
            )
            frozen[-1] = frozen[-1].model_copy(update={"resource": reference})
        return frozen

    def freeze_image(self, intent: HydrationIntent) -> HydrationIntent:
        if intent.resource.kind != "image":
            raise WorkflowError("agent_resource_kind", "此水合策略只处理图片", 422)
        data = self._bytes(intent.resource, MAX_IMAGE_BYTES)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > 40_000_000:
                    raise WorkflowError("agent_resource_too_large", "图片像素数超过 4000 万", 422)
                image = ImageOps.exif_transpose(image).convert("RGB")
                if intent.crop:
                    if intent.crop[2] > image.width or intent.crop[3] > image.height:
                        raise WorkflowError(
                            "agent_resource_crop",
                            "裁剪范围超过图片边界",
                            422,
                            details={"width": image.width, "height": image.height},
                        )
                    image = image.crop(intent.crop)
                if intent.detail != "original":
                    edge = 1280 if intent.detail == "low" else 2560
                    image.thumbnail((edge, edge))
                encoded = io.BytesIO()
                image.save(encoded, "JPEG", quality=85)
                data = encoded.getvalue()
                dimensions = {"width": image.width, "height": image.height}
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            logger.warning(
                "agent_resource_decode_failed",
                extra={"resource_id": intent.resource.ref_id, "error_type": type(error).__name__},
            )
            raise WorkflowError("agent_resource_decode", "无法解码受管图片", 422) from None
        if len(data) > MAX_HYDRATED_BYTES:
            raise WorkflowError("agent_resource_too_large", "处理后的图片超过 4 MiB", 422)
        return self._freeze_bytes(
            intent, data, kind="image", mime="image/jpeg", metadata=dimensions
        )

    def hydrate(
        self, intent: HydrationIntent, *, protocol: str, skill_body: str | None = None
    ) -> HydratedResource:
        reference = intent.resource
        if reference.kind == "skill":
            if skill_body is None or hashlib.sha256(
                skill_body.encode()
            ).hexdigest() != reference.metadata.get("body_checksum"):
                raise WorkflowError("agent_resource_integrity", "方法正文校验失败", 409)
            return HydratedResource(
                reference, skill_body, "text/markdown", "tool_result", "deduplicate"
            )
        if reference.source not in {"hydrated_image", "hydrated_audio"}:
            raise WorkflowError("agent_resource_unprepared", "媒体必须先冻结处理结果", 409)
        if reference.kind == "audio" and protocol != "openai_chat.v1":
            raise WorkflowError("agent_resource_modality", "此协议不支持音频理解", 422)
        data = self._bytes(reference, MAX_HYDRATED_BYTES)
        logger.info(
            "agent_resource_hydrated",
            extra={
                "resource_id": reference.ref_id,
                "invocation_id": intent.invocation_id,
                "byte_size": len(data),
                "estimated_tokens": 2048 if intent.detail == "low" else 4096,
            },
        )
        return HydratedResource(
            reference,
            base64.b64encode(data).decode("ascii"),
            reference.metadata["mime_type"],
            "tool_result"
            if protocol in {"claude_messages.v1", "openai_responses.v1"}
            else "user_message",
            "ephemeral",
        )


def image_block(resource: HydratedResource, protocol: str) -> dict:
    if resource.reference.kind == "audio":
        if protocol != "openai_chat.v1":
            raise WorkflowError("agent_resource_modality", "此协议不支持音频理解", 422)
        return {"type": "input_audio", "input_audio": {"data": resource.content, "format": "mp3"}}
    if protocol == "claude_messages.v1":
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": resource.mime_type,
                "data": resource.content,
            },
        }
    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{resource.mime_type};base64,{resource.content}",
            "detail": "low" if resource.reference.metadata.get("detail") == "low" else "high",
        },
    }


def request_template(body: object, resources: list[HydratedResource], tool_results=None) -> object:
    """Replace only known inline entities. Unknown media cannot enter the snapshot."""
    by_data, consumed = {}, {}
    for item in resources:
        by_data.setdefault(item.content, []).append(item.reference.model_dump(mode="json"))

    def reference(data):
        if data not in by_data:
            raise WorkflowError("agent_media_entity_leak", "请求包含未登记媒体实体", 500)
        index = consumed.get(data, 0)
        consumed[data] = index + 1
        refs = by_data[data]
        selected = refs[min(index, len(refs) - 1)]
        return {"audio_ref" if selected["kind"] == "audio" else "image_ref": selected}

    def replace(value):
        if isinstance(value, str):
            if tool_results and value in tool_results:
                return {"operation_ref": tool_results[value]}
            if value.startswith(("data:image/", "data:audio/")) and ";base64," in value:
                data = value.split(";base64,", 1)[1]
                return reference(data)
            return value
        if isinstance(value, dict):
            if value.get("type") == "base64" and isinstance(value.get("data"), str):
                return reference(value["data"])
            if value.get("type") == "input_audio":
                return reference(value["input_audio"]["data"])
            return {key: replace(child) for key, child in value.items()}
        if isinstance(value, list):
            return [replace(child) for child in value]
        return value

    return replace(body)
