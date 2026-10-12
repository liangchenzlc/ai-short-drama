"""从当前不可变版本解析真实资源；只读，不复制、不检查对象字节。"""

import re

from pydantic import ValidationError
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError, NotFound, WorkflowError
from short_drama.db.access import scope_of
from short_drama.domain import Asset, MediaFile, ProjectCanvas
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.canvas_character_catalog import CanvasCharacterRepresentationRead
from short_drama.schemas.canvas_character_images import (
    CanvasCharacterImageRead,
    CanvasCharacterImagesRead,
)

from .canvas_character_catalog_service import CanvasCharacterCatalogService
from .canvas_resource_service import CanvasResourceService


class CanvasCharacterImageService(CanvasCharacterCatalogService):
    def __init__(self, session: Session, settings: Settings) -> None:
        super().__init__(session)
        # 只复用无外部 I/O 的资源读取模型与定位解析，不提供 Storage 实例。
        self.resource_service = CanvasResourceService(session, settings, None)

    def _resource(
        self,
        asset: Asset,
        representation: CanvasCharacterRepresentationRead,
        canvas: ProjectCanvas,
    ) -> CanvasCharacterImageRead:
        resources = self.resource_service.resources
        resource = resources.resource(representation.media_id, lock=True)
        if not isinstance(resource, MediaFile):
            raise NotFound("角色表示图没有可读的原生图片资源")
        # resource() 优先媒体，resource_map() 同 ID 的二进制实体会覆盖媒体。
        # 两个命名空间都可见时明确拒绝，不能按查询顺序猜测资源。
        mapped = resources.resource_map({resource.id}, lock=True).get(resource.id)
        if not isinstance(mapped, MediaFile):
            raise WorkflowError(
                "canvas_character_resource_ambiguous", "角色图片资源标识存在歧义", 409
            )
        if scope_of(self.session, resource) != scope_of(self.session, asset):
            raise NotFound("角色图片资源不属于该角色范围")
        if (
            resource.created_by is None
            or resource.created_at is None
            or resource.updated_at is None
            or resource.byte_size is None
            or resource.byte_size <= 0
            or not re.fullmatch(r"image/[a-zA-Z0-9!#$&^_.+\-]+", resource.format_code)
        ):
            raise WorkflowError(
                "canvas_character_resource_invalid",
                "角色图片资源缺少有效媒体或审计信息",
                409,
            )
        try:
            self.resource_service.location(resource.storage_locator)
            read = self.resource_service.read_model(resource)
        except (BusinessError, ValidationError, TypeError, ValueError):
            raise WorkflowError(
                "canvas_character_resource_invalid",
                "角色图片资源定位或读取合同无效",
                409,
            ) from None
        return CanvasCharacterImageRead(
            reference_id=representation.reference_id,
            role=representation.role,
            ordinal=representation.ordinal,
            media_id=representation.media_id,
            resource_key=f"resource:{resource.id}",
            file_path=f"/api/v1/canvas-runtime/resources/{resource.id}/file",
            requires_canvas_copy=resource.project_id != canvas.project_id,
            resource=read,
        )

    def get(
        self, source_key: str, asset_id: int, expected_version_id: int
    ) -> CanvasCharacterImagesRead:
        _ = self.actor_id
        asset_id = parse_identifier(asset_id)
        expected_version_id = parse_identifier(expected_version_id)
        with self._transaction(read_only=True):
            canvas = self._bound_canvas(source_key)
            relation = self.catalog.relation(canvas.source_project_id, asset_id)
            if relation is None:
                raise NotFound("角色未关联到该来源项目")
            asset = self.catalog.locked_asset(asset_id)
            if asset is None or asset.kind != "character":
                raise NotFound("角色不存在或当前账号无权访问")
            if asset.current_version_id != expected_version_id:
                raise WorkflowError(
                    "canvas_character_version_changed",
                    "角色当前版本已变化，请重新读取角色",
                    409,
                )
            item = self._item(relation, asset)
            return CanvasCharacterImagesRead(
                **self._context(canvas),
                asset_id=asset.id,
                version_id=item.current_version_id,
                images=[self._resource(asset, slot, canvas) for slot in item.representations],
            )
