"""从可信画布来源读取已采用角色作品；不补版、不建媒体映射、不调用供应商。"""

from pydantic import TypeAdapter, ValidationError

from short_drama.core.exceptions import Conflict, NotFound, WorkflowError
from short_drama.dao.asset_version_dao import AssetVersionDAO
from short_drama.dao.canvas_character_catalog_dao import CanvasCharacterCatalogDAO
from short_drama.db.access import require_project, scope_of
from short_drama.domain import Asset, ProjectAsset, ProjectCanvas
from short_drama.schemas.base import parse_identifier
from short_drama.schemas.canvas import CanvasKey
from short_drama.schemas.canvas_character_catalog import (
    CanvasCharacterCatalogDetailRead,
    CanvasCharacterCatalogItemRead,
    CanvasCharacterCatalogRead,
    CanvasCharacterRepresentationRead,
)

from .asset_version_service import AssetVersionService
from .canvas_service import CanvasService


class CanvasCharacterCatalogService(CanvasService):
    def __init__(self, session, *, settings=None):
        super().__init__(session)
        self.settings = settings
        self.catalog = CanvasCharacterCatalogDAO(session)
        self.versions = AssetVersionDAO(session)

    def _bound_canvas(self, source_key: str) -> ProjectCanvas:
        try:
            source_key = TypeAdapter(CanvasKey).validate_python(source_key)
        except ValidationError:
            raise WorkflowError("invalid_canvas_key", "画布稳定标识格式无效", 422) from None
        canvas = self.canvas_dao.source_canvas(source_key)
        if canvas is None:
            raise NotFound("画布不存在或当前账号无权访问")
        identity = (canvas.id, canvas.project_id, canvas.source_project_id)
        if canvas.source_project_id is None:
            raise WorkflowError("canvas_character_source_required", "画布尚未绑定可信业务项目", 409)
        if canvas.source_project_id == canvas.project_id:
            raise WorkflowError("canvas_binding_invalid", "画布项目不能作为自身业务来源", 409)
        for project_id in sorted({canvas.project_id, canvas.source_project_id}):
            require_project(self.session, project_id, lock=True)
            project = self.canvas_dao.project(project_id, lock=True)
            if project is None:
                raise NotFound("画布所属或来源项目不存在或当前账号无权访问")
            expected_mode = (
                "standard" if project_id == canvas.source_project_id else "infinite_canvas"
            )
            if project.workspace_mode != expected_mode:
                raise WorkflowError("canvas_binding_invalid", "画布绑定的项目模式不匹配", 409)
        canvas = self.canvas_dao.source_canvas(source_key, lock=True)
        if canvas is None:
            raise NotFound("画布不存在或已归档")
        if (canvas.id, canvas.project_id, canvas.source_project_id) != identity:
            raise WorkflowError("canvas_binding_changed", "画布来源绑定已变化，请重新读取", 409)
        return canvas

    @staticmethod
    def _context(canvas: ProjectCanvas) -> dict:
        return {
            "canvas_key": canvas.source_key,
            "canvas_row_version": canvas.row_version,
            "source_project_id": canvas.source_project_id,
        }

    def _item(
        self, relation: ProjectAsset, asset: Asset, requested_version_id: int | None = None
    ) -> CanvasCharacterCatalogItemRead:
        versions = AssetVersionService(self.session)
        try:
            snapshot = (
                versions.current_snapshot_locked(asset)
                if requested_version_id is None
                else versions.snapshot_locked(asset, requested_version_id)
            )
        except ValidationError:
            raise Conflict("角色版本快照不符合保存合同") from None
        version = self.versions.current(
            asset.id,
            asset.current_version_id if requested_version_id is None else requested_version_id,
        )
        if version is None:
            raise NotFound("角色当前作品版本不存在")
        representations = []
        for reference in self.versions.references(asset.id, version.id):
            if reference.purpose != "representation":
                continue
            media = self.catalog.media(reference.media_id)
            if media is None or scope_of(self.session, media) != scope_of(self.session, asset):
                raise NotFound("角色表示图不存在或不属于角色范围")
            representations.append(
                CanvasCharacterRepresentationRead(
                    reference_id=reference.id,
                    role=reference.role,
                    ordinal=reference.ordinal,
                    media_id=media.id,
                    format_code=media.format_code,
                    mime_type=media.format_code if media.format_code.startswith("image/") else None,
                    original_name=media.original_name,
                    byte_size=media.byte_size,
                    width=media.width,
                    height=media.height,
                )
            )
        from .canvas_character_voice import read_canvas_voice_sample

        voice_status, voice_sample = read_canvas_voice_sample(
            self.catalog, self.settings, relation.project_id, asset
        )
        flat = snapshot["asset"]
        return CanvasCharacterCatalogItemRead(
            asset_id=asset.id,
            asset_row_version=asset.row_version,
            current_version_id=asset.current_version_id,
            version_id=version.id,
            version=version.version,
            position=relation.position,
            name=flat["name"],
            label=flat["label"],
            description=flat["description"],
            prompt=flat["prompt"],
            tags=flat["tags"],
            definition=snapshot["definition"],
            definition_origin=snapshot["definition_origin"],
            representations=representations,
            voice_status=voice_status,
            voice_sample=voice_sample,
        )

    def list(self, source_key: str) -> CanvasCharacterCatalogRead:
        _ = self.actor_id
        with self._transaction(read_only=True):
            canvas = self._bound_canvas(source_key)
            relations = self.catalog.relations(canvas.source_project_id)
            assets = {}
            # 固定顺序持角色锁；ProjectAsset 不能代替角色自身的可见性。
            for relation in sorted(relations, key=lambda row: row.asset_id):
                asset = self.catalog.locked_asset(relation.asset_id)
                if asset is None:
                    raise NotFound("项目关联的素材不存在或当前账号无权访问")
                assets[asset.id] = asset
            return CanvasCharacterCatalogRead(
                **self._context(canvas),
                items=[
                    self._item(relation, assets[relation.asset_id])
                    for relation in relations
                    if assets[relation.asset_id].kind == "character"
                ],
            )

    def get(
        self, source_key: str, asset_id: int, *, requested_version_id: int | None = None
    ) -> CanvasCharacterCatalogDetailRead:
        _ = self.actor_id
        asset_id = parse_identifier(asset_id)
        if requested_version_id is not None:
            requested_version_id = parse_identifier(requested_version_id)
        with self._transaction(read_only=True):
            canvas = self._bound_canvas(source_key)
            relation = self.catalog.relation(canvas.source_project_id, asset_id)
            if relation is None:
                raise NotFound("角色未关联到该来源项目")
            asset = self.catalog.locked_asset(asset_id)
            if asset is None or asset.kind != "character":
                raise NotFound("角色不存在或当前账号无权访问")
            return CanvasCharacterCatalogDetailRead(
                **self._context(canvas), item=self._item(relation, asset, requested_version_id)
            )
