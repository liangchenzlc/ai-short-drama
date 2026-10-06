"""Cursor recovery uses bounded SQL; MySQL performance is verified separately."""

from types import SimpleNamespace

import pytest
from generation_fixtures import config, generation_session
from sqlalchemy import event

from short_drama.domain.agent import AgentConversation, AgentEvent, AgentMessage, AgentRun
from short_drama.service.agent_run_service import AgentRunService
from short_drama.service.base import utcnow
from short_drama.service.episode_service import EpisodeService
from short_drama.service.project_service import ProjectService


def seed(session, status, *, next_event_seq=105):
    project = ProjectService(session).create({"name": "P", "aspect": "16:9"})
    episode = EpisodeService(session).create(
        {"project_id": project.id, "title": "E", "position": 1, "aspect": "16:9"}
    )
    model = config(session, "text", 71)
    now = utcnow()
    for identifier in (301, 302):
        session.add(
            AgentConversation(
                id=identifier,
                owner_user_id=1,
                project_id=project.id,
                episode_id=episode.id,
                stage="source",
                subject_type="episode",
                subject_id=episode.id,
                task_type="writing",
                scope_version=1,
                title="C",
                next_event_seq=next_event_seq,
                fixed_requirements={},
                created_at=now,
                updated_at=now,
            )
        )
    for identifier, parent, state in (
        (801, 301, "succeeded"),
        (802, 301, status),
        (803, 302, "running"),
    ):
        session.add(
            AgentMessage(
                id=identifier + 1000,
                conversation_id=parent,
                seq=identifier,
                role="user",
                content="Continue",
                references=[],
                artifacts=[],
                created_at=now,
            )
        )
        session.add(
            AgentRun(
                id=identifier,
                conversation_id=parent,
                trigger_message_id=identifier + 1000,
                initiated_by=1,
                model_config_id=model.id,
                status=state,
                checkpoint={"authorization": {"mode": "auto"}},
                config_snapshot={"name": "M"},
                budget={},
                usage={},
                created_at=now,
                updated_at=now,
                finished_at=now if state in {"succeeded", "failed", "cancelled"} else None,
            )
        )
    session.commit()
    session.info["actor"] = SimpleNamespace(user_id=1, request_id="cursor-recovery")
    session.info["request_project"] = int(project.id)
    return {
        "stage": "source",
        "subject_type": "episode",
        "subject_id": episode.id,
        "task_type": "writing",
    }


def add_event(session, run_id, seq, *, conversation_id=301):
    session.add(
        AgentEvent(
            id=run_id * 1000 + seq,
            conversation_id=conversation_id,
            run_id=run_id,
            seq=seq,
            event_type="assistant.delta",
            payload={"text": "text"},
            created_at=utcnow(),
        )
    )


def runtime_state(session, scope):
    return AgentRunService(session, SimpleNamespace(agent_enabled=True)).runtime_state("301", scope)


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled"])
def test_completed_history_starts_at_latest_cursor_without_an_event_scan(status):
    with generation_session() as session:
        scope = seed(session, status)
        add_event(session, 801, 1)
        add_event(session, 802, 20)
        session.commit()
        statements = []
        event.listen(
            session, "do_orm_execute", lambda state: statements.append(str(state.statement))
        )
        state = runtime_state(session, scope)
        assert state.cursor == state.resume_cursor == 104
        assert state.active_run is None and state.queued_runs == []
        assert not any("min(agent_events.seq)" in statement for statement in statements)


@pytest.mark.parametrize("status", ["queued", "running", "waiting_generation", "waiting_review"])
def test_active_recovery_ignores_completed_other_conversation_and_future_events(status):
    with generation_session() as session:
        scope = seed(session, status)
        add_event(session, 801, 1)
        add_event(session, 803, 2, conversation_id=302)
        add_event(session, 802, 101)
        add_event(session, 802, 104)
        add_event(session, 802, 105)
        session.commit()
        state = runtime_state(session, scope)
        assert state.cursor == 104 and state.resume_cursor == 100
        if status == "queued":
            assert state.active_run is None
            assert [row.id for row in state.queued_runs] == [802]
        else:
            assert state.active_run.id == 802


def test_an_active_run_without_snapshot_events_starts_from_snapshot_cursor():
    with generation_session() as session:
        scope = seed(session, "running")
        add_event(session, 801, 1)
        add_event(session, 802, 105)
        session.commit()
        state = runtime_state(session, scope)
        assert state.cursor == state.resume_cursor == 104
        assert state.active_run.id == 802


def test_the_first_active_event_can_resume_from_zero():
    with generation_session() as session:
        scope = seed(session, "running", next_event_seq=2)
        add_event(session, 802, 1)
        session.commit()
        state = runtime_state(session, scope)
        assert state.cursor == 1 and state.resume_cursor == 0
