"""Read-only check for missing ORM tables/columns before deploying API and workers."""

from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError

from short_drama.core.config import Settings
from short_drama.db.session import build_engine
from short_drama.domain import Base


def main():
    engine = build_engine(Settings())
    missing = []
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = set(inspector.get_table_names())
            for table in Base.metadata.sorted_tables:
                if table.name not in tables:
                    missing.append(f"Missing table: {table.name}")
                    continue
                columns = {column["name"] for column in inspector.get_columns(table.name)}
                for name in sorted(set(table.columns.keys()) - columns):
                    missing.append(f"Missing column: {table.name}.{name}")
    except SQLAlchemyError:
        # Do not print connection strings, SQL parameters or driver messages.
        print("Database inspection failed. Check connectivity and database permissions.")
        return 2
    finally:
        engine.dispose()
    if missing:
        print("\n".join(missing))
        print("Apply the matching migrations in docs/数据库模型/migrations before deployment.")
        return 1
    print("Schema check passed: all ORM tables and columns exist (types/defaults not checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
