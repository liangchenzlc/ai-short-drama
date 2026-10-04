"""Private managed inputs; adding context never publishes a personal file."""

import hashlib
import io
import json
import logging
import subprocess
from pathlib import PurePath

from sqlalchemy import func, select

from short_drama.agent.input_capabilities import input_capabilities
from short_drama.agent.input_media import MAX_INPUT_BYTES, inspect_attachment
from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.domain import Asset, MediaFile
from short_drama.domain.agent_context import AgentAttachment
from short_drama.schemas.agent_context import AttachmentRead, AttachmentReference
from short_drama.schemas.base import parse_identifier
from short_drama.service.agent_conversation_service import AgentConversationService
from short_drama.service.base import Page, utcnow
from short_drama.service.storage_service import StorageService
from short_drama.storage.minio import MinioStorage
from short_drama.utils.snowflake import next_id

logger = logging.getLogger(__name__)


def attachment_key(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or not all(
            character.isascii() and (character.isalnum() or character in "_-:")
            for character in value
        )
    ):
        raise WorkflowError("invalid_idempotency_key", "附件需要有效的幂等键", 422)
    return hashlib.sha256(value.encode()).hexdigest()


def attachment_read(row, storage, media=None):
    return AttachmentRead(
        id=row.id,
        kind=row.kind,
        name=row.name,
        mime_type=row.mime_type,
        byte_size=row.byte_size,
        media_id=row.media_id,
        url=storage.download_url(media.storage_locator) if media else None,
        text_preview=(row.text_content or "")[:500] or None,
        metadata=row.input_metadata,
        checksum_sha256=row.checksum_sha256,
        pending=row.attached_message_id is None,
    )


def freeze_attachments(session, conversation, identifiers, snapshot, video_audio):
    capability = input_capabilities(snapshot)
    references, frozen, rows = [], [], []
    for identifier in identifiers:
        row = session.scalar(
            select(AgentAttachment)
            .where(
                AgentAttachment.id == identifier,
                AgentAttachment.conversation_id == conversation.id,
                AgentAttachment.owner_user_id == conversation.owner_user_id,
                AgentAttachment.deleted_at.is_(None),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise NotFound("Attachment does not exist")
        if row.kind in {"image", "video"} and not capability["image"]:
            raise WorkflowError(
                "agent_image_input_unsupported", "所选模型不支持图片或视频画面", 422
            )
        needs_audio = row.kind == "audio" or (
            row.kind == "video"
            and row.input_metadata.get("has_audio", True)
            and video_audio == "include"
        )
        if needs_audio and not capability["audio"]:
            raise WorkflowError(
                "agent_audio_input_unsupported",
                "所选模型不支持音频理解；视频可明确选择仅分析画面，音频附件需更换模型",
                422,
            )
        media = session.get(MediaFile, row.media_id) if row.media_id else None
        if row.media_id and media is None:
            raise NotFound("Attachment media is no longer accessible")
        frozen.append(
            {
                "id": str(row.id),
                "kind": row.kind,
                "name": row.name,
                "text_content": row.text_content,
                "storage_locator": media.storage_locator if media else None,
                "checksum_sha256": row.checksum_sha256,
            }
        )
        references.append(
            {
                "type": "attachment",
                "id": str(row.id),
                "kind": row.kind,
                "name": row.name,
                "mime_type": row.mime_type,
                "media_id": str(row.media_id) if row.media_id else None,
                "text_preview": (row.text_content or "")[:500] or None,
                "metadata": row.input_metadata,
                "checksum_sha256": row.checksum_sha256,
            }
        )
        rows.append(row)
    if sum(len((item["text_content"] or "").encode("utf-8")) for item in frozen) > 131072:
        raise WorkflowError("agent_context_too_large", "文本资料合计不能超过 128 KiB", 422)
    return references, frozen, rows


class AgentAttachmentService(AgentConversationService):
    def __init__(self, session, settings, storage=None):
        super().__init__(session, settings)
        self._storage = storage

    @property
    def storage(self):
        if not isinstance(self._storage, StorageService):
            self._storage = StorageService(
                self._storage or MinioStorage(self.settings), self.settings
            )
        return self._storage

    def _read(self, row):
        media = self.session.get(MediaFile, row.media_id) if row.media_id else None
        return attachment_read(row, self.storage if media else None, media)

    def list(self, conversation_id, offset=0, limit=50, pending=None):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            conversation = self._conversation(conversation_id)
            conditions = [
                AgentAttachment.conversation_id == conversation.id,
                AgentAttachment.owner_user_id == conversation.owner_user_id,
                AgentAttachment.deleted_at.is_(None),
            ]
            if pending is not None:
                conditions.append(
                    AgentAttachment.attached_message_id.is_(None)
                    if pending
                    else AgentAttachment.attached_message_id.is_not(None)
                )
            rows = self.session.scalars(
                select(AgentAttachment)
                .where(*conditions)
                .order_by(AgentAttachment.id.desc())
                .offset(offset)
                .limit(limit)
            ).all()
            total = self.session.scalar(select(func.count(AgentAttachment.id)).where(*conditions))
            return Page(
                items=[self._read(row) for row in rows], total=total, offset=offset, limit=limit
            )

    def _existing(self, conversation, key, request_hash):
        row = self.session.scalar(
            select(AgentAttachment)
            .where(
                AgentAttachment.conversation_id == conversation.id,
                AgentAttachment.create_key == key,
            )
            .with_for_update()
        )
        if row:
            if row.create_hash != request_hash or row.deleted_at:
                raise Conflict("附件幂等键已经用于其他内容或已移除的附件")
            return row
        if conversation.status != "active":
            raise WorkflowError("agent_conversation_archived", "请先恢复这段对话", 409)
        return None

    def _cleanup_uncommitted(self, stored):
        # Retain the object when a failed database receipt cannot be resolved.
        try:
            with self._transaction(read_only=True):
                durable = self.session.scalar(
                    select(MediaFile.id).where(MediaFile.storage_locator == stored.storage_locator)
                )
        except Exception as error:
            logger.warning(
                "Agent attachment upload retained after uncertain database receipt: %s",
                type(error).__name__,
            )
            return
        if durable:
            return
        try:
            self.storage.delete(stored.storage_locator, version_id=stored.version_id)
        except Exception as error:
            logger.warning("Agent attachment orphan cleanup failed: %s", type(error).__name__)

    def upload(self, conversation_id, stream, filename, idempotency_key):
        key = attachment_key(idempotency_key)
        with self._transaction(read_only=True):
            self._conversation(conversation_id)
        data = stream.read(MAX_INPUT_BYTES["video"] + 1)
        name = PurePath(filename.replace("\\", "/")).name[:255]
        try:
            kind, mime, content, metadata = inspect_attachment(data, name, self.settings)
        except (ValueError, RuntimeError, UnicodeDecodeError, subprocess.TimeoutExpired) as error:
            raise WorkflowError("invalid_agent_attachment", f"无法添加附件：{error}", 422) from None
        checksum = hashlib.sha256(data).hexdigest()
        request_hash = hashlib.sha256(f"{name}\n{checksum}".encode()).hexdigest()
        with self._transaction():
            conversation = self._conversation(conversation_id, lock=True)
            existing = self._existing(conversation, key, request_hash)
            if existing:
                return self._read(existing)
        stored = (
            self.storage.upload(io.BytesIO(data), length=len(data), content_type=mime)
            if kind != "text"
            else None
        )
        try:
            with self._transaction():
                conversation = self._conversation(conversation_id, lock=True)
                existing = self._existing(conversation, key, request_hash)
                if existing:
                    result = self._read(existing)
                else:
                    media = None
                    now = utcnow()
                    if stored:
                        media = MediaFile(
                            id=next_id(),
                            scope_user_id=conversation.owner_user_id,
                            project_id=None,
                            format_code=mime,
                            storage_locator=stored.storage_locator,
                            original_name=name,
                            byte_size=len(data),
                            checksum_sha256=checksum,
                            width=metadata.get("width"),
                            height=metadata.get("height"),
                            duration_ms=metadata.get("duration_ms"),
                            video_metadata=metadata if kind == "video" else None,
                            created_at=now,
                            updated_at=now,
                        )
                        self.session.add(media)
                        self.session.flush()
                    row = AgentAttachment(
                        id=next_id(),
                        owner_user_id=conversation.owner_user_id,
                        conversation_id=conversation.id,
                        kind=kind,
                        name=name,
                        mime_type=mime,
                        media_id=media.id if media else None,
                        text_content=content,
                        checksum_sha256=checksum,
                        byte_size=len(data),
                        input_metadata=metadata,
                        create_key=key,
                        create_hash=request_hash,
                        created_at=now,
                    )
                    self.session.add(row)
                    self.session.flush()
                    result = self._read(row)
            if existing and stored:
                self.storage.delete(stored.storage_locator, version_id=stored.version_id)
            return result
        except Exception:
            if stored:
                self._cleanup_uncommitted(stored)
            raise

    def reference(self, conversation_id, payload, idempotency_key):
        values = AttachmentReference.model_validate(payload)
        key = attachment_key(idempotency_key)
        request_hash = hashlib.sha256(values.model_dump_json().encode()).hexdigest()
        with self._transaction():
            conversation = self._conversation(conversation_id, lock=True)
            existing = self._existing(conversation, key, request_hash)
            if existing:
                return self._read(existing)
            text_content = None
            metadata = {"source_type": values.source_type, "source_id": str(values.source_id)}
            if values.source_type == "asset":
                asset = self.session.get(Asset, values.source_id)
                if asset is None or (
                    asset.project_id and asset.project_id != conversation.project_id
                ):
                    raise NotFound("Asset does not exist in the current scope")
                name = asset.name
                text_content = json.dumps(
                    {
                        "name": asset.name,
                        "description": asset.description,
                        "prompt": asset.prompt,
                        "row_version": str(asset.row_version),
                    },
                    ensure_ascii=False,
                )
                media_id = asset.media_id
                metadata["source_row_version"] = str(asset.row_version)
            else:
                media_id = values.source_id
                name = "媒体附件"
            media = self.session.get(MediaFile, media_id) if media_id else None
            if media_id and (
                media is None or (media.project_id and media.project_id != conversation.project_id)
            ):
                raise NotFound("Media does not exist in the current scope")
            kind = media.format_code.split("/")[0] if media else "text"
            if kind not in MAX_INPUT_BYTES:
                raise WorkflowError("invalid_agent_attachment", "该媒体格式不支持作为对话输入", 422)
            if media:
                if not media.byte_size or media.byte_size > MAX_INPUT_BYTES[kind]:
                    raise WorkflowError("invalid_agent_attachment", "该媒体超过附件大小限制", 422)
                if media.duration_ms and media.duration_ms > 120000:
                    raise WorkflowError("invalid_agent_attachment", "媒体附件最长为 120 秒", 422)
                metadata.update(media.video_metadata or {})
                if kind == "video" and "has_audio" not in metadata:
                    metadata["has_audio"] = True
            row = AgentAttachment(
                id=next_id(),
                owner_user_id=conversation.owner_user_id,
                conversation_id=conversation.id,
                kind=kind,
                name=media.original_name if media and values.source_type == "media" else name,
                mime_type=media.format_code if media else "text/plain",
                media_id=media.id if media else None,
                text_content=text_content or (name if not media else None),
                checksum_sha256=media.checksum_sha256
                if media
                else hashlib.sha256(text_content.encode()).hexdigest(),
                byte_size=media.byte_size if media else len(text_content.encode()),
                input_metadata=metadata,
                create_key=key,
                create_hash=request_hash,
                created_at=utcnow(),
            )
            if not row.checksum_sha256:
                raise WorkflowError(
                    "invalid_agent_attachment", "该媒体缺少文件校验值，请重新上传", 422
                )
            self.session.add(row)
            self.session.flush()
            return self._read(row)

    def delete(self, conversation_id, attachment_id):
        with self._transaction():
            conversation = self._conversation(conversation_id, lock=True)
            row = self.session.scalar(
                select(AgentAttachment)
                .where(
                    AgentAttachment.id == parse_identifier(attachment_id),
                    AgentAttachment.conversation_id == conversation.id,
                    AgentAttachment.owner_user_id == conversation.owner_user_id,
                )
                .with_for_update()
            )
            if row is None:
                raise NotFound("Attachment does not exist")
            row.deleted_at = utcnow()
