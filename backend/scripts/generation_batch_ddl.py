"""Print additive batch DDL, or apply it explicitly to the configured database."""

import sys

try:
    from scripts.assembly_ddl import tables_ddl
except ModuleNotFoundError:
    from assembly_ddl import tables_ddl

from short_drama.core.config import Settings
from short_drama.db.session import build_engine
from short_drama.domain import GenerationBatchItem, GenerationBatchJob

tables = [GenerationBatchJob.__table__, GenerationBatchItem.__table__]


def ddl():
    return tables_ddl((GenerationBatchJob, GenerationBatchItem)) + "\n"


if __name__ == "__main__":
    if "--apply" in sys.argv:
        engine = build_engine(Settings())
        try:
            for table in tables:
                table.create(engine, checkfirst=True)
            print("Batch tables are ready; enable GENERATION_BATCHES_ENABLED after verification.")
        finally:
            engine.dispose()
    else:
        print(ddl(), end="")
