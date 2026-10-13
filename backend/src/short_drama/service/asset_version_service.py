"""已采用角色作品的不可变版本；候选、私人模型和状态不形成内容版本。"""

import copy

from sqlalchemy.orm import Session

from short_drama.core.exceptions import BusinessError, Conflict, NotFound, WorkflowError
from short_drama.dao.asset_version_dao import AssetVersionDAO
from short_drama.db.asset_version_access import _append_context
from short_drama.domain import Asset, AssetVersion, AssetVersionMediaReference
from short_drama.schemas.asset_version import (
    snapshot_content_hash,
    snapshot_media_references,
    validate_snapshot,
)
from short_drama.schemas.base import UINT64_MAX
from short_drama.utils.snowflake import next_id

from .base import utcnow

_FLAT_FIELDS = ("kind", "name", "label", "description", "prompt", "tags", "scene_time")


def canonical_current(
    asset: Asset, *, previous: dict | None = None, source_audit: dict | None = None
) -> dict:
    """保持可信结构化定义及多视角，仅刷新当前入口拥有的平面、主图和参考槽。"""
    flat = {key: copy.deepcopy(getattr(asset, key)) for key in _FLAT_FIELDS}
    flat["media_id"] = str(asset.media_id) if asset.media_id is not None else None
    if previous is None:
        snapshot = {
            "schema_version": 1,
            "asset": flat,
            "definition": {"description": asset.description, "prompt": asset.prompt},
            "definition_origin": "legacy_current",
            "representations": (
                [{"role": "primary", "media_id": str(asset.media_id)}] if asset.media_id else []
            ),
        }
    else:
        snapshot = validate_snapshot(previous).model_dump(mode="json")
        old_media = snapshot["asset"]["media_id"]
        snapshot["asset"] = flat
        if snapshot["definition_origin"] == "legacy_current":
            snapshot["definition"] = {"description": asset.description, "prompt": asset.prompt}
        if old_media != flat["media_id"]:
            slots = snapshot["representations"]
            if flat["media_id"] is None:
                snapshot["representations"] = [row for row in slots if row["role"] != "primary"]
            elif not any(row["media_id"] == flat["media_id"] for row in slots):
                primary = next(
                    (i for i, row in enumerate(slots) if row["role"] == "primary"), len(slots)
                )
                retained = [row for row in slots if row["role"] != "primary"]
                retained.insert(
                    min(primary, len(retained)), {"role": "primary", "media_id": flat["media_id"]}
                )
                snapshot["representations"] = retained
    snapshot["generation_reference_ids"] = [str(mid) for mid in asset.reference_media_ids or []]
    snapshot["source_audit"] = copy.deepcopy(source_audit or {})
    return validate_snapshot(snapshot).model_dump(mode="json")


class AssetVersionService:
    def __init__(self, session: Session):
        self.session = session
        self.dao = AssetVersionDAO(session)

    def _require_transaction(self) -> None:
        if not self.session.in_transaction():
            raise BusinessError("角色版本服务需要调用者开启事务并持有角色锁")

    def _validate_media(self, asset: Asset, snapshot: dict) -> None:
        from short_drama.db.access import scope_of

        for slot in snapshot_media_references(snapshot):
            media = self.dao.media(int(slot["media_id"]))
            if media is None or scope_of(self.session, media) != scope_of(self.session, asset):
                raise NotFound("角色版本引用的媒体不属于角色范围")
            if not (media.format_code.startswith("image/") or media.format_code == "demo:image"):
                raise WorkflowError("invalid_asset_version_media", "角色版本媒体必须是图片", 422)

    def _stored_snapshot(self, asset: Asset, version: AssetVersion) -> dict:
        if version.asset_id != asset.id or not 1 <= version.source_row_version <= asset.row_version:
            raise Conflict("角色版本所属或来源编辑号不匹配")
        snapshot = validate_snapshot(version.snapshot_json).model_dump(mode="json")
        if version.content_hash != snapshot_content_hash(snapshot):
            raise Conflict("角色版本快照摘要不一致")
        actual = [
            {
                "purpose": row.purpose,
                "role": row.role,
                "ordinal": row.ordinal,
                "media_id": str(row.media_id),
            }
            for row in self.dao.references(asset.id, version.id)
        ]

        def key(row):
            return row["purpose"], row["ordinal"]

        if sorted(actual, key=key) != sorted(snapshot_media_references(snapshot), key=key):
            raise Conflict("角色版本媒体槽位与快照不一致")
        self._validate_media(asset, snapshot)
        return snapshot

    def current_snapshot_locked(self, asset: Asset) -> dict:
        """只读核验；未回填角色明确失败，不在 GET 或导入读取时补历史。"""
        self._require_transaction()
        if asset.kind != "character" or asset.current_version_id is None:
            raise Conflict("角色尚无有效当前作品版本")
        version = self.dao.current(asset.id, asset.current_version_id)
        if version is None or version.version != self.dao.latest_number(asset.id):
            raise Conflict("角色当前版本指针所属不匹配")
        snapshot = self._stored_snapshot(asset, version)
        if (
            snapshot_content_hash(canonical_current(asset, previous=snapshot))
            != version.content_hash
        ):
            raise Conflict("角色当前内容与不可变版本不一致")
        return copy.deepcopy(snapshot)

    def snapshot_locked(self, asset: Asset, version_id: int) -> dict:
        """读取明确指定的不可变作品；历史版本不要求等于角色当前平面内容。"""
        self._require_transaction()
        if asset.kind != "character":
            raise NotFound("角色作品版本不存在")
        if version_id == asset.current_version_id:
            return self.current_snapshot_locked(asset)
        version = self.dao.current(asset.id, version_id)
        if version is None:
            raise NotFound("指定的角色作品版本不存在")
        return copy.deepcopy(self._stored_snapshot(asset, version))

    def snapshot_for_copy_locked(self, asset: Asset) -> dict:
        """冻结复制来源；无历史的旧角色只记录实际当前值，不隐式补版。"""
        self._require_transaction()
        if asset.current_version_id is not None:
            return self.current_snapshot_locked(asset)
        if self.dao.latest_number(asset.id):
            raise Conflict("角色已有历史但当前指针为空，不能降级为旧角色快照")
        snapshot = canonical_current(asset)
        self._validate_media(asset, snapshot)
        return snapshot

    def verify_locked(self, asset: Asset) -> None:
        self.current_snapshot_locked(asset)

    def append_locked(
        self, asset: Asset, *, snapshot: dict | None = None, source_audit: dict | None = None
    ) -> AssetVersion | None:
        self._require_transaction()
        if asset.kind != "character":
            if asset.current_version_id is not None:
                raise Conflict("非角色不能持有作品版本指针")
            return None
        previous = None
        version = None
        latest = self.dao.latest_number(asset.id)
        if asset.current_version_id is not None:
            version = self.dao.current(asset.id, asset.current_version_id)
            if version is None or version.version != latest:
                raise Conflict("角色当前版本指针所属或顺序不匹配")
            previous = self._stored_snapshot(asset, version)
        elif latest:
            raise Conflict("角色已有历史但当前指针为空，不能伪造新初版")
        audit = source_audit if source_audit is not None else {"state": asset.state}
        if snapshot is None:
            snapshot = canonical_current(asset, previous=previous, source_audit=audit)
        else:
            snapshot = validate_snapshot(snapshot).model_dump(mode="json")
            expected = canonical_current(asset, previous=snapshot)
            if (
                snapshot["asset"] != expected["asset"]
                or snapshot["generation_reference_ids"] != expected["generation_reference_ids"]
            ):
                raise Conflict("显式角色快照与实际角色平面内容或生成参考不一致")
            if (
                snapshot["definition_origin"] == "legacy_current"
                and snapshot["definition"] != expected["definition"]
            ):
                raise Conflict("当前平面来源定义不匹配；完整结构化定义必须明确声明来源")
            if source_audit is not None:
                snapshot["source_audit"] = copy.deepcopy(source_audit)
        self._validate_media(asset, snapshot)
        digest = snapshot_content_hash(snapshot)
        if version is not None and version.content_hash == digest:
            return version
        if latest >= UINT64_MAX:
            raise Conflict("角色作品版本号已耗尽")
        actor = self.session.info.get("actor")
        version = AssetVersion(
            id=next_id(),
            asset_id=asset.id,
            version=latest + 1,
            source_row_version=asset.row_version,
            snapshot_json=copy.deepcopy(snapshot),
            content_hash=digest,
            created_at=utcnow(),
            created_by=actor.user_id if actor else None,
        )
        with _append_context(self.session, asset.id, version.id):
            self.dao.add(version)
            self.session.flush()
            for slot in snapshot_media_references(snapshot):
                self.dao.add(
                    AssetVersionMediaReference(
                        id=next_id(),
                        asset_id=asset.id,
                        version_id=version.id,
                        purpose=slot["purpose"],
                        role=slot["role"],
                        ordinal=slot["ordinal"],
                        media_id=int(slot["media_id"]),
                    )
                )
            self.session.flush()
            asset.current_version_id = version.id
            self.session.flush()
        return version
