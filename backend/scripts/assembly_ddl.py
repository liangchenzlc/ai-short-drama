"""Emit the new tables in the project's canonical CREATE-only SQL style."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.mysql import dialect

from short_drama.domain import EpisodeAssembly, EpisodeAssemblyClip, EpisodeRenderJob


def tables_ddl(models=(EpisodeAssembly, EpisodeAssemblyClip, EpisodeRenderJob)):
    statements = []
    for model in models:
        table = model.__table__
        lines = []
        for column in table.columns:
            kind = str(column.type.compile(dialect=dialect())).replace("INTEGER", "INT")
            line = f"  `{column.name}` {kind} {'NULL' if column.nullable else 'NOT NULL'}"
            if column.server_default is not None:
                default = str(column.server_default.arg)
                if column.type.python_type is str and not default.startswith("'"):
                    default = f"'{default}'"
                line += f" DEFAULT {default}"
            if column.comment:
                line += " COMMENT '" + column.comment.replace("'", "''") + "'"
            lines.append(line)
        primary = ", ".join(f"`{c.name}`" for c in table.primary_key.columns)
        lines.append(f"  PRIMARY KEY ({primary})")
        for constraint in sorted(table.constraints, key=lambda c: c.name or ""):
            name = constraint.name
            if isinstance(constraint, UniqueConstraint):
                cols = ", ".join(f"`{c.name}`" for c in constraint.columns)
                lines.append(f"  UNIQUE KEY `{name}` ({cols})")
            elif isinstance(constraint, ForeignKeyConstraint):
                cols = ", ".join(f"`{c.parent.name}`" for c in constraint.elements)
                refs = ", ".join(f"`{c.column.name}`" for c in constraint.elements)
                target = constraint.elements[0].column.table.name
                lines.append(
                    f"  CONSTRAINT `{name}` FOREIGN KEY ({cols}) REFERENCES `{target}` ({refs}) "
                    "ON DELETE RESTRICT ON UPDATE RESTRICT"
                )
            elif isinstance(constraint, CheckConstraint):
                lines.append(f"  CONSTRAINT `{name}` CHECK ({constraint.sqltext})")
        for index in sorted(table.indexes, key=lambda c: c.name):
            cols = ", ".join(f"`{c.name}`" for c in index.columns)
            lines.append(f"  KEY `{index.name}` ({cols})")
        statements.append(
            f"CREATE TABLE `{table.name}` (\n"
            + ",\n".join(lines)
            + "\n) ENGINE=InnoDB ROW_FORMAT=DYNAMIC DEFAULT CHARSET=utf8mb4 "
            + f"COLLATE=utf8mb4_0900_ai_ci COMMENT='{table.comment or ''}';"
        )
    return "\n\n".join(statements)


if __name__ == "__main__":
    from pathlib import Path

    canonical = next((Path(__file__).resolve().parents[2] / "docs").glob("*/schema.mysql8.sql"))
    source = canonical.read_text(encoding="utf-8")
    start = (
        source.index("CREATE TABLE episode_assemblies")
        if "CREATE TABLE episode_assemblies" in source
        else source.index("CREATE TABLE `episode_assemblies`")
    )
    canonical.write_text(source[:start] + tables_ddl() + "\n", encoding="utf-8")
    migration = canonical.parent / "migrations/2026-09-28-episode-assembly/001_episode_assembly.sql"
    migration.write_text(
        "-- Additive upgrade, use the Python runner to resume partial application.\n"
        "ALTER TABLE media_files ADD COLUMN video_metadata JSON NULL "
        "COMMENT 'ffprobe actual video metadata', ADD CONSTRAINT ck_media_video_metadata "
        "CHECK (video_metadata IS NULL OR JSON_TYPE(video_metadata) = 'OBJECT');\n\n"
        + tables_ddl()
        + "\n",
        encoding="utf-8",
    )
