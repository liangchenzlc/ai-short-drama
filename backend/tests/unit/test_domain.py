import re
from pathlib import Path

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateTable

from short_drama.domain import Base

SQL = (Path(__file__).resolve().parents[3] / "docs/数据库模型/schema.mysql8.sql").read_text(
    encoding="utf-8"
)
TABLES = dict(re.findall(r"CREATE TABLE `([^`]+)` \((.*?)\n\) ENGINE", SQL, re.S))


def test_all_tables_and_columns_match_authoritative_sql():
    assert set(Base.metadata.tables) == set(TABLES) | {
        "async_tasks",
        "ai_generation_records",
        "media_assets",
    }
    assert len(TABLES) == 17
    for name, body in TABLES.items():
        table = Base.metadata.tables[name]
        columns = dict(re.findall(r"^  `([^`]+)` (.*?)(?: COMMENT .*),?$", body, re.M))
        if name == "ai_model_configs":
            columns["capability_cache"] = "JSON NULL"
        assert set(table.c.keys()) == set(columns), name
        for column_name, declaration in columns.items():
            column = table.c[column_name]
            expected_type = declaration.split(" NOT NULL")[0].split(" NULL")[0]
            expected_type = expected_type.split(" GENERATED")[0]
            expected_type = expected_type.split(" COLLATE")[0].split(" CHARACTER SET")[0]
            expected_type = re.sub(r"^INT\b", "INTEGER", expected_type)
            assert str(column.type.compile(dialect=dialect())).startswith(expected_type)
            assert column.nullable == ("NOT NULL" not in declaration)
            assert bool(column.computed) == ("GENERATED ALWAYS" in declaration)
            if column.computed is not None:
                assert column.computed.persisted is True
                assert str(column.computed.sqltext) in declaration
            else:
                expected_default = re.search(r"DEFAULT (.*)$", declaration)
                if expected_default:
                    assert str(column.server_default.arg) == expected_default[1]
                else:
                    assert column.server_default is None
            collation = re.search(r"COLLATE (\w+)", declaration)
            if collation:
                assert column.type.collation == collation[1]
            if column_name == "id":
                assert column.primary_key and column.autoincrement is False


def test_constraints_indexes_and_mysql_compilation_match_sql():
    for name, body in TABLES.items():
        table = Base.metadata.tables[name]
        ddl = str(CreateTable(table).compile(dialect=dialect()))
        assert "AUTO_INCREMENT" not in ddl
        assert "AUTO_INCREMENT" not in body
        assert "ENGINE=InnoDB" in ddl
        assert "utf8mb4_0900_ai_ci" in ddl
        expected_names = set(re.findall(r"(?:CONSTRAINT|(?:UNIQUE )?KEY) `([^`]+)`", body))
        actual_names = {item.name for item in table.constraints | table.indexes if item.name}
        assert actual_names == expected_names, name
        for constraint in table.constraints:
            if isinstance(constraint, ForeignKeyConstraint):
                assert constraint.ondelete == constraint.onupdate == "RESTRICT"
                assert all(element.column is not None for element in constraint.elements)
                source = "`, `".join(element.parent.name for element in constraint.elements)
                target = "`, `".join(element.column.name for element in constraint.elements)
                table_name = constraint.elements[0].column.table.name
                expected_fk = (
                    f"CONSTRAINT `{constraint.name}` FOREIGN KEY (`{source}`) "
                    f"REFERENCES `{table_name}` (`{target}`)"
                )
                assert expected_fk in re.sub(r"\s+", " ", body)
            elif isinstance(constraint, CheckConstraint):
                assert str(constraint.sqltext) in body
            elif isinstance(constraint, UniqueConstraint):
                expected = "`, `".join(column.name for column in constraint.columns)
                assert f"UNIQUE KEY `{constraint.name}` (`{expected}`)" in body
        for index in table.indexes:
            expected = "`, `".join(column.name for column in index.columns)
            assert f"KEY `{index.name}` (`{expected}`)" in body


def test_creation_only_tables_do_not_gain_update_audit():
    for name in (
        "global_assets",
        "project_assets",
        "episode_assets",
        "script_shot_records",
        "novel_script_records",
        "media_recycle_bin",
    ):
        assert "updated_at" not in Base.metadata.tables[name].c
        assert "updated_by" not in Base.metadata.tables[name].c


def test_generation_constraints_and_columns_match_incremental_sql():
    migrations = (
        Path(__file__).resolve().parents[3] / "docs/数据库模型/migrations/2026-09-20-ai-generation"
    )
    for number in range(1, 4):
        source = next(migrations.glob(f"00{number}_*.sql")).read_text(encoding="utf-8")
        table_name, body = re.search(
            r"CREATE TABLE (\w+) \((.*?)\n\) ENGINE", source, re.S
        ).groups()
        table = Base.metadata.tables[table_name]
        fields = dict(
            re.findall(
                r"^  (\w+) ((?:BIGINT|INT|TINYINT|VARCHAR|CHAR|DATETIME|JSON|"
                r"MEDIUMTEXT|TEXT)\b.*),$",
                body,
                re.M,
            )
        )
        assert set(table.c.keys()) == set(fields)
        for name, declaration in fields.items():
            column = table.c[name]
            assert column.nullable == ("NOT NULL" not in declaration)
            expected = re.sub(
                r"^INT\b",
                "INTEGER",
                declaration.split(" NOT NULL")[0]
                .split(" NULL")[0]
                .split(" COLLATE")[0]
                .split(" CHARACTER SET")[0],
            )
            assert str(column.type.compile(dialect=dialect())).startswith(expected)
        names = set(re.findall(r"(?:CONSTRAINT|(?:UNIQUE )?KEY) (\w+)", body))
        assert {item.name for item in table.constraints | table.indexes if item.name} == names
        normalized = " ".join(body.split())
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                assert " ".join(str(constraint.sqltext).split()) in normalized
            if isinstance(constraint, ForeignKeyConstraint):
                assert constraint.ondelete == constraint.onupdate == "RESTRICT"
