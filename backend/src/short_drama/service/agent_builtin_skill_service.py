"""Explicit system publication with verified resources before a short SQL transaction."""

from collections.abc import Sequence

from sqlalchemy.orm import Session, sessionmaker

from short_drama.agent.skill.packages import BuiltinSkillPackage, builtin_skill_packages
from short_drama.core.config import Settings
from short_drama.core.exceptions import Conflict
from short_drama.domain.agent_skill import AgentSkillVersion
from short_drama.service.agent_skill_package import package_digest, validate_package_size
from short_drama.service.agent_skill_resource_service import (
    lock_builtin_catalog,
    validate_staged_resources_locked,
)
from short_drama.service.agent_skill_service import (
    BUILTIN_NAMES,
    AgentSkillService,
    current_version,
    skill_digest,
)
from short_drama.service.base import utcnow
from short_drama.storage.minio import MinioStorage
from short_drama.utils.snowflake import next_id


def publish_builtin_skills(
    factory: sessionmaker[Session],
    settings: Settings,
    storage: MinioStorage,
    packages: Sequence[BuiltinSkillPackage] | None = None,
) -> dict:
    packages = builtin_skill_packages() if packages is None else packages
    library = AgentSkillService(None, settings, storage)
    prepared = []
    for package in packages:
        if package.key not in BUILTIN_NAMES:
            raise ValueError(f"Unregistered builtin Skill: {package.key}")
        metadata = library._metadata(package.content)
        if metadata["issues"]:
            raise ValueError(f"Incompatible builtin Skill: {package.key}: {metadata['issues']}")
        metadata["instructions_checksum_sha256"] = skill_digest(package.content.instructions)
        manifest = [
            {
                "path": item.path,
                "kind": item.kind,
                "byte_size": len(item.content.encode("utf-8")),
                "checksum_sha256": skill_digest(item.content),
            }
            for item in package.content.resources
        ]
        validate_package_size(metadata, package.content.instructions, manifest)
        checksum = package_digest(metadata, package.content.instructions, manifest)
        with factory.begin() as session:
            original = current_version(session, package.key, package.content_version)
            if original is not None:
                if original.owner_user_id is not None or original.checksum_sha256 != checksum:
                    raise Conflict(f"内置 Skill {package.key} 的此版本已发布，修改内容须增加版本")
                manifest = list(original.resources)
        prepared.append((package, metadata, manifest, original is not None))
    staged, uploaded = [], {}
    for package, metadata, manifest, existing_version in prepared:
        if existing_version:
            for resource in manifest:
                library._resource_bytes(resource)
        else:
            manifest = library._store_resources(
                "builtin", package.content.resources, staged=uploaded
            )
        staged.append((package, metadata, manifest))
    published, existing = [], []
    with factory.begin() as session:
        # The explicit 005 migration seeds this immutable row. It serializes
        # publishers without holding a SQL connection during resource I/O.
        lock_builtin_catalog(session)
        for package, metadata, manifest in staged:
            checksum = package_digest(metadata, package.content.instructions, manifest)
            original = current_version(session, package.key, package.content_version)
            receipt = {
                "id": package.key,
                "content_version": str(package.content_version),
                "package_checksum": checksum,
                "resource_count": len(manifest),
            }
            if original is not None:
                if original.owner_user_id is not None or original.checksum_sha256 != checksum:
                    raise Conflict(f"内置 Skill {package.key} 的此版本已发布，修改内容须增加版本")
                existing.append(receipt)
                continue
            validate_staged_resources_locked(session, manifest)
            session.add(
                AgentSkillVersion(
                    id=next_id(),
                    skill_key=package.key,
                    skill_id=None,
                    owner_user_id=None,
                    content_version=package.content_version,
                    package_metadata=metadata,
                    instructions=package.content.instructions,
                    resources=manifest,
                    checksum_sha256=checksum,
                    created_at=utcnow(),
                )
            )
            published.append(receipt)
    return {"published": published, "existing": existing}
