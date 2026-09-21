import hashlib
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile

from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.asset_image_candidate_dao import AssetImageCandidateDAO
from short_drama.dao.asset_library_dao import AssetLibraryDAO
from short_drama.dao.base import BaseDAO
from short_drama.domain.ai_generation_record import AIGenerationRecord
from short_drama.domain.asset import Asset
from short_drama.domain.asset_image_candidate import AssetImageCandidate
from short_drama.domain.media_asset import MediaAsset
from short_drama.domain.media_file import MediaFile
from short_drama.schemas.asset_image_candidate import (
    AssetConfirm,
    AssetImageCandidateRead,
)
from short_drama.schemas.base import parse_identifier

from .asset_library_service import AssetLibraryService
from .base import BaseService, utcnow
from .storage_service import StorageService

MAX_UPLOAD_BYTES = 20 * 1024**2
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


@dataclass
class InspectedUpload:
    stream: SpooledTemporaryFile
    byte_size: int
    checksum_sha256: str
    content_type: str
    width: int
    height: int


def inspect_image_upload(stream, _claimed_content_type=None):
    if not callable(getattr(stream, "read", None)):
        raise WorkflowError("invalid_image", "Upload requires a binary stream", 422)
    output = SpooledTemporaryFile(max_size=4 * 1024**2)
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = stream.read(min(1024**2, MAX_UPLOAD_BYTES + 1 - total))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise WorkflowError("invalid_image", "Upload requires a binary stream", 422)
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            output.close()
            raise WorkflowError("upload_too_large", "Image upload must not exceed 20 MiB", 413)
        digest.update(chunk)
        output.write(chunk)
    output.seek(0)
    try:
        with Image.open(output) as image:
            content_type = IMAGE_MIMES.get((image.format or "").upper())
            width, height = image.size
            if content_type is None:
                raise WorkflowError(
                    "invalid_image", "Only PNG, JPEG, or WebP images are supported", 422
                )
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise WorkflowError(
                    "invalid_image", "Decoded image must not exceed 40 million pixels", 422
                )
            image.verify()
    except WorkflowError:
        output.close()
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        output.close()
        raise WorkflowError(
            "invalid_image", "Only valid PNG, JPEG, or WebP images are supported", 422
        ) from None
    output.seek(0)
    return InspectedUpload(
        stream=output,
        byte_size=total,
        checksum_sha256=digest.hexdigest(),
        content_type=content_type,
        width=width,
        height=height,
    )


class AssetImageService(BaseService):
    model = AssetImageCandidate

    def __init__(self, session, settings, storage):
        super().__init__(session)
        self.settings = settings
        self.storage = (
            storage
            if callable(getattr(storage, "upload", None))
            else StorageService(storage, settings)
        )
        self.candidates = AssetImageCandidateDAO(session)
        self.assets = BaseDAO(session, Asset)
        self.media = BaseDAO(session, MediaFile)
        self.library = AssetLibraryDAO(session)

    def _ensure_asset_exists(self, asset_id):
        with self._transaction():
            if self.assets.get(parse_identifier(asset_id)) is None:
                raise NotFound("Asset does not exist")

    def _candidate_read(self, candidate, media):
        return AssetImageCandidateRead(
            id=candidate.id,
            media_id=media.id,
            url=self.storage.download_url(media.storage_locator),
            width=media.width,
            height=media.height,
            created_at=candidate.created_at,
        )

    def list(self, asset_id, offset=0, limit=20):
        asset_id = parse_identifier(asset_id)
        self.candidates.validate_pagination(offset, limit)
        with self._transaction():
            if self.assets.get(asset_id) is None:
                raise NotFound("Asset does not exist")
            rows = self.candidates.list_with_media(asset_id, offset, limit)
            total = self.session.scalar(
                select(func.count()).select_from(AssetImageCandidate).where(
                    AssetImageCandidate.asset_id == asset_id
                )
            )
            return {
                "items": [self._candidate_read(candidate, media) for candidate, media in rows],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    def _generated_image(self, media_id):
        return self.session.execute(
            select(MediaAsset, AIGenerationRecord)
            .join(AIGenerationRecord, AIGenerationRecord.id == MediaAsset.record_id)
            .where(MediaAsset.media_id == media_id, MediaAsset.media_type == "image")
        ).first()

    def _shared_uploaded_image(self, asset_id, media_id):
        source_ids = set(
            self.session.scalars(
                select(AssetImageCandidate.asset_id).where(
                    AssetImageCandidate.media_id == media_id
                )
            )
        )
        target_projects = self.library.project_ids(asset_id)
        return any(
            self.library.is_global(source_id)
            or bool(target_projects & self.library.project_ids(source_id))
            for source_id in source_ids
        )

    def add_candidate(self, asset_id, media_id):
        asset_id, media_id = parse_identifier(asset_id), parse_identifier(media_id)
        with self._transaction():
            candidate, media, created = self.ensure_candidate_locked(asset_id, media_id)
        return self._candidate_read(candidate, media), created

    def ensure_candidate_locked(self, asset_id, media_id):
        """Ensure a generated image candidate while the caller owns the transaction."""
        asset_id, media_id = parse_identifier(asset_id), parse_identifier(media_id)
        if self.assets.get(asset_id, for_update=True) is None:
            raise NotFound("Asset does not exist")
        existing = self.candidates.get_for_asset(asset_id, media_id, for_update=True)
        media = self.media.get(media_id)
        if media is None:
            raise NotFound("Media does not exist")
        if existing is not None:
            return existing, media, False
        shareable = self._generated_image(media_id) is not None or self._shared_uploaded_image(
            asset_id, media_id
        )
        if not media.format_code.startswith("image/") or not shareable:
            raise WorkflowError(
                "media_not_shareable",
                "Image is not available through an allowed asset sharing path",
                400,
            )
        candidate = self.candidates.create({"asset_id": asset_id, "media_id": media_id})
        return candidate, media, True

    def _persist_upload(self, asset_id, inspected, stored, original_name):
        with self._transaction():
            if self.assets.get(asset_id, for_update=True) is None:
                raise NotFound("Asset does not exist")
            duplicate = self.candidates.duplicate_upload(
                asset_id, inspected.checksum_sha256, inspected.byte_size
            )
            if duplicate is not None:
                candidate, media = duplicate
                return candidate, media, False
            now = utcnow()
            media = self.media.create(
                {
                    "format_code": inspected.content_type,
                    "storage_locator": stored.storage_locator,
                    "original_name": (original_name or "")[:255],
                    "byte_size": inspected.byte_size,
                    "width": inspected.width,
                    "height": inspected.height,
                    "duration_ms": None,
                    "checksum_sha256": inspected.checksum_sha256,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": None,
                    "updated_by": None,
                }
            )
            candidate = self.candidates.create({"asset_id": asset_id, "media_id": media.id})
            return candidate, media, True

    def _storage_locator_exists(self, locator):
        """Resolve uncertain commit outcomes before compensating an uploaded object."""
        with self._transaction():
            return self.session.scalar(
                select(func.count()).select_from(MediaFile).where(
                    MediaFile.storage_locator == locator
                )
            ) > 0

    def upload(self, asset_id, stream, length, name, content_type=None):
        asset_id = parse_identifier(asset_id)
        if length is not None and (
            type(length) is not int or length < 0 or length > MAX_UPLOAD_BYTES
        ):
            raise WorkflowError("upload_too_large", "Image upload must not exceed 20 MiB", 413)
        self._ensure_asset_exists(asset_id)
        inspected = inspect_image_upload(stream, content_type)
        if length is not None and length != inspected.byte_size:
            inspected.stream.close()
            raise WorkflowError(
                "invalid_image", "Upload byte length does not match the file", 422
            )
        stored = None
        try:
            stored = self.storage.upload(
                inspected.stream,
                length=inspected.byte_size,
                content_type=inspected.content_type,
            )
            try:
                candidate, media, created = self._persist_upload(
                    asset_id, inspected, stored, name
                )
            except Exception:
                # A failed commit acknowledgement is ambiguous. Delete only after a fresh
                # database check proves the locator never became durable. If that check is
                # unavailable, retain the object for reconciliation.
                try:
                    locator_exists = self._storage_locator_exists(stored.storage_locator)
                except Exception:
                    locator_exists = True
                if not locator_exists:
                    try:
                        self.storage.delete(
                            stored.storage_locator, version_id=stored.version_id
                        )
                    except Exception:
                        pass
                raise
            if not created:
                self.storage.delete(stored.storage_locator, version_id=stored.version_id)
            return self._candidate_read(candidate, media), created
        except Exception:
            raise
        finally:
            inspected.stream.close()

    def confirm(self, asset_id, payload):
        asset_id = parse_identifier(asset_id)
        parsed = AssetConfirm.model_validate(payload)
        with self._transaction():
            asset, media = self.confirm_locked(asset_id, parsed)
            result = AssetLibraryService(
                self.session, self.settings, self.storage
            )._read(asset, media)
        return result

    def confirm_locked(self, asset_id, payload, *, add_generated_candidate=False):
        """Adopt a candidate while the caller owns a transaction; returns ORM rows."""
        asset_id = parse_identifier(asset_id)
        parsed = AssetConfirm.model_validate(payload)
        if add_generated_candidate:
            self.ensure_candidate_locked(asset_id, parsed.media_id)
        asset = self.assets.get(asset_id, for_update=True)
        if asset is None:
            raise NotFound("Asset does not exist")
        if asset.row_version != parsed.row_version:
            raise WorkflowError(
                "asset_version_conflict", "Asset changed; refresh before confirming",
                details={"current_version": asset.row_version},
            )
        if asset.media_id != parsed.expected_media_id:
            raise WorkflowError(
                "asset_version_conflict", "Current asset image changed; refresh first"
            )
        candidate = self.candidates.get_for_asset(asset_id, parsed.media_id, for_update=True)
        media = self.media.get(parsed.media_id)
        if candidate is None or media is None or not media.format_code.startswith("image/"):
            raise WorkflowError("invalid_image", "Selected image is not an asset candidate", 422)
        if not asset.name.strip() or not (asset.description.strip() or asset.prompt.strip()):
            raise WorkflowError(
                "asset_content_required",
                "Provide a name and either description or prompt before confirming",
                400,
            )
        if asset.state == "confirmed" and asset.media_id == parsed.media_id:
            return asset, media
        references = self.library.reference_count(asset.id)
        if references > 1 and not parsed.confirm_shared:
            raise WorkflowError(
                "shared_asset_confirmation_required",
                "Confirm updating every reference to this shared asset",
                details={"reference_count": references},
            )
        generated = self._generated_image(parsed.media_id)
        model_id = generated[1].config_id if generated is not None else None
        self.assets.update(
            asset,
            {
                "media_id": parsed.media_id,
                "model_id": model_id,
                "state": "confirmed",
                "row_version": asset.row_version + 1,
            },
        )
        return asset, media


def confirm_asset_image(session, settings, storage, asset_id, payload):
    """Shared adoption entry point used by both asset API and media-library apply."""
    return AssetImageService(session, settings, storage).confirm(asset_id, payload)
