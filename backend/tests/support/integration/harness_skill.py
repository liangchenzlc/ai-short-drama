"""Historical Skill fixtures keep public script intake prohibited."""

import hashlib
import io

from sqlalchemy import select

from short_drama.core.time import utcnow
from short_drama.domain.agent_context import AgentSkill
from short_drama.domain.agent_skill import AgentSkillVersion
from short_drama.service.agent_skill_package import package_digest
from short_drama.service.agent_skill_service import AgentSkillService
from short_drama.utils.snowflake import next_id


def seed_legacy_script(workspace, skill, actor):
    """Append a historical package fixture without rewriting an immutable version."""
    factory, settings, _model, storage = workspace
    data = b"# Read-only checklist: two characters, three shots. Never execute.\n"
    stored = storage.put(
        settings.minio_image_bucket,
        f"legacy-skills/{skill.id}/check.py",
        io.BytesIO(data),
        len(data),
        "text/plain; charset=utf-8",
    )
    with factory.begin() as session:
        session.info["actor"] = actor
        version = session.scalar(
            select(AgentSkillVersion).where(
                AgentSkillVersion.skill_key == skill.id,
                AgentSkillVersion.content_version == skill.content_version,
            )
        )
        resources = [
            *version.resources,
            {
                "path": "scripts/check.py",
                "kind": "script",
                "byte_size": len(data),
                "checksum_sha256": hashlib.sha256(data).hexdigest(),
                "bucket": stored.bucket,
                "object_name": stored.object_name,
            },
        ]
        row = session.get(AgentSkill, int(skill.id))
        row.content_version += 1
        row.row_version += 1
        session.add(
            AgentSkillVersion(
                id=next_id(),
                skill_key=skill.id,
                skill_id=row.id,
                owner_user_id=row.owner_user_id,
                content_version=row.content_version,
                package_metadata=version.package_metadata,
                instructions=version.instructions,
                resources=resources,
                checksum_sha256=package_digest(
                    version.package_metadata, version.instructions, resources
                ),
                created_at=utcnow(),
            )
        )
    with factory() as session:
        session.info["actor"] = actor
        return AgentSkillService(session, settings, storage).detail(skill.id)
