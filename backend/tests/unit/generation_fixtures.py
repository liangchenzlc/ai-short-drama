from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import JSON, DateTime, Integer, MetaData, String, create_engine, event, text
from sqlalchemy.orm import Session

from short_drama.domain import AIModelConfig, Base


def generation_session():
    """Local behavior fixture; MySQL constraint/concurrency tests stay integration tests."""
    engine = create_engine("sqlite://")
    event.listen(engine, "connect", lambda db, _: db.create_function("CHAR_LENGTH", 1, len))
    metadata = MetaData()
    for original in Base.metadata.sorted_tables:
        table = original.to_metadata(metadata)
        for column in table.columns:
            kind = column.type.python_type
            column.type = {int: Integer, str: String, datetime: DateTime, dict: JSON}[kind]()
            if column.server_default is not None and column.computed is None:
                if "CURRENT_TIMESTAMP" in str(column.server_default.arg):
                    column.server_default.arg = text("CURRENT_TIMESTAMP")
    metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def config(session, kind="image", identifier=1):
    now = datetime(2026, 1, 1)
    row = AIModelConfig(
        id=identifier,
        service_type=kind,
        name="Test model",
        model_key="model",
        provider="test",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        apikey="encrypted-secret",
        enabled=1,
        is_deleted=0,
        is_default=1,
        row_version=1,
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.commit()
    return row


settings = SimpleNamespace(generation_archive_budget_seconds=86400)
