"""User personal generation draft ORM model."""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import (
    BIGINT,
    DATETIME,
    JSON,
    MEDIUMTEXT,
    VARCHAR,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class UserCreationDraft(Base):
    """C5: Personal generation draft storage.

    Scope: user, project, episode, object_id, purpose.
    Contains personal model configuration, input supplements, and settings for next generation.
    Visible only to the owner (user_id).
    """

    __tablename__ = "user_creation_drafts"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=False,
        nullable=False,
        comment="Snowflake-generated stable identifier",
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="本人草稿所有者"
    )
    project_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="Project scope"
    )
    episode_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, comment="Episode scope"
    )
    object_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        comment="分集、素材或镜头的稳定对象ID",
    )
    purpose: Mapped[str] = mapped_column(
        VARCHAR(32, collation="utf8mb4_0900_bin"),
        nullable=False,
        comment="novel_adaptation / asset_image / storyboard_image / storyboard_video",
    )
    model_config_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True), nullable=True, comment="Saved model choice ID"
    )
    input_text: Mapped[str] = mapped_column(
        MEDIUMTEXT(),
        nullable=False,
        server_default=text("('')"),
        comment="本人本次输入；图片和改编最多4000字符，视频最多16000字符",
    )
    image_settings: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
        server_default=text("NULL"),
        comment="Image generation settings: aspect, resolution, count",
    )
    video_settings: Mapped[dict | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
        server_default=text("NULL"),
        comment="Video generation settings: resolution, duration_ms",
    )
    draft_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        server_default=text("1"),
        comment="Local draft version for UI conflict detection",
    )
    reference_media_ids: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
        server_default=text("(JSON_ARRAY())"),
        comment="个人参考图永久媒体ID；不保存展示URL",
    )
    source_version: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        nullable=True,
        server_default=text("NULL"),
        comment="Source object version when draft was last saved",
    )
    row_version: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        nullable=False,
        server_default=text("1"),
        comment="Optimistic concurrency token for server updates",
    )
    created_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="Creation timestamp",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DATETIME(fsp=6),
        nullable=True,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="Last modification timestamp",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "project_id",
            "episode_id",
            "object_id",
            "purpose",
            name="uk_user_creation_drafts_scope",
        ),
        Index("idx_user_creation_drafts_user_project", "user_id", "project_id"),
        Index("idx_user_creation_drafts_user_episode", "user_id", "episode_id"),
        ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_creation_drafts_user_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_user_creation_drafts_project_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["episode_id"],
            ["episodes.id"],
            name="fk_user_creation_drafts_episode_id",
            ondelete="RESTRICT",
            onupdate="RESTRICT",
        ),
        CheckConstraint("`row_version` > 0", name="ck_user_creation_drafts_row_version"),
        CheckConstraint("`draft_version` > 0", name="ck_user_creation_drafts_draft_version"),
        CheckConstraint(
            "`purpose` IN ('novel_adaptation', 'asset_image', "
            "'storyboard_image', 'storyboard_video')",
            name="ck_user_creation_drafts_purpose",
        ),
        CheckConstraint(
            "`image_settings` IS NULL OR JSON_TYPE(`image_settings`) = 'OBJECT'",
            name="ck_user_creation_drafts_image_settings",
        ),
        CheckConstraint(
            "`video_settings` IS NULL OR JSON_TYPE(`video_settings`) = 'OBJECT'",
            name="ck_user_creation_drafts_video_settings",
        ),
        CheckConstraint(
            "JSON_TYPE(`reference_media_ids`) = 'ARRAY' "
            "AND JSON_LENGTH(`reference_media_ids`) <= 16",
            name="ck_user_creation_drafts_reference_media_ids",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_row_format": "DYNAMIC",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_0900_ai_ci",
            "comment": "本人、项目、分集、对象和用途独立的生成草稿",
        },
    )
