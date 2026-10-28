from fastapi import Depends, Request
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session


def get_project_writing_service(session: Session = Depends(get_session)):
    from short_drama.service.creation.writing.script.documents import ProjectWritingService

    return ProjectWritingService(session)


def get_asset_library_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.creation.assets.management.library import AssetLibraryService

    return AssetLibraryService(session, request.app.state.settings, request.app.state.storage)


def get_asset_image_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.creation.assets.images.image import AssetImageService

    return AssetImageService(session, request.app.state.settings, request.app.state.storage)


def get_user_creation_draft_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.creation.drafts.drafts import UserCreationDraftService

    return UserCreationDraftService(session, request.app.state.settings, request.app.state.storage)
