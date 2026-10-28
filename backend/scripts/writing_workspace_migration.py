"""Add the single story-outline document and normalize published current scripts."""

import argparse
from pathlib import Path

from sqlalchemy import inspect, select, update
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateTable

from short_drama.core.config import Settings
from short_drama.core.time import utcnow
from short_drama.db.session import build_engine
from short_drama.domain import Project, ProjectScript, ProjectStoryOutline
from short_drama.domain.agent import AgentArtifact


def migrate(engine):
    table = ProjectStoryOutline.__table__
    with engine.begin() as connection:
        table.create(connection, checkfirst=True)
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
    target = (
        Path(__file__).resolve().parents[2]
        / "docs/database/migrations/2026-10-27-writing-workspace"
    )
    target.mkdir(parents=True, exist_ok=True)
    ddl = str(CreateTable(ProjectStoryOutline.__table__).compile(dialect=dialect())).strip() + ";\n"
    (target / "001_story_outline.sql").write_text(ddl, encoding="utf-8", newline="\n")
    if args.apply:
        engine = build_engine(Settings())
        count = migrate(engine)
        print(
            f"Writing workspace migrated in {engine.url.database}: outline table ready; "
            f"{count} published current scripts normalized."
        )
        engine.dispose()
    else:
        print("DDL exported. Use --apply to add the table and normalize published current scripts.")


if __name__ == "__main__":
    main()
