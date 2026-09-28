"""Apply only the additive shot-video migration to the configured local database."""

from pathlib import Path

from sqlalchemy import inspect

from short_drama.core.config import Settings
from short_drama.db.session import build_engine


def main():
    engine = build_engine(Settings())
    migration = (
        Path(__file__).resolve().parents[2]
        / "docs/数据库模型/migrations/2026-09-28-shot-video/001_shot_video.sql"
    )
    source = "\n".join(
        line
        for line in migration.read_text(encoding="utf-8").splitlines()
        if not line.startswith("--")
    )
    statements = [statement.strip() for statement in source.split(";") if statement.strip()]
    expected = [
        ("shot_scripts", {"video_prompt", "video_settings"}),
        ("shot_videos", {"context_hash", "first_frame_media_id"}),
    ]
    try:
        with engine.begin() as connection:
            inspector = inspect(connection)
            pending = []
            for statement, (table, names) in zip(statements, expected, strict=True):
                present = {item["name"] for item in inspector.get_columns(table)} & names
                if present == names:
                    continue
                if present:
                    raise RuntimeError(f"Partial migration on {table}; inspect before continuing")
                pending.append(statement)
            for statement in pending:
                connection.exec_driver_sql(statement)
            print(f"Shot video migration complete: {len(pending)} additive ALTER statements.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
