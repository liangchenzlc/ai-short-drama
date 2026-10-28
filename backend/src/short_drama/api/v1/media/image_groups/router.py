from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, Query, Request, Response, UploadFile
from sqlalchemy.orm import Session

from short_drama.api.dependencies.session import get_session
from short_drama.schemas.base import Identifier
from short_drama.schemas.media.image_groups.groups import (
    ImageGroupPage,
    ImageGroupRead,
    ImageOperationCreate,
    ImageOperationRead,
    ImagePrimarySet,
)
from short_drama.service.media.image_groups.groups import ImageGroupService
from short_drama.service.media.image_tools.operations import ImageOperationService

router = APIRouter(tags=["Image groups and tools"])
SessionDep = Annotated[Session, Depends(get_session)]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def service(request, session, cls=ImageGroupService):
    return cls(session, request.app.state.settings, request.app.state.storage)


@router.get("/image-groups/current/{kind}/{target_id}", response_model=ImageGroupRead | None)
def current(
    kind: Literal["asset", "shot"], target_id: Identifier, request: Request, session: SessionDep
):
    return service(request, session).current(kind, target_id)


@router.get("/image-groups/history/{kind}/{target_id}", response_model=ImageGroupPage)
def history(
    kind: Literal["asset", "shot"],
    target_id: Identifier,
    request: Request,
    session: SessionDep,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service(request, session).history(kind, target_id, offset, limit)


@router.get("/image-groups/media/{media_id}/content")
def content(media_id: Identifier, request: Request, session: SessionDep):
    images = service(request, session, ImageOperationService)
    locator, mime = images.source(media_id)
    data = images._bytes(locator)
    return Response(
        data,
        media_type=mime,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'inline; filename="image-{media_id}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/image-groups/{group_id}", response_model=ImageGroupRead)
def detail(group_id: Identifier, request: Request, session: SessionDep):
    return service(request, session).detail(group_id)


@router.put("/image-groups/{group_id}/primary", response_model=ImageGroupRead)
def primary(group_id: Identifier, body: ImagePrimarySet, request: Request, session: SessionDep):
    return service(request, session).primary(group_id, body)


@router.post("/image-operations", response_model=ImageOperationRead, status_code=201)
def create_operation(
    body: ImageOperationCreate, request: Request, session: SessionDep, idempotency_key: Key
):
    return service(request, session, ImageOperationService).create_operation(body, idempotency_key)


@router.get("/image-operations/{operation_id}", response_model=ImageOperationRead)
def operation(operation_id: Identifier, request: Request, session: SessionDep):
    return service(request, session, ImageOperationService).get_operation(operation_id)


@router.post("/image-operations/{operation_id}/execute", response_model=ImageOperationRead)
def execute(
    operation_id: Identifier,
    request: Request,
    session: SessionDep,
    file: Annotated[UploadFile | None, File()] = None,
):
    return service(request, session, ImageOperationService).execute_operation(
        operation_id, file.file if file else None
    )
