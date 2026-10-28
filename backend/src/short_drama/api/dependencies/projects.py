from fastapi import Depends, Request
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.service.media.files.storage import StorageService


def get_project_service(request: Request, session: Session = Depends(get_session)):
    from short_drama.service.projects.management.project import ProjectService

    return ProjectService(
        session, StorageService(request.app.state.storage, request.app.state.settings)
    )
