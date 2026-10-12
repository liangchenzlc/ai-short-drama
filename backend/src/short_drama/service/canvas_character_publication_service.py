"""角色作品发布门禁：真实当前版本、双项目授权及已归一化封面；无外部 I/O。"""

from typing import TYPE_CHECKING

from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.dao.canvas_resource_dao import CanvasResourceDAO
from short_drama.db.access import require_project
from short_drama.domain import MediaFile, ProjectCanvas
from short_drama.domain.canvas import CanvasNode
from short_drama.schemas.canvas import CanvasDocument

from .canvas_document import (
    CHARACTER_PRIVATE_NESTED,
    CHARACTER_UNVERIFIED_FIELDS,
    SHARED_METADATA,
    _split_nested,
    character_node,
    has_character_nodes,
    resource_identifier,
)
from .canvas_media_locators import canonicalize_canvas_media

if TYPE_CHECKING:
    from .canvas_service import CanvasService


class CanvasPreflightRevisionChanged(WorkflowError):
    """范围未变而版本竞争；默认仍拒绝，仅任务列表与详情可重新预检。"""

    def __init__(self) -> None:
        super().__init__(
            "canvas_binding_changed", "批量读取期间画布或可信来源已变化，请重新读取", 409
        )


class CanvasCharacterPublicationService:
    def __init__(self, canvases: "CanvasService") -> None:
        self.canvases = canvases
        self.session = canvases.session

    def lock_existing_canvases(self, originals: list[ProjectCanvas]) -> dict[int, ProjectCanvas]:
        """批量物化先锁全部可信项目；不按各画布分别排序而制造倒序。"""
        _ = self.canvases.actor_id
        identities = {}
        modes = {}
        for original in originals:
            identities[original.id] = (
                original.project_id,
                original.source_project_id,
                original.row_version,
            )
            if modes.get(original.project_id, "infinite_canvas") != "infinite_canvas":
                raise WorkflowError("canvas_binding_invalid", "业务来源不能作为画布所属项目", 409)
            modes[original.project_id] = "infinite_canvas"
            if any(
                character_node(node.content_json)
                for node in self.canvases.canvas_dao.children(CanvasNode, original.id, active=True)
            ):
                source = original.source_project_id
                if source is None or source == original.project_id:
                    raise WorkflowError("canvas_binding_invalid", "已共享角色缺少可信业务来源", 409)
                if modes.get(source, "standard") != "standard":
                    raise WorkflowError("canvas_binding_invalid", "角色来源不是标准业务项目", 409)
                modes[source] = "standard"
        for identifier in sorted(modes):
            require_project(self.session, identifier, lock=True)
            project = self.canvases.canvas_dao.project(identifier, lock=True)
            if project is None or project.workspace_mode != modes[identifier]:
                raise WorkflowError("canvas_binding_invalid", "角色批量物化的项目范围已变化", 409)
        result = {}
        for identifier, identity in sorted(identities.items()):
            canvas = self.canvases.canvas_dao.canvas(identity[0], identifier, lock=True)
            if canvas is None or (canvas.project_id, canvas.source_project_id) != identity[:2]:
                raise WorkflowError(
                    "canvas_binding_changed", "批量读取期间画布或可信来源已变化，请重新读取", 409
                )
            if canvas.row_version != identity[2]:
                raise CanvasPreflightRevisionChanged()
            result[identifier] = canvas
        return result

    def lock_canvas(self, original: ProjectCanvas) -> ProjectCanvas:
        _ = self.canvases.actor_id
        identity = (original.id, original.project_id, original.source_project_id)
        if original.source_project_id is None:
            raise WorkflowError(
                "canvas_character_source_required", "角色作品需要画布的可信业务项目绑定", 409
            )
        if original.project_id == original.source_project_id:
            raise WorkflowError("canvas_binding_invalid", "画布不能作为自身业务来源", 409)
        for identifier in sorted({original.project_id, original.source_project_id}):
            require_project(self.session, identifier, lock=True)
            project = self.canvases.canvas_dao.project(identifier, lock=True)
            if project is None:
                raise NotFound("角色发布的所属或来源项目不存在或当前账号无权访问")
            expected = "standard" if identifier == original.source_project_id else "infinite_canvas"
            if project.workspace_mode != expected:
                raise WorkflowError("canvas_binding_invalid", "角色发布的项目模式不匹配", 409)
        canvas = self.canvases.canvas_dao.canvas(identity[1], identity[0], lock=True)
        if canvas is None:
            raise NotFound("角色发布画布不存在或当前账号无权访问")
        if (canvas.id, canvas.project_id, canvas.source_project_id) != identity:
            raise WorkflowError("canvas_binding_changed", "画布可信业务绑定已变化，请重新读取", 409)
        return canvas

    @staticmethod
    def _identifier(metadata: dict, field: str) -> int:
        value = metadata.get(field)
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
            raise WorkflowError("canvas_character_invalid", "角色身份必须是十进制字符串", 422)
        if value.startswith("0") or len(value) > 20 or int(value) > 2**64 - 1:
            raise WorkflowError("canvas_character_invalid", "角色身份超出有效范围", 422)
        return int(value)

    def _previous_covers(self, canvas: ProjectCanvas) -> set[tuple[str, str, int]]:
        result = set()
        for node in self.canvases.canvas_dao.children(CanvasNode, canvas.id, active=True):
            metadata = node.content_json.get("metadata") or {}
            if metadata.get("workflowKind") != "character":
                continue
            cover = resource_identifier(metadata.get("characterCoverUrl"))
            if cover is not None:
                result.add(
                    (metadata.get("characterAssetId"), metadata.get("characterVersionId"), cover)
                )
        return result

    def validate(
        self, canvas: ProjectCanvas, document: CanvasDocument | dict, *, shared=False
    ) -> None:
        if not has_character_nodes(document):
            return
        # 共享作品写回前核验真实 binding 与 Actor；同一事务回滚保护此前快照及新画布写入。
        canvas = self.lock_canvas(canvas)
        from .canvas_character_catalog_service import CanvasCharacterCatalogService

        catalog = CanvasCharacterCatalogService(self.session)
        raw = (
            document.model_dump(mode="json", by_alias=True, exclude_unset=True)
            if isinstance(document, CanvasDocument)
            else document
        )
        original_covers = {
            node["id"]: (node.get("metadata") or {}).get("characterCoverUrl")
            for node in raw.get("nodes", [])
        }
        raw = canonicalize_canvas_media(raw)
        nodes = [node for node in raw.get("nodes", []) if character_node(node)]
        if shared:
            for node in nodes:
                if node["metadata"].get("characterCoverUrl") != original_covers[node["id"]]:
                    raise WorkflowError(
                        "canvas_character_cover_not_persisted",
                        "共享角色封面必须使用规范资源定位",
                        422,
                    )
                for key, value in node["metadata"].items():
                    public, _ = _split_nested(
                        value, CHARACTER_PRIVATE_NESTED if key == "characterDefinition" else None
                    )
                    if (
                        key not in SHARED_METADATA
                        or key in CHARACTER_UNVERIFIED_FIELDS
                        or public != value
                    ):
                        raise WorkflowError(
                            "canvas_character_private_publication",
                            "角色共享图不能包含私人参数，请先完成作者状态拆分",
                            422,
                        )
        items = {}
        # 多角色共享稳定锁顺序，不根据前端节点排列获取 Asset 锁。
        for identifier in sorted(
            {self._identifier(node["metadata"], "characterAssetId") for node in nodes}
        ):
            relation = catalog.catalog.relation(canvas.source_project_id, identifier)
            asset = catalog.catalog.locked_asset(identifier)
            if relation is None or asset is None or asset.kind != "character":
                raise NotFound("角色未关联到可信业务来源或当前账号无权访问")
            items[identifier] = catalog._item(relation, asset)
        previous = self._previous_covers(canvas)
        resources = CanvasResourceDAO(self.session)
        for node in nodes:
            metadata = node["metadata"]
            if node.get("type") != "text" or metadata.get("workflowKind") != "character":
                raise WorkflowError("canvas_character_invalid", "角色作品必须使用源角色卡节点", 422)
            identifier = self._identifier(metadata, "characterAssetId")
            expected_version = self._identifier(metadata, "characterVersionId")
            item = items[identifier]
            if expected_version != int(item.current_version_id):
                raise WorkflowError(
                    "canvas_character_version_changed", "角色当前版本已变化，请重新读取角色", 409
                )
            if metadata.get("characterVersionPolicy") != "current":
                raise WorkflowError(
                    "canvas_character_version_policy_unsupported", "当前只支持真实当前角色版本", 422
                )
            definition = metadata.get("characterDefinition")
            public, _ = _split_nested(definition, CHARACTER_PRIVATE_NESTED)
            expected, _ = _split_nested(item.definition, CHARACTER_PRIVATE_NESTED)
            aliases = item.definition.get("aliases", [])
            aliases = (
                [value for value in aliases if isinstance(value, str)]
                if isinstance(aliases, list)
                else []
            )
            if (
                not isinstance(definition, dict)
                or public != expected
                or metadata.get("characterName") != item.name
                or metadata.get("characterAliases", []) != aliases
            ):
                raise WorkflowError(
                    "canvas_character_work_changed", "角色卡作品与当前不可变版本不一致", 409
                )
            cover_url = metadata.get("characterCoverUrl")
            if cover_url in {None, ""}:
                continue
            cover_id = resource_identifier(cover_url)
            target = resources.resource(cover_id, lock=True)
            if (
                not isinstance(target, MediaFile)
                or not isinstance(
                    resources.resource_map({cover_id}, lock=True).get(cover_id), MediaFile
                )
                or target.project_id != canvas.project_id
                or not target.format_code.startswith("image/")
                or target.byte_size is None
                or target.byte_size <= 0
                or target.created_by is None
                or target.created_at is None
                or target.updated_at is None
                or target.published_at is None
                and target.created_by != self.canvases.actor_id
            ):
                raise NotFound("角色封面不是本画布可发布的真实图片资源，请先归一化")
            # 已核验作品可由协作者继续保存；不借共享项目读取作者的私人复制回执。
            identity = (str(identifier), str(expected_version), cover_id)
            if identity in previous:
                continue
            real_slots = {int(slot.media_id) for slot in item.representations}
            if cover_id in real_slots:
                continue
            ancestors = resources.copy_ancestors({cover_id}, lock=True).get(cover_id, set())
            if not ancestors.intersection(real_slots):
                raise WorkflowError(
                    "canvas_character_cover_mismatch",
                    "角色封面不是当前真实表示槽位的已完成副本，请先归一化",
                    409,
                )
