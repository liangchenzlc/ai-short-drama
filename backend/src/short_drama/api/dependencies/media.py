from fastapi import Depends, Request
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.service.media.files.storage import StorageService


def get_storage_service(request: Request) -> StorageService:
    return StorageService(request.app.state.storage, request.app.state.settings)


def get_media_asset_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.media.library.assets import MediaAssetService

    return MediaAssetService(session, request.app.state.settings, request.app.state.storage)
