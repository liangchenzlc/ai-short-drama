from fastapi import Depends, Request
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.service.ai_config.management.models import AIModelConfigService


def get_ai_config_service(request: Request, session: Session = Depends(get_session)):
    return AIModelConfigService(session, settings=request.app.state.settings)


def get_model_preference_service(session: Session = Depends(get_session)):
    from short_drama.service.ai_config.preferences.preferences import ModelPreferenceService

    return ModelPreferenceService(session)
