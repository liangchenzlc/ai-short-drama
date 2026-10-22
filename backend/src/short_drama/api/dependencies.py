from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.storage_service import StorageService


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        actor = getattr(request.state, "actor", None)
        if actor is not None:
            session.info["actor"] = actor
            scope = getattr(request.state, "resource_scope", None)
            if scope is not None:
                session.info["resource_scope"] = scope
                session.info["request_project"] = scope[1]
        yield session


def get_project_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.project_service import ProjectService

    return ProjectService(
        session, StorageService(request.app.state.storage, request.app.state.settings)
    )


def get_project_writing_service(session: Session = Depends(get_session)):
    from short_drama.service.project_writing_service import ProjectWritingService

    return ProjectWritingService(session)


def get_storage_service(request: Request) -> StorageService:
    return StorageService(request.app.state.storage, request.app.state.settings)


def get_ai_config_service(request: Request, session: Session = Depends(get_session)):
    return AIModelConfigService(session, settings=request.app.state.settings)


def get_ai_generation_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.ai_generation_service import AIGenerationService

    return AIGenerationService(session, request.app.state.settings)


def get_media_asset_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.media_asset_service import MediaAssetService

    return MediaAssetService(session, request.app.state.settings, request.app.state.storage)


def get_asset_library_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.asset_library_service import AssetLibraryService

    return AssetLibraryService(session, request.app.state.settings, request.app.state.storage)


def get_asset_image_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.asset_image_service import AssetImageService

    return AssetImageService(session, request.app.state.settings, request.app.state.storage)


def get_user_creation_draft_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.user_creation_draft_service import UserCreationDraftService

    return UserCreationDraftService(session, request.app.state.settings, request.app.state.storage)


def get_model_preference_service(session: Session = Depends(get_session)):
    from short_drama.service.model_preference_service import ModelPreferenceService

    return ModelPreferenceService(session)
