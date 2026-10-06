"""会话范围增量在随机 MySQL 库中保留旧记录，并允许结构安全重入。"""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_agent_conversations import actor
from test_agent_services import settings

from short_drama.domain.agent import AgentConversation
from short_drama.service.agent_conversation_service import AgentConversationService
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService

pytestmark = pytest.mark.integration


def migration_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "agent_scope_migration.py"
    spec = importlib.util.spec_from_file_location("agent_scope_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scope_migration_retains_legacy_identity_and_reentry_is_noop(migration_mysql_engine):
    engine = migration_mysql_engine
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
        session.info["actor"] = actor(1)
        project = ProjectService(session).create_project({"name": "Legacy Agent", "aspect": "16:9"})
        episode = EpisodeService(session).create_for_project(project.id, {"title": "Legacy"})
        conversation = AgentConversationService(session, settings()).create_conversation(
            {"project_id": project.id, "episode_id": episode.id, "title": "人物A（不能据此回填）"}
        )
    module = migration_module()
    with engine.connect() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE agent_conversations DROP CHECK ck_agent_conversations_scope"
        )
        connection.exec_driver_sql(
            "DROP INDEX idx_agent_conversations_scope ON agent_conversations"
        )
        for name in module.SCOPE_COLUMNS:
            connection.exec_driver_sql(f"ALTER TABLE agent_conversations DROP COLUMN {name}")
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uk_agent_runs_active_conversation "
            "ON agent_runs (active_conversation_id)"
        )
        connection.exec_driver_sql("DROP INDEX idx_agent_runs_active ON agent_runs")
        connection.commit()
        old = module.inspect_scope_schema(connection)
        assert old["status"] == "partial" and len(old["missing"]) == 9
    assert module.apply(engine)["status"] == "ready"
    assert module.apply(engine)["status"] == "ready"
    with Session(engine) as session:
        row = session.scalar(
            select(AgentConversation).where(AgentConversation.id == conversation.id)
        )
        assert row.title == "人物A（不能据此回填）" and row.scope_version == 0
        assert row.subject_id is None and row.subject_type is None
        assert row.stage is None and row.task_type is None
