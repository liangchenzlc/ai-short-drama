from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from short_drama.api.dependencies import get_media_asset_service
from short_drama.schemas.base import Identifier
from short_drama.schemas.media_asset import MediaAssetApply, MediaAssetRename

router = APIRouter(prefix="/media-library/items", tags=["media-library"])
Service = Annotated[object, Depends(get_media_asset_service)]


@router.get("")
def list_assets(
    service: Service,
    media_type: Literal["image", "video"] | None = None,
    name: Annotated[str | None, Query(max_length=255)] = None,
    source_scene: Literal["shot_image"] | None = None,
    source_id: Identifier | None = None,
    project_id: Identifier | None = None,
    episode_id: Identifier | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    return service.list(
        offset,
        limit,
        {
            "media_type": media_type,
            "name": name,
            "source_scene": source_scene,
            "source_id": source_id,
            "project_id": project_id,
            "episode_id": episode_id,
            "created_after": created_after,
            "created_before": created_before,
        },
    )


@router.get("/{asset_id}")
def detail(asset_id: Identifier, service: Service):
    return service.detail(asset_id)


@router.patch("/{asset_id}")
def rename(asset_id: Identifier, body: MediaAssetRename, service: Service):
    return service.rename(asset_id, body)


@router.post("/{asset_id}/apply")
def apply(asset_id: Identifier, body: MediaAssetApply, service: Service):
    return service.apply(asset_id, body)
