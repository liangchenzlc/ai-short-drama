from sqlalchemy import select

from short_drama.domain.asset_image_candidate import AssetImageCandidate
from short_drama.domain.media_file import MediaFile

from .base import BaseDAO


class AssetImageCandidateDAO(BaseDAO):
    def __init__(self, session):
        super().__init__(session, AssetImageCandidate)

    def get_for_asset(self, asset_id, media_id, *, for_update=False):
        statement = select(AssetImageCandidate).where(
            AssetImageCandidate.asset_id == asset_id,
            AssetImageCandidate.media_id == media_id,
        )
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def list_with_media(self, asset_id, offset, limit):
        self.validate_pagination(offset, limit)
        statement = (
            select(AssetImageCandidate, MediaFile)
            .join(MediaFile, MediaFile.id == AssetImageCandidate.media_id)
            .where(AssetImageCandidate.asset_id == asset_id)
            .order_by(AssetImageCandidate.created_at.desc(), AssetImageCandidate.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return self.session.execute(statement).all()

    def duplicate_upload(self, asset_id, checksum, byte_size):
        statement = (
            select(AssetImageCandidate, MediaFile)
            .join(MediaFile, MediaFile.id == AssetImageCandidate.media_id)
            .where(
                AssetImageCandidate.asset_id == asset_id,
                MediaFile.checksum_sha256 == checksum,
                MediaFile.byte_size == byte_size,
            )
            .order_by(AssetImageCandidate.created_at, AssetImageCandidate.id)
            .limit(1)
        )
        return self.session.execute(statement).first()
