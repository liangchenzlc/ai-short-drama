from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile
from sqlalchemy.orm import Session

from short_drama.api.dependencies import get_session
from short_drama.api.v1.assets import _version
from short_drama.schemas.base import Identifier
from short_drama.service.generation_reference_service import GenerationReferenceService

router = APIRouter(prefix="/generation-references", tags=["Generation reference images"])
SessionDep = Annotated[Session, Depends(get_session)]


def service(request, session, kind, version=None):
    return GenerationReferenceService(
        session, request.app.state.settings, request.app.state.storage, kind, version
    )


@router.get("/{kind}/{owner_id}")
def list_references(
    kind: Literal["asset", "shot"], owner_id: Identifier, request: Request, session: SessionDep
):
    return service(request, session, kind).list_references(owner_id)


@router.post("/{kind}/{owner_id}")
def upload_reference(
    kind: Literal["asset", "shot"],
    owner_id: Identifier,
    request: Request,
    session: SessionDep,
    if_match: Annotated[str, Header(alias="If-Match")],
    file: Annotated[UploadFile, File()],
):
    result, _ = service(request, session, kind, _version(if_match)).upload(
        owner_id, file.file, file.size, file.filename or "", file.content_type
    )
    return result


@router.delete("/{kind}/{owner_id}/{media_id}")
def remove_reference(
    kind: Literal["asset", "shot"],
    owner_id: Identifier,
    media_id: Identifier,
    request: Request,
    session: SessionDep,
    if_match: Annotated[str, Header(alias="If-Match")],
):
    return service(request, session, kind, _version(if_match)).remove_reference(owner_id, media_id)
