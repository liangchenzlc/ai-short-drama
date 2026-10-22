"""Bounded uploads and explicit orphan collection coordinated with version publication."""

import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.domain.agent_skill import AgentSkillVersion
from short_drama.domain.collaboration import User
from short_drama.service.base import utcnow

STAGE_FORMAT = "%Y%m%dT%H%M%S%fZ"
MAX_UPLOAD_AGE = timedelta(hours=1)
_STAGED = re.compile(
    r"(?:^|/)agent-skills/(?:builtin|[1-9][0-9]*)/staged/"
    r"([0-9]{8}T[0-9]{12}Z)-[0-9a-f]{32}/[0-9a-f]{64}$"
)
_OWNED_KEY = re.compile(
    r"(builtin|[1-9][0-9]*)/(?:content/[0-9a-f]{64}|"
    r"staged/[0-9]{8}T[0-9]{12}Z-[0-9a-f]{32}/[0-9a-f]{64})$"
)


def database_now(session) -> datetime:
    return session.connection().scalar(select(func.utc_timestamp(6)))


def stage_object_key(owner, checksum: str) -> str:
    # An unpublished old object must never be reused while GC can select it.
    stamp = utcnow().strftime(STAGE_FORMAT)
    return f"agent-skills/{owner}/staged/{stamp}-{uuid4().hex}/{checksum}"


def staged_at(key: str) -> datetime | None:
    match = _STAGED.search(key)
    if match is None:
        return None
    try:
        return datetime.strptime(match[1], STAGE_FORMAT)
    except ValueError:
        return None


def validate_staged_resources_locked(session, resources: list[dict]) -> None:
    """Caller holds the same owner/catalog row lock used by orphan collection."""
    if not resources:
        return
    now = database_now(session)
    for item in resources:
        created = staged_at(item["object_name"])
        if created is None or not now - MAX_UPLOAD_AGE < created <= now + timedelta(minutes=5):
            raise WorkflowError(
                "agent_skill_upload_expired",
                f"Skill 资料“{item['path']}”的暂存上传已过期或时钟不一致，请以原请求重新保存",
                409,
            )


def lock_builtin_catalog(session) -> None:
    anchor = session.scalar(
        select(AgentSkillVersion.id)
        .where(
            AgentSkillVersion.skill_key == "novel.v1",
            AgentSkillVersion.content_version == 1,
            AgentSkillVersion.owner_user_id.is_(None),
        )
        .with_for_update()
    )
    if anchor is None:
        raise WorkflowError(
            "agent_skill_catalog_not_ready", "请先执行 Agent 005 迁移并回填旧内置版本", 503
        )


def collect_unreferenced_skill_resources(
    factory,
    settings,
    storage,
    *,
    apply: bool = False,
    grace_hours: int = 24,
    limit: int = 1000,
    namespace_prefix: str = "",
) -> dict:
    """Keep every immutable version; delete only old, known keys with no reference.

    Listing and exact-version removal run without a SQL connection. The reference
    snapshot shares the publisher's row lock. New uploads expire after one hour,
    before GC's minimum 24-hour window, so publication after this snapshot cannot
    resurrect a selected key. All writers must use this publication protocol.
    """
    if not 24 <= grace_hours <= 8760 or not 1 <= limit <= 10000:
        raise ValueError("Skill 回收宽限期须为 24–8760 小时，批次上限须为 1–10000")
    if namespace_prefix and (
        not namespace_prefix.endswith("/")
        or "\\" in namespace_prefix
        or any(part in {"", ".", ".."} for part in namespace_prefix[:-1].split("/"))
    ):
        raise ValueError("Skill 回收命名空间不正确")
    prefix = namespace_prefix + "agent-skills/"
    bucket = settings.minio_image_bucket
    grouped = defaultdict(list)
    report = {
        "apply": apply,
        "grace_hours": grace_hours,
        "limit": limit,
        "scanned": 0,
        "protected": 0,
        "recent": 0,
        "ignored": 0,
        "candidates": [],
        "removed": [],
        "errors": [],
    }
    try:
        for item in storage.iter_objects(bucket, prefix, include_versions=True):
            report["scanned"] += 1
            key = item.object_name
            match = (
                _OWNED_KEY.fullmatch(key.removeprefix(prefix)) if key.startswith(prefix) else None
            )
            if match is None or item.last_modified is None:
                report["ignored"] += 1
                continue
            grouped[match[1]].append(item)
    except (BusinessError, OSError) as error:
        raise WorkflowError(
            "agent_skill_gc_storage_unavailable", f"列举 Skill 暂存资料失败：{error}", 503
        ) from error
    for owner, objects in grouped.items():
        with factory.begin() as session:
            if owner == "builtin":
                lock_builtin_catalog(session)
                condition = AgentSkillVersion.owner_user_id.is_(None)
            else:
                session.scalar(select(User.id).where(User.id == int(owner)).with_for_update())
                condition = AgentSkillVersion.owner_user_id == int(owner)
            cutoff = database_now(session) - timedelta(hours=grace_hours)
            protected = {
                (resource["bucket"], resource["object_name"])
                for manifest in session.scalars(
                    select(AgentSkillVersion.resources).where(condition)
                )
                for resource in manifest
            }
        for item in objects:
            key = item.object_name
            if (bucket, key) in protected:
                report["protected"] += 1
                continue
            modified = item.last_modified
            if modified.tzinfo is not None:
                modified = modified.astimezone(UTC).replace(tzinfo=None)
            created = staged_at(key)
            if modified >= cutoff or (created is not None and created >= cutoff):
                report["recent"] += 1
                continue
            if len(report["candidates"]) >= limit:
                continue
            candidate = {
                "bucket": bucket,
                "object_name": key,
                "version_id": item.version_id,
                "byte_size": item.size,
            }
            report["candidates"].append(candidate)
            if apply:
                try:
                    storage.remove(bucket, key, version_id=item.version_id)
                except (BusinessError, OSError) as error:
                    report["errors"].append(
                        {**candidate, "message": f"回收 Skill 资料失败：{error}"}
                    )
                else:
                    report["removed"].append(candidate)
    return report
