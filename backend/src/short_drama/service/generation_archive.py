"""Validate and archive individual generated media; adoption is a separate service."""

import base64
import hashlib
import time
from io import BytesIO

from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from short_drama.core.crypto import KeyCipher
from short_drama.core.exceptions import NotFound
from short_drama.dao.asset_image_candidate_dao import AssetImageCandidateDAO
from short_drama.dao.task_runtime_dao import LeaseLost, owned_task
from short_drama.domain import AIGenerationRecord, Asset, MediaAsset, MediaFile
from short_drama.service.base import utcnow
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id

MAX_MEDIA_BYTES = {"image": 50 * 1024**2, "video": 1024**3}


def inspect_media(data, kind):
    if not data or len(data) > MAX_MEDIA_BYTES[kind]:
        raise ValueError("Media exceeds size limit or is empty")
    if kind == "image":
        try:
            with Image.open(BytesIO(data)) as image:
                width, height = image.size
                fmt = image.format
                if fmt not in {"PNG", "JPEG", "WEBP", "GIF", "AVIF"}:
                    raise ValueError("Unsupported image format")
                image.verify()
                return {
                    "mime": Image.MIME.get(fmt, f"image/{fmt.lower()}"),
                    "ext": "jpg" if fmt == "JPEG" else fmt.lower(),
                    "width": width,
                    "height": height,
                }
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
            raise ValueError("Invalid image") from error
    if len(data) >= 12 and data[4:8] == b"ftyp":
        return {"mime": "video/mp4", "ext": "mp4"}
    if data.startswith(b"\x1aE\xdf\xa3"):
        return {"mime": "video/webm", "ext": "webm"}
    raise ValueError("Unsupported video container")


class GenerationArchive:
    def __init__(self, factory, settings, gateway, storage):
        self.factory, self.settings = factory, settings
        self.gateway, self.storage = gateway, storage

    def _cipher(self):
        return KeyCipher(
            self.settings.encryption_key.get_secret_value()
            if self.settings.encryption_key
            else None
        )

    def prepare(self, task, record, outputs):
        """Build recoverable identities BEFORE any object write, without storing inline bytes."""
        manifest = []
        for index, output in enumerate(outputs, start=1):
            entry = {
                "output_index": index,
                "asset_id": str(next_id()),
                "media_type": task.service_type,
            }
            if output.get("url"):
                entry["source_cipher"] = self._cipher().encrypt(output["url"])
            elif output.get("base64"):
                try:
                    data = base64.b64decode(output["base64"], validate=True)
                    entry.update(self._describe(task.id, record.id, entry, data))
                except Exception:
                    # The provider has completed; no fresh model request is safe here.
                    entry["save_error"] = {
                        "code": "result_unavailable",
                        "message": "内联媒体未能持久保存，不能自动重新生成",
                    }
            else:
                entry["save_error"] = {"code": "missing_output", "message": "供应商未返回可用文件"}
            manifest.append(entry)
        return manifest

    def stage_inline(self, task, record, outputs, manifest, still_owned):
        for output, entry in zip(outputs, manifest, strict=True):
            if not output.get("base64") or not entry.get("locator"):
                continue
            data = base64.b64decode(output["base64"], validate=True)
            for attempt in range(3):
                if not still_owned():
                    raise LeaseLost()
                try:
                    self._store(task.id, record.id, entry, data)
                    break
                except Exception:
                    if attempt < 2:
                        time.sleep(attempt + 1)
            # A failed PUT can have succeeded remotely. save will stat the durable locator;
            # it never repeats generation and never hands another worker a local path.

    def _describe(self, task_id, record_id, entry, data):
        meta = inspect_media(data, entry["media_type"])
        checksum = hashlib.sha256(data).hexdigest()
        bucket = (
            self.settings.minio_image_bucket
            if entry["media_type"] == "image"
            else self.settings.minio_video_bucket
        )
        # Different bytes from an expired worker must never overwrite the active result.
        key = f"generations/{task_id}/{record_id}/{entry['asset_id']}-{checksum}.{meta['ext']}"
        return {
            **meta,
            "byte_size": len(data),
            "checksum": checksum,
            "locator": ObjectLocation(bucket, key).locator,
        }

    def _store(self, task_id, record_id, entry, data):
        meta = self._describe(task_id, record_id, entry, data)
        location = ObjectLocation.parse(
            meta["locator"],
            {
                self.settings.minio_image_bucket,
                self.settings.minio_video_bucket,
            },
        )
        bucket, key, checksum = location.bucket, location.object_name, meta["checksum"]
        try:
            existing = self.storage.stat(bucket, key)
        except NotFound:
            existing = None
        if existing is None:
            self.storage.put(bucket, key, BytesIO(data), len(data), meta["mime"])
        else:
            # Never overwrite an already archived object, including from a stale worker.
            with self.storage.open(bucket, key) as stream:
                digest = hashlib.sha256()
                for chunk in stream.stream(64 * 1024):
                    digest.update(chunk)
            if existing.size != len(data) or digest.hexdigest() != checksum:
                raise ValueError("Existing archive object does not match output")
        return meta

    def _link_asset_candidate(self, session, request: dict, media_id: int) -> str | None:
        source = request.get("source") or {}
        if source.get("scene") != "asset_image":
            return None
        asset = session.get(Asset, int(source["asset_id"]), with_for_update=True)
        if asset is None:
            return "source_missing"
        candidates = AssetImageCandidateDAO(session)
        candidate = candidates.get_for_asset(asset.id, media_id, for_update=True)
        if candidate is None:
            candidates.create({"asset_id": asset.id, "media_id": media_id})
        return "linked"

    def save_one(self, task, record, entry, version, token):
        with self.factory.begin() as session:
            owned_task(session, task.id, version, token)
            current_record = session.get(AIGenerationRecord, record.id)
            existing = session.scalar(
                select(MediaAsset).where(
                    MediaAsset.record_id == record.id,
                    MediaAsset.output_index == entry["output_index"],
                )
            )
            if existing is not None:
                if existing.id != int(entry["asset_id"]):
                    raise ValueError("Output identity conflict")
                candidate_status = self._link_asset_candidate(
                    session, current_record.request_data, existing.media_id
                )
                data = dict(current_record.response_data or {})
                manifest = [dict(item) for item in data.get("media_manifest", [])]
                for item in manifest:
                    if item["output_index"] == entry["output_index"]:
                        item["saved"] = True
                        if candidate_status is not None:
                            item["candidate_status"] = candidate_status
                data["media_manifest"] = manifest
                current_record.response_data = data
                current_record.updated_at = utcnow()
                return True
        if not entry.get("locator"):
            if not entry.get("source_cipher"):
                return False
            url = self._cipher().decrypt(entry["source_cipher"])
            data, _mime = self.gateway.download_media(url, MAX_MEDIA_BYTES[task.service_type])
            entry = {**entry, **self._store(task.id, record.id, entry, data)}
        location = ObjectLocation.parse(
            entry["locator"],
            {self.settings.minio_image_bucket, self.settings.minio_video_bucket},
        )
        stored = self.storage.stat(location.bucket, location.object_name)
        if stored.size != entry["byte_size"]:
            raise ValueError("Stored media size changed")
        with self.factory.begin() as session:
            owned_task(session, task.id, version, token)
            current_record = session.get(AIGenerationRecord, record.id)
            asset = session.scalar(
                select(MediaAsset).where(
                    MediaAsset.record_id == record.id,
                    MediaAsset.output_index == entry["output_index"],
                )
            )
            if asset is not None and asset.id != int(entry["asset_id"]):
                raise ValueError("Output identity conflict")
            media = session.scalar(
                select(MediaFile).where(MediaFile.storage_locator == entry["locator"])
            )
            now = utcnow()
            if media is None:
                media = MediaFile(
                    id=next_id(),
                    format_code=entry["mime"],
                    storage_locator=entry["locator"],
                    original_name="",
                    byte_size=entry["byte_size"],
                    width=entry.get("width"),
                    height=entry.get("height"),
                    checksum_sha256=entry["checksum"],
                    created_at=now,
                    updated_at=now,
                )
                session.add(media)
                session.flush()
            elif media.checksum_sha256 != entry["checksum"]:
                raise ValueError("Stored metadata does not match output")
            if asset is None:
                label = "\u56fe\u7247" if task.service_type == "image" else "\u89c6\u9891"
                asset = MediaAsset(
                    id=int(entry["asset_id"]),
                    record_id=record.id,
                    output_index=entry["output_index"],
                    media_id=media.id,
                    media_type=task.service_type,
                    name=f"{label}-{task.id}-{entry['output_index']}",
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
                session.add(asset)
                session.flush()
            elif asset.media_id != media.id:
                raise ValueError("Output media identity conflict")
            candidate_status = self._link_asset_candidate(
                session, current_record.request_data, media.id
            )
            data = dict(current_record.response_data or {})
            manifest = [dict(item) for item in data.get("media_manifest", [])]
            for item in manifest:
                if item["output_index"] == entry["output_index"]:
                    item.update(entry)
                    item.pop("source_cipher", None)
                    item.pop("save_error", None)
                    item["saved"] = True
                    if candidate_status is not None:
                        item["candidate_status"] = candidate_status
            data["media_manifest"] = manifest
            current_record.response_data = data
            current_record.updated_at = now
        return True
