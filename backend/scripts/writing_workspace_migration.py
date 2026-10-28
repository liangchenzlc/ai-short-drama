"""规范已发布当前剧本的确认状态；退役的大纲表不再创建。"""

import argparse

from sqlalchemy import inspect, select, update

from short_drama.core.config import Settings
from short_drama.core.time import utcnow
from short_drama.db.session import build_engine
from short_drama.domain import Project, ProjectScript
from short_drama.domain.assistant.artifacts.models import AgentArtifact


def migrate(engine):
    # 大纲表已退役；仅规范仍有效的剧本确认状态，不重建历史功能表。
    with engine.begin() as connection:
        statement = (
            select(Project.id, ProjectScript.id, ProjectScript.content)
            .join(ProjectScript, Project.editing_script_id == ProjectScript.id)
            .where(
                Project.workspace_mode == "standard",
                Project.archived_at.is_(None),
                ProjectScript.published_at.is_not(None),
            )
            .with_for_update()
        )
        if inspect(connection).has_table(AgentArtifact.__tablename__):
            statement = statement.where(
                ~select(AgentArtifact.id)
                .where(
                    AgentArtifact.script_id == ProjectScript.id, AgentArtifact.status != "applied"
                )
                .exists()
            )
        rows = connection.execute(statement).all()
        changed = 0
        for project_id, script_id, content in rows:
            confirmed = set(
                connection.scalars(
                    select(ProjectScript.id).where(
                        ProjectScript.project_id == project_id,
                        ProjectScript.state == "confirmed",
                    )
                )
            )
            if confirmed == ({script_id} if content.strip() else set()):
                continue
            now = utcnow()
            connection.execute(
                update(ProjectScript)
                .where(ProjectScript.project_id == project_id, ProjectScript.state == "confirmed")
                .values(state="unconfirmed", updated_at=now)
            )
            if content.strip():
                connection.execute(
                    update(ProjectScript)
                    .where(ProjectScript.id == script_id)
                    .values(state="confirmed", updated_at=now)
                )
            connection.execute(
                update(Project)
                .where(Project.id == project_id)
                .values(content_version=Project.content_version + 1, updated_at=now)
            )
            changed += 1
        return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        engine = build_engine(Settings())
        count = migrate(engine)
        print(
            f"Writing workspace migrated in {engine.url.database}: "
            f"{count} published current scripts normalized."
        )
        engine.dispose()
    else:
        print("Use --apply to normalize published current scripts; no retired tables are created.")


if __name__ == "__main__":
    main()
