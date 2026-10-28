"""离线清理测试创作数据并删除退役表；保留账号、模型、Skill 和画布配置。"""

import argparse
import hashlib
import json

from sqlalchemy import create_engine, inspect, text

from short_drama.core.config import Settings
from short_drama.domain import Base

RETIRED = ("novel_script_records", "project_story_outlines", "project_novels")
PRESERVED = frozenset(
    {
        "users",
        "user_sessions",
        "email_challenges",
        "email_outbox",
        "auth_rate_limits",
        "audit_events",
        "ai_model_configs",
        "user_model_preferences",
        "agent_preferences",
        "agent_skills",
        "agent_skill_versions",
        "agent_skill_settings",
        "canvas_model_catalogs",
        "canvas_channel_models",
        "canvas_workspace_user_states",
        "canvas_style_profiles",
        "canvas_style_preferences",
        "canvas_style_media_references",
    }
)


def fingerprint(connection, name):
    rows = connection.execute(text(f"SELECT * FROM `{name}`")).mappings()
    values = sorted(json.dumps(dict(row), sort_keys=True, default=str) for row in rows)
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def reset(engine, *, execute=False):
    with engine.connect() as connection:
        names = set(inspect(connection).get_table_names())
        current = set(Base.metadata.tables)
        unknown = names - current - set(RETIRED)
        if unknown or current - names:
            raise RuntimeError("数据库结构不是本次迁移的已知版本，拒绝清理")
        business = sorted(names - PRESERVED - {"media_files"})
        counts = {
            name: connection.scalar(text(f"SELECT COUNT(*) FROM `{name}`")) for name in business
        }
        kept = {name: fingerprint(connection, name) for name in sorted(PRESERVED)}
        # 风格参考图属于配置闭包；Skill 资源直接引用 MinIO，整个脚本不删除对象。
        private_style_media = connection.scalar(
            text(
                "SELECT COUNT(*) FROM media_files m "
                "JOIN canvas_style_media_references r ON r.media_id = m.id "
                "WHERE m.project_id IS NOT NULL"
            )
        )
        if private_style_media:
            raise RuntimeError("保留的风格配置仍引用项目媒体，请先处理该引用，拒绝删除项目")
        retained_media = (
            connection.execute(
                text(
                    "SELECT DISTINCT m.* FROM media_files m "
                    "JOIN canvas_style_media_references r ON r.media_id = m.id"
                )
            )
            .mappings()
            .all()
        )
        connection.rollback()
        report = {"execute": execute, "business_rows": counts, "retired_tables": list(RETIRED)}
        if not execute:
            return report
        # 只在停止 API、scheduler 和全部 Worker 后执行。MySQL 关闭外键检查是连接级设置，
        # 必须在 finally 恢复；所有业务行删除仍在同一个可回滚事务中。
        connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
        connection.commit()
        try:
            with connection.begin():
                for name in business:
                    connection.exec_driver_sql(f"DELETE FROM `{name}`")
                connection.exec_driver_sql(
                    "DELETE FROM media_files WHERE id NOT IN "
                    "(SELECT media_id FROM canvas_style_media_references)"
                )
                if kept != {name: fingerprint(connection, name) for name in sorted(PRESERVED)}:
                    raise RuntimeError("保留配置发生变化，回滚业务数据删除")
                remaining = connection.execute(text("SELECT * FROM media_files")).mappings().all()
                if sorted(map(str, remaining)) != sorted(map(str, retained_media)):
                    raise RuntimeError("保留媒体闭包不一致，回滚业务数据删除")
            # DROP TABLE 会隐式提交，因此在数据与配置核对完成后单独执行，可安全重跑。
            for name in RETIRED:
                connection.exec_driver_sql(f"DROP TABLE IF EXISTS `{name}`")
            connection.commit()
        finally:
            connection.rollback()
            connection.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")
            connection.commit()
        if set(inspect(connection).get_table_names()) != current:
            raise RuntimeError("清理后表清单与 ORM 不一致")
        report["preserved_config_verified"] = True
        report["table_count"] = len(current)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="确认已离线，执行测试业务数据清理")
    args = parser.parse_args()
    engine = create_engine(Settings().database_url, pool_pre_ping=True, hide_parameters=True)
    try:
        print(json.dumps(reset(engine, execute=args.execute), ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
