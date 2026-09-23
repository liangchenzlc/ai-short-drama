from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Header, Path, Query, Response, UploadFile, status

from short_drama.api.dependencies import get_asset_image_service, get_asset_library_service
from short_drama.core.exceptions import WorkflowError
from short_drama.schemas.asset import AssetPatch, AssetRead
from short_drama.schemas.asset_image_candidate import (
    AssetConfirm,
    AssetImageCandidateCreate,
    AssetImageCandidateRead,
)
from short_drama.schemas.asset_library import AssetLibraryCreate, LibraryAssetRead
from short_drama.schemas.base import Identifier, parse_identifier
from short_drama.schemas.common import PageResponse
from short_drama.service.asset_image_service import AssetImageService
from short_drama.service.asset_library_service import AssetLibraryService

router = APIRouter(tags=["Asset libraries"])
AssetId = Annotated[Identifier, Path(description="Decimal Snowflake asset identifier")]
ScopedId = Annotated[Identifier, Path(description="Decimal Snowflake scope identifier")]
Libraries = Annotated[AssetLibraryService, Depends(get_asset_library_service)]
Images = Annotated[AssetImageService, Depends(get_asset_image_service)]
CreationKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def _version(value):
    if not isinstance(value, str) or len(value) > 24:
        raise WorkflowError("invalid_version", "If-Match must be a quoted asset version", 422)
    if value.startswith("W/"):
        raise WorkflowError("invalid_version", "A strong If-Match asset version is required", 422)
    if len(value) < 3 or value[0] != '"' or value[-1] != '"':
        raise WorkflowError("invalid_version", "If-Match must be a quoted asset version", 422)
    try:
        return parse_identifier(value[1:-1])
    except ValueError:
        raise WorkflowError(
            "invalid_version", "If-Match must be a quoted asset version", 422
        ) from None


def _list(service, kind, parent_id, project_id, asset_kind, q, offset, limit):
    return service.list(
        kind, parent_id, project_id, asset_kind=asset_kind, q=q.strip(), offset=offset, limit=limit
    )


@router.get("/libraries/global/assets", response_model=PageResponse[LibraryAssetRead])
def list_global_assets(
    service: Libraries,
    kind: Annotated[Literal["character", "scene", "prop"] | None, Query()] = None,
    q: Annotated[str, Query(max_length=120)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return _list(service, "global", None, None, kind, q, offset, limit)


@router.post("/libraries/global/assets", response_model=LibraryAssetRead)
def create_global_asset(
    payload: AssetLibraryCreate, key: CreationKey, response: Response, service: Libraries
):
    result, created = service.create("global", None, None, payload, key)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return result


@router.put("/libraries/global/assets/{asset_id}", response_model=LibraryAssetRead)
def link_global_asset(asset_id: AssetId, service: Libraries):
    return service.link("global", None, None, asset_id)


@router.delete("/libraries/global/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_global_asset(
    asset_id: AssetId,
    service: Libraries,
    if_match: Annotated[str, Header(alias="If-Match")],
):
    service.unlink("global", None, None, asset_id, _version(if_match))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/projects/{project_id}/assets", response_model=PageResponse[LibraryAssetRead])
def list_project_assets(
    project_id: ScopedId,
    service: Libraries,
    kind: Annotated[Literal["character", "scene", "prop"] | None, Query()] = None,
    q: Annotated[str, Query(max_length=120)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return _list(service, "project", project_id, project_id, kind, q, offset, limit)


@router.post("/projects/{project_id}/assets", response_model=LibraryAssetRead)
def create_project_asset(
    project_id: ScopedId,
    payload: AssetLibraryCreate,
    key: CreationKey,
    response: Response,
    service: Libraries,
):
    result, created = service.create("project", project_id, project_id, payload, key)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return result


@router.put("/projects/{project_id}/assets/{asset_id}", response_model=LibraryAssetRead)
def link_project_asset(project_id: ScopedId, asset_id: AssetId, service: Libraries):
    return service.link("project", project_id, project_id, asset_id)


@router.delete("/projects/{project_id}/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def unlink_project_asset(
    project_id: ScopedId,
    asset_id: AssetId,
    service: Libraries,
    if_match: Annotated[str, Header(alias="If-Match")],
):
    service.unlink("project", project_id, project_id, asset_id, _version(if_match))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/projects/{project_id}/episodes/{episode_id}/assets",
    response_model=PageResponse[LibraryAssetRead],
)
def list_episode_assets(
    project_id: ScopedId,
    episode_id: ScopedId,
    service: Libraries,
    kind: Annotated[Literal["character", "scene", "prop"] | None, Query()] = None,
    q: Annotated[str, Query(max_length=120)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return _list(service, "episode", episode_id, project_id, kind, q, offset, limit)


@router.post("/projects/{project_id}/episodes/{episode_id}/assets", response_model=LibraryAssetRead)
def create_episode_asset(
    project_id: ScopedId,
    episode_id: ScopedId,
    payload: AssetLibraryCreate,
    key: CreationKey,
    response: Response,
    service: Libraries,
):
    result, created = service.create("episode", episode_id, project_id, payload, key)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return result


@router.put(
    "/projects/{project_id}/episodes/{episode_id}/assets/{asset_id}",
    response_model=LibraryAssetRead,
)
def link_episode_asset(
    project_id: ScopedId, episode_id: ScopedId, asset_id: AssetId, service: Libraries
):
    return service.link("episode", episode_id, project_id, asset_id)


@router.delete(
    "/projects/{project_id}/episodes/{episode_id}/assets/{asset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def unlink_episode_asset(
    project_id: ScopedId,
    episode_id: ScopedId,
    asset_id: AssetId,
    service: Libraries,
    if_match: Annotated[str, Header(alias="If-Match")],
):
    service.unlink("episode", episode_id, project_id, asset_id, _version(if_match))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/assets/{asset_id}", response_model=AssetRead)
def get_asset(asset_id: AssetId, service: Libraries):
    return service.get(asset_id)


@router.patch("/assets/{asset_id}", response_model=AssetRead)
def patch_asset(asset_id: AssetId, payload: AssetPatch, service: Libraries):
    return service.patch(asset_id, payload)


@router.get(
    "/assets/{asset_id}/image-candidates",
    response_model=PageResponse[AssetImageCandidateRead],
)
def list_asset_image_candidates(
    asset_id: AssetId,
    service: Images,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.list(asset_id, offset, limit)


@router.post("/assets/{asset_id}/image-candidates", response_model=AssetImageCandidateRead)
def add_asset_image_candidate(
    asset_id: AssetId, payload: AssetImageCandidateCreate, response: Response, service: Images
):
    result, created = service.add_candidate(asset_id, payload.media_id)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return result


@router.post("/assets/{asset_id}/image-candidates/upload", response_model=AssetImageCandidateRead)
def upload_asset_image_candidate(
    asset_id: AssetId,
    response: Response,
    service: Images,
    file: Annotated[UploadFile, File()],
):
    result, created = service.upload(
        asset_id, file.file, file.size, file.filename or "", file.content_type
    )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return result


@router.post("/assets/{asset_id}/confirm", response_model=AssetRead)
def confirm_asset_image(asset_id: AssetId, payload: AssetConfirm, service: Images):
    return service.confirm(asset_id, payload)
