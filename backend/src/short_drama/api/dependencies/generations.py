from fastapi import Depends, Request
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session


def get_ai_generation_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.generations.admission.generation import AIGenerationService

    return AIGenerationService(session, request.app.state.settings)
