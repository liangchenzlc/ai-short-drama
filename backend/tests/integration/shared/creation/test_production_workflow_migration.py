"""当前标准创作 DDL 的真实 MySQL 增量、数据保留与重复执行契约。"""

import re

import pytest
from sqlalchemy import bindparam, text

from scripts.standard_creation_ddl import apply_ddl

pytestmark = pytest.mark.integration

TABLES = (
    "projects",
    "project_scripts",
    "shot_scripts",
    "assets",
    "shot_images",
    "asset_image_candidates",
    "user_creation_drafts",
)


def mysql_statements(path):
    delimiter = ";"
    buffer = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("--"):
            continue
        if line.upper().startswith("DELIMITER "):
            assert not buffer
            delimiter = line.split(maxsplit=1)[1]
            continue
        buffer.append(raw_line)
        if line.endswith(delimiter):
            statement = "\n".join(buffer)
            statement = statement[: statement.rfind(delimiter)].strip()
            if statement:
                yield statement
            buffer = []
    assert not buffer, f"unterminated SQL in {path.name}"


def contract_snapshot(connection):
    table_params = {"names": TABLES}
    # ORM 按列名访问；增量追加与完整 SQL 的物理列序可不同，列定义仍逐项严格核对。
    columns = connection.execute(
        text(
            "SELECT table_name,column_name,column_type,is_nullable,column_default,"
            # Historical annotations may differ from Domain-generated canonical DDL.
            # Types, defaults, generated expressions and all constraints remain exact.
            "extra,generation_expression,character_set_name,collation_name "
            "FROM information_schema.columns WHERE table_schema=DATABASE() "
            "AND table_name IN :names ORDER BY table_name,column_name"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    indexes = connection.execute(
        text(
            "SELECT table_name,index_name,non_unique,seq_in_index,column_name,collation,index_type "
            "FROM information_schema.statistics WHERE table_schema=DATABASE() "
            "AND table_name IN :names ORDER BY table_name,index_name,seq_in_index"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    constraints = connection.execute(
        text(
            "SELECT table_name,constraint_name,constraint_type,enforced "
            "FROM information_schema.table_constraints WHERE constraint_schema=DATABASE() "
            "AND table_name IN :names ORDER BY table_name,constraint_name"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    keys = connection.execute(
        text(
            "SELECT table_name,constraint_name,column_name,ordinal_position,"
            "referenced_table_name,referenced_column_name "
            "FROM information_schema.key_column_usage WHERE constraint_schema=DATABASE() "
            "AND table_name IN :names ORDER BY table_name,constraint_name,ordinal_position"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    checks = connection.execute(
        text(
            "SELECT tc.table_name,cc.constraint_name,cc.check_clause "
            "FROM information_schema.check_constraints cc "
            "JOIN information_schema.table_constraints tc "
            "ON tc.constraint_schema=cc.constraint_schema "
            "AND tc.constraint_name=cc.constraint_name "
            "WHERE cc.constraint_schema=DATABASE() AND tc.table_name IN :names "
            "ORDER BY tc.table_name,cc.constraint_name"
        ).bindparams(bindparam("names", expanding=True)),
        table_params,
    ).mappings()
    return {
        "columns": [tuple(row.values()) for row in columns],
        "indexes": [tuple(row.values()) for row in indexes],
        "constraints": [tuple(row.values()) for row in constraints],
        "keys": [tuple(row.values()) for row in keys],
        "checks": [
            (table_name, constraint_name, re.sub(r"[\s`]", "", clause).lower())
            for table_name, constraint_name, clause in (row.values() for row in checks)
        ],
    }


def test_standard_creation_ddl_preserves_documents_and_is_reentrant(migration_mysql_engine):
    with migration_mysql_engine.connect() as connection:
        database = connection.scalar(text("SELECT DATABASE()"))
        assert re.fullmatch(r"short_drama_[a-f0-9]{32}_test", database)
        # 此测试重建历史迁移后的结构，当前 ORM 已不包含旧格式列。
        connection.exec_driver_sql(
            "ALTER TABLE project_scripts ADD COLUMN editor_document JSON NULL "
            "COMMENT '与纯文本同版本保存的受限 Quill Delta' AFTER content, "
            "ADD CONSTRAINT ck_scripts_editor_document "
            "CHECK (editor_document IS NULL OR JSON_TYPE(editor_document) = 'OBJECT')"
        )
        canonical = contract_snapshot(connection)
        connection.exec_driver_sql(
            "INSERT INTO projects (id,owner_user_id,name) VALUES (9001,1,'migration')"
        )
        connection.exec_driver_sql(
            "INSERT INTO project_scripts (id,project_id,position,content,state) "
            "VALUES (9006,9001,1,'saved script','confirmed')"
        )
        connection.exec_driver_sql(
            "INSERT INTO shot_scripts (id,project_id,position,script,row_version) "
            "VALUES (9007,9001,1,'saved shot',7)"
        )
        connection.commit()

        # 只撤去此工具负责的增量；历史分集迁移不再套到当前项目结构上执行。
        connection.exec_driver_sql("DROP TABLE user_creation_drafts")
        connection.exec_driver_sql(
            "ALTER TABLE project_scripts DROP CHECK ck_scripts_editor_document, "
            "DROP COLUMN editor_document"
        )
        connection.exec_driver_sql(
            "ALTER TABLE shot_scripts DROP CHECK ck_shot_scripts_shot_size, "
            "DROP COLUMN title, DROP COLUMN shot_size"
        )
        statements = {
            "project_scripts": text("SELECT * FROM project_scripts WHERE id=9006"),
            "shot_scripts": text("SELECT * FROM shot_scripts WHERE id=9007"),
        }
        before = {
            table: dict(connection.execute(statement).mappings().one())
            for table, statement in statements.items()
        }
        assert apply_ddl(connection)
        for table, statement in statements.items():
            migrated = dict(connection.execute(statement).mappings().one())
            assert {key: migrated[key] for key in before[table]} == before[table]
        assert contract_snapshot(connection) == canonical
        assert apply_ddl(connection) == []

        connection.exec_driver_sql(
            "UPDATE shot_scripts SET title='kept title',shot_size='medium_shot',row_version=8 "
            "WHERE id=9007"
        )
        connection.exec_driver_sql(
            "UPDATE project_scripts SET editor_document=JSON_OBJECT("
            "'ops',JSON_ARRAY(JSON_OBJECT('insert','saved script'))) WHERE id=9006"
        )
        connection.commit()
        changed = {
            table: dict(connection.execute(statement).mappings().one())
            for table, statement in statements.items()
        }
        assert apply_ddl(connection) == []
        assert contract_snapshot(connection) == canonical
        assert {
            table: dict(connection.execute(statement).mappings().one())
            for table, statement in statements.items()
        } == changed
