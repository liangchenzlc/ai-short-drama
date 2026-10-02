"""Resumable audio schema expansion. Run with --apply only after taking a backup."""

import sys

from sqlalchemy import text

from short_drama.core.config import Settings
from short_drama.db.session import build_engine
from short_drama.domain import AIModelConfig, AsyncTask, MediaAsset, MediaFile
from short_drama.domain.episode_sound import EpisodeSound, ProjectVoiceDefaults, SoundMediaReference

try:
    from scripts.assembly_ddl import tables_ddl
except ModuleNotFoundError:
    from assembly_ddl import tables_ddl

MODELS = (EpisodeSound, ProjectVoiceDefaults, SoundMediaReference)
CHECKS = [
    (model.__table__.name, constraint.name, str(constraint.sqltext))
    for model in (AIModelConfig, AsyncTask, MediaAsset, MediaFile)
    for constraint in model.__table__.constraints
    if constraint.name
    in {
        "ck_ai_service_type",
        "ck_async_tasks_type",
        "ck_media_assets_type",
        "ck_media_format",
        "ck_media_duration",
    }
]


def ddl():
    return (
        "\n\n".join(
            f"ALTER TABLE `{table}` DROP CHECK `{name}`, "
            f"ADD CONSTRAINT `{name}` CHECK ({expression});"
            for table, name, expression in CHECKS
        )
        + "\n\n"
        + tables_ddl(MODELS)
        + "\n"
    )


def apply(engine):
    # Each ALTER is atomic in MySQL 8. Repeating it or resuming between tables is safe.
    with engine.begin() as connection:
        for table, name, expression in CHECKS:
            exists = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS "
                    "WHERE CONSTRAINT_SCHEMA=DATABASE() "
                    "AND TABLE_NAME=:table AND CONSTRAINT_NAME=:name"
                ),
                {"table": table, "name": name},
            )
            drop = f"DROP CHECK `{name}`, " if exists else ""
            connection.execute(
                text(f"ALTER TABLE `{table}` {drop}ADD CONSTRAINT `{name}` CHECK ({expression})")
            )
        for model in MODELS:
            model.__table__.create(connection, checkfirst=True)


if __name__ == "__main__":
    if "--apply" in sys.argv:
        engine = build_engine(Settings())
        try:
            apply(engine)
            print(
                "Audio schema ready. Configure storage, workers and fonts "
                "before enabling audio production."
            )
        finally:
            engine.dispose()
    else:
        print(ddl(), end="")
