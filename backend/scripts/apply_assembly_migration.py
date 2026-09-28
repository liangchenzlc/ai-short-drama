"""Add assembly tables and actual video metadata, safe to resume after partial DDL."""

from sqlalchemy import inspect

from short_drama.core.config import Settings
from short_drama.db.session import build_engine
from short_drama.domain import EpisodeAssembly, EpisodeAssemblyClip, EpisodeRenderJob


def main():
    engine = build_engine(Settings())
    with engine.begin() as connection:
        inspector = inspect(connection)
        if "video_metadata" not in {c["name"] for c in inspector.get_columns("media_files")}:
            connection.exec_driver_sql(
                "ALTER TABLE media_files ADD COLUMN video_metadata JSON NULL "
                "COMMENT '实际视频探测结果'"
            )
        if "ck_media_video_metadata" not in {
            c["name"] for c in inspect(connection).get_check_constraints("media_files")
        }:
            connection.exec_driver_sql(
                "ALTER TABLE media_files ADD CONSTRAINT ck_media_video_metadata "
                "CHECK (video_metadata IS NULL OR JSON_TYPE(video_metadata) = 'OBJECT')"
            )
        for model in (EpisodeAssembly, EpisodeAssemblyClip, EpisodeRenderJob):
            model.__table__.create(connection, checkfirst=True)
    engine.dispose()
    print("Assembly migration complete: additive columns and tables only.")


if __name__ == "__main__":
    main()
