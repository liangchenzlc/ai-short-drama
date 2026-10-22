"""显式保存旧角色的当前快照；默认纯只读，不猜旧历史、项目来源或声音绑定。"""

import argparse
import json
import logging
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.core.exceptions import BusinessError
from short_drama.core.logging import configure_logging
from short_drama.db.session import AppSession, build_engine
from short_drama.domain import (
    AIGenerationRecord,
    Asset,
    AssetVersion,
    AssetVersionMediaReference,
    AsyncTask,
    MediaFile,
    ResourceImport,
    User,
)
from short_drama.schemas.asset_version import (
    snapshot_content_hash,
    snapshot_media_references,
    validate_snapshot,
)
from short_drama.service.asset_version_service import AssetVersionService, canonical_current

logger = logging.getLogger(__name__)


def _require_system_session(session: Session) -> None:
    if session.info.get("actor") or session.info.get("legacy_user_id"):
        raise ValueError("角色版本维护必须使用独立系统 Session")
    if session.new or session.dirty or session.deleted:
        raise ValueError("角色版本维护不能复用含未提交业务修改的 Session")
    if not session.in_transaction():
        raise ValueError("角色版本维护需要调用者显式开启事务")


def _require_no_active_writers(session: Session) -> None:
    for model, statuses in (
        (AsyncTask, {"queued", "running"}),
        (AIGenerationRecord, {"sent", "unknown"}),
        (ResourceImport, {"pending", "copying"}),
    ):
        if session.scalar(select(model.id).where(model.status.in_(statuses)).limit(1)):
            raise ValueError(f"{model.__tablename__} 仍有活动或未知任务；停止写入后再维护")


def _validate_media(session: Session, asset: Asset, snapshot: dict) -> None:
    for slot in snapshot_media_references(snapshot):
        media = session.get(MediaFile, int(slot["media_id"]))
        if media is None:
            raise ValueError("missing_media")
        if (media.scope_user_id, media.project_id) != (None, asset.project_id):
            raise ValueError("media_scope_mismatch")
        if not (media.format_code.startswith("image/") or media.format_code == "demo:image"):
            raise ValueError("media_type_mismatch")


def _error_code(error: Exception) -> str:
    # Summaries may identify a record and controlled code, never dump content/credentials.
    code = getattr(error, "code", None)
    if isinstance(code, str):
        return code
    if isinstance(error, ValueError) and str(error) in {
        "missing_media",
        "media_scope_mismatch",
        "media_type_mismatch",
        "missing_author",
    }:
        return str(error)
    return type(error).__name__


def backfill(session: Session, *, apply: bool = False) -> dict:
    """调用者负责 commit/rollback；失败角色回滚，不修已有历史或改变 source 关联。"""
    _require_system_session(session)
    _require_no_active_writers(session)
    counts, failures = Counter(), []
    service = AssetVersionService(session)
    rows = list(
        session.scalars(
            select(Asset).where(Asset.kind == "character").order_by(Asset.id).with_for_update()
        )
    )
    for asset in rows:
        counts["characters"] += 1
        try:
            if asset.current_version_id is not None:
                service.verify_locked(asset)
                counts["unchanged"] += 1
                continue
            if session.scalar(
                select(AssetVersion.id).where(AssetVersion.asset_id == asset.id).limit(1)
            ):
                raise ValueError("已有历史但当前指针为空；必须人工核对，不能再造初版")
            for author in (asset.created_by, asset.updated_by):
                if author is not None and session.get(User, author) is None:
                    raise ValueError("missing_author")
            snapshot = canonical_current(
                asset,
                source_audit={
                    "migration": "current_snapshot_only",
                    "source_row_version": str(asset.row_version),
                    "source_created_by": str(asset.created_by) if asset.created_by else None,
                    "source_created_at": asset.created_at.isoformat() if asset.created_at else None,
                    "source_updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
                    "state": asset.state,
                },
            )
            validate_snapshot(snapshot)
            _validate_media(session, asset, snapshot)
            if apply:
                with session.begin_nested():
                    service.append_locked(asset, snapshot=snapshot)
                    service.verify_locked(asset)
                counts["created"] += 1
            else:
                counts["would_create"] += 1
        except (ValueError, BusinessError) as error:
            # Continue only after the failed character's SAVEPOINT rolled back.
            if not session.is_active:
                raise
            counts["failed"] += 1
            failures.append({"asset_id": str(asset.id), "code": _error_code(error)})
    return {"applied": apply, "counts": dict(sorted(counts.items())), "failures": failures}


def verify(session: Session) -> dict:
    """纯只读核对当前、全部历史快照、槽位与媒体所属；不自动补任何数据。"""
    _require_system_session(session)
    counts, failures = Counter(), []
    service = AssetVersionService(session)
    for asset in session.scalars(select(Asset).order_by(Asset.id).with_for_update()):
        if asset.kind != "character":
            if asset.current_version_id is not None:
                failures.append({"asset_id": str(asset.id), "code": "noncharacter_pointer"})
            continue
        counts["characters"] += 1
        try:
            service.verify_locked(asset)
            versions = list(
                session.scalars(
                    select(AssetVersion)
                    .where(AssetVersion.asset_id == asset.id)
                    .order_by(AssetVersion.version)
                )
            )
            if [row.version for row in versions] != list(range(1, len(versions) + 1)):
                raise ValueError("角色历史版本号不连续")
            for version in versions:
                snapshot = validate_snapshot(version.snapshot_json).model_dump(mode="json")
                if version.content_hash != snapshot_content_hash(snapshot):
                    raise ValueError("历史角色版本内容摘要不一致")
                if not 1 <= version.source_row_version <= asset.row_version:
                    raise ValueError("历史角色来源编辑号无效")
                _validate_media(session, asset, snapshot)
                references = list(
                    session.scalars(
                        select(AssetVersionMediaReference).where(
                            AssetVersionMediaReference.asset_id == asset.id,
                            AssetVersionMediaReference.version_id == version.id,
                        )
                    )
                )
                actual = [
                    {
                        "purpose": row.purpose,
                        "role": row.role,
                        "ordinal": row.ordinal,
                        "media_id": str(row.media_id),
                    }
                    for row in references
                ]

                def key(row):
                    return row["purpose"], row["ordinal"]

                if sorted(actual, key=key) != sorted(snapshot_media_references(snapshot), key=key):
                    raise ValueError("历史角色媒体槽位与快照不一致")
                counts["versions"] += 1
                counts["media_references"] += len(references)
            counts["verified"] += 1
        except (ValueError, BusinessError) as error:
            counts["failed"] += 1
            failures.append({"asset_id": str(asset.id), "code": _error_code(error)})
    orphan_versions = session.scalar(
        select(AssetVersion.id)
        .outerjoin(Asset, Asset.id == AssetVersion.asset_id)
        .where((Asset.id.is_(None)) | (Asset.kind != "character"))
        .limit(1)
    )
    if orphan_versions is not None:
        failures.append({"version_id": str(orphan_versions), "code": "invalid_version_owner"})
    return {"applied": False, "counts": dict(sorted(counts.items())), "failures": failures}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="显式提交当前角色初版；要求写者已停")
    mode.add_argument("--verify", action="store_true", help="只核验数据，不补版、不改指针")
    args = parser.parse_args()
    configure_logging()
    engine = build_engine(Settings())
    try:
        with AppSession(engine, autoflush=False) as session, session.begin():
            result = verify(session) if args.verify else backfill(session, apply=args.apply)
        logger.info("角色版本数据检查：%s", json.dumps(result, ensure_ascii=False))
        return 1 if result["failures"] else 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
