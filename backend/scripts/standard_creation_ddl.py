"""显式执行标准创作增量 DDL；应用启动不自动建表或迁移。"""

import argparse
import logging
import sys

from sqlalchemy import inspect
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateIndex, CreateTable

from short_drama.core.config import Settings
from short_drama.db.session import build_engine
from short_drama.domain import UserCreationDraft

logger = logging.getLogger(__name__)
SHOT_SIZE_CHECK = (
    "CHECK (`shot_size` IS NULL OR `shot_size` IN "
    "('full_shot','medium_shot','close_up','extreme_close_up','wide_shot','two_shot'))"
)


def ddl() -> str:
    return (
        "ALTER TABLE `shot_scripts` ADD COLUMN `title` VARCHAR(255) NOT NULL DEFAULT '' "
        "COMMENT '镜头名称；旧记录为空' AFTER `row_version`, "
        "ADD COLUMN `shot_size` VARCHAR(20) NULL DEFAULT NULL COMMENT '受限景别元信息' "
        "AFTER `title`, ADD CONSTRAINT `ck_shot_scripts_shot_size` " + SHOT_SIZE_CHECK + ";\n\n"
        "ALTER TABLE `episode_scripts` ADD COLUMN `editor_document` JSON NULL "
        "COMMENT '与纯文本同版本保存的受限 Quill Delta' AFTER `content`, "
        "ADD CONSTRAINT `ck_scripts_editor_document` "
        "CHECK (`editor_document` IS NULL OR JSON_TYPE(`editor_document`) = 'OBJECT');\n\n"
        + str(CreateTable(UserCreationDraft.__table__).compile(dialect=dialect())).strip()
        + ";\n"
        + "\n".join(
            str(CreateIndex(index).compile(dialect=dialect())) + ";"
            for index in sorted(UserCreationDraft.__table__.indexes, key=lambda index: index.name)
        )
        + "\n"
    )


def apply_ddl(connection) -> list[str]:
    applied = []

    def execute(statement):
        connection.exec_driver_sql(statement)
        applied.append(statement)

    columns = {col["name"]: col for col in inspect(connection).get_columns("shot_scripts")}
    if "title" not in columns:
        execute(
            "ALTER TABLE `shot_scripts` ADD COLUMN `title` VARCHAR(255) NOT NULL DEFAULT '' "
            "COMMENT '镜头名称；旧记录为空' AFTER `row_version`"
        )
    elif columns["title"]["nullable"]:
        execute("UPDATE `shot_scripts` SET `title` = '' WHERE `title` IS NULL")
        execute(
            "ALTER TABLE `shot_scripts` MODIFY COLUMN `title` VARCHAR(255) NOT NULL "
            "DEFAULT '' COMMENT '镜头名称；旧记录为空'"
        )
    if "shot_size" not in columns:
        execute(
            "ALTER TABLE `shot_scripts` ADD COLUMN `shot_size` VARCHAR(20) NULL "
            "DEFAULT NULL COMMENT '受限景别元信息' AFTER `title`"
        )
    checks = {c["name"] for c in inspect(connection).get_check_constraints("shot_scripts")}
    if "ck_shot_scripts_shot_size" not in checks:
        execute(
            "ALTER TABLE `shot_scripts` ADD CONSTRAINT `ck_shot_scripts_shot_size` "
            + SHOT_SIZE_CHECK
        )
    columns = {c["name"] for c in inspect(connection).get_columns("episode_scripts")}
    if "editor_document" not in columns:
        execute(
            "ALTER TABLE `episode_scripts` ADD COLUMN `editor_document` JSON NULL "
            "COMMENT '与纯文本同版本保存的受限 Quill Delta' AFTER `content`"
        )
    checks = {c["name"] for c in inspect(connection).get_check_constraints("episode_scripts")}
    if "ck_scripts_editor_document" not in checks:
        execute(
            "ALTER TABLE `episode_scripts` ADD CONSTRAINT `ck_scripts_editor_document` "
            "CHECK (`editor_document` IS NULL OR JSON_TYPE(`editor_document`) = 'OBJECT')"
        )
    if not inspect(connection).has_table("user_creation_drafts"):
        execute(str(CreateTable(UserCreationDraft.__table__).compile(dialect=dialect())))
    else:
        columns = {c["name"]: c for c in inspect(connection).get_columns("user_creation_drafts")}
        if "reference_media_ids" not in columns:
            execute(
                "ALTER TABLE `user_creation_drafts` ADD COLUMN `reference_media_ids` JSON "
                "NOT NULL DEFAULT (JSON_ARRAY()) COMMENT '个人参考图永久媒体ID；不保存展示URL', "
                "ADD CONSTRAINT `ck_user_creation_drafts_reference_media_ids` CHECK "
                "(JSON_TYPE(`reference_media_ids`) = 'ARRAY' "
                "AND JSON_LENGTH(`reference_media_ids`) <= 16)"
            )
        if not columns["model_config_id"]["nullable"]:
            execute(
                "ALTER TABLE `user_creation_drafts` MODIFY COLUMN `model_config_id` "
                "BIGINT UNSIGNED NULL COMMENT 'Saved model choice ID'"
            )
    columns = {c["name"]: c for c in inspect(connection).get_columns("user_creation_drafts")}
    prompt_comment = UserCreationDraft.__table__.c.input_text.comment
    if columns["input_text"]["comment"] != prompt_comment:
        execute(
            "ALTER TABLE `user_creation_drafts` MODIFY COLUMN `input_text` "
            f"MEDIUMTEXT NOT NULL DEFAULT ('') COMMENT '{prompt_comment}'"
        )
    indexes = {index["name"] for index in inspect(connection).get_indexes("user_creation_drafts")}
    for index in sorted(UserCreationDraft.__table__.indexes, key=lambda index: index.name):
        if index.name not in indexes:
            execute(str(CreateIndex(index).compile(dialect=dialect())))
    return applied


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--db-port", type=int)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if not args.apply:
        sys.stdout.write(ddl())
    else:
        settings = Settings(**({"db_port": args.db_port} if args.db_port else {}))
        engine = build_engine(settings)
        try:
            with engine.begin() as connection:
                logger.info(
                    "当前数据库：%s", connection.exec_driver_sql("SELECT DATABASE()").scalar()
                )
                for statement in apply_ddl(connection):
                    logger.info("已执行：%s", statement)
                logger.info("标准创作字段和私人草稿表已就绪")
        finally:
            engine.dispose()
