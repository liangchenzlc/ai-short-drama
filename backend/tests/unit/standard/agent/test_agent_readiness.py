from types import SimpleNamespace

import pytest

from short_drama.db import readiness
from short_drama.domain import AGENT_TABLES, Base


def test_prompt_schema_check_excludes_all_agent_tables(monkeypatch):
    class Inspector:
        def get_table_names(self):
            return sorted(set(Base.metadata.tables) - AGENT_TABLES)

        def get_columns(self, name):
            return [{"name": column.name} for column in Base.metadata.tables[name].columns]

    monkeypatch.setattr(readiness, "inspect", lambda _connection: Inspector())
    assert readiness.schema_gaps(None) == []
    assert readiness.schema_gaps(None, include_agent=True) == sorted(AGENT_TABLES)
    assert readiness.inspect_agent_schema(None) == {
        "status": "absent",
        "gaps": sorted(AGENT_TABLES),
    }
    assert {"agent_attachments", "agent_skills"} <= AGENT_TABLES


def test_agent_disabled_does_not_connect_and_enabled_requires_accounts():
    readiness.assert_agent_ready(None, SimpleNamespace(auth_enabled=True, agent_enabled=False))
    with pytest.raises(RuntimeError, match="authenticated accounts"):
        readiness.assert_agent_ready(None, SimpleNamespace(auth_enabled=False, agent_enabled=True))


def test_check_normalization_keeps_boolean_grouping_and_case_sensitive_literals():
    normalize = readiness._normalize_sql
    assert normalize("a > 0 AND (b IS NULL OR b > 0)") == normalize(
        "((`a` > 0) and ((`b` is null) or (`b` > 0)))"
    )
    assert normalize("a > 0 AND (b IS NULL OR b > 0)") != normalize("a > 0 AND b IS NULL OR b > 0")
    assert normalize("status = 'active'") != normalize("status = 'ACTIVE'")
    assert normalize("duration BETWEEN 1000 AND 10000 AND version > 0") == normalize(
        "((duration BETWEEN 1000 AND 10000) AND (version > 0))"
    )
