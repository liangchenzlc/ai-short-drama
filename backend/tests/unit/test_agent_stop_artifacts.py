"""Cancelled runs retain reachable candidates without duplicate message references."""

from copy import deepcopy

import pytest
from generation_fixtures import generation_session
from sqlalchemy import event, select
from sqlalchemy.dialects import mysql
from test_agent_runtime_state import seed

from short_drama.agent import state
from short_drama.domain.agent import AgentConversation, AgentEvent, AgentMessage, AgentRun
from short_drama.service.base import utcnow

REFERENCES = [
    {"artifact_id": "9101", "kind": "script_candidate"},
    {"artifact_id": "9102", "kind": "novel_proposal"},
]


def flow(session, monkeypatch, status="running"):
    seed(session, status)
    conversation = session.get(AgentConversation, 301)
    run = session.get(AgentRun, 802)
    conversation.next_message_seq = 803
    session.commit()
    monkeypatch.setattr(state, "run_artifact_references", lambda *_: deepcopy(REFERENCES))
    return conversation, run


def existing_message(session, conversation, references):
    row = AgentMessage(
        id=9000 + conversation.next_message_seq,
        conversation_id=conversation.id,
        seq=conversation.next_message_seq,
        role="assistant",
        content="Saved reply",
        references=[],
        artifacts=references,
        created_at=utcnow(),
    )
    conversation.next_message_seq += 1
    session.add(row)
    return row


def messages(session):
    return session.scalars(
        select(AgentMessage)
        .where(AgentMessage.conversation_id == 301, AgentMessage.role == "assistant")
        .order_by(AgentMessage.seq)
    ).all()


@pytest.mark.parametrize("status", ["running", "waiting_generation", "waiting_review"])
def test_cancellation_persists_existing_candidates_and_keeps_the_run_terminal(monkeypatch, status):
    with generation_session() as session:
        conversation, run = flow(session, monkeypatch, status)
        assert state.finish_locked(session, conversation, run, "cancelled")
        session.commit()
        saved = messages(session)
        assert len(saved) == 1 and saved[0].artifacts == REFERENCES
        assert run.status == "cancelled" and run.next_run_at is None
        assert run.message_status == "idle" and run.lease_token is None
        assert not state.finish_locked(session, conversation, run, "cancelled")
        session.commit()
        assert len(messages(session)) == 1
        events = session.scalars(select(AgentEvent).where(AgentEvent.run_id == run.id)).all()
        assert [event.event_type for event in events] == ["run.finished", "message.created"]


def test_cancellation_only_appends_candidates_missing_from_existing_messages(monkeypatch):
    with generation_session() as session:
        conversation, run = flow(session, monkeypatch)
        existing_message(session, conversation, REFERENCES[:1])
        state.finish_locked(session, conversation, run, "cancelled")
        session.commit()
        assert [message.artifacts for message in messages(session)] == [
            REFERENCES[:1],
            REFERENCES[1:],
        ]


def test_already_referenced_candidates_and_an_empty_run_add_no_cancellation_message(monkeypatch):
    with generation_session() as session:
        conversation, run = flow(session, monkeypatch)
        existing_message(session, conversation, REFERENCES)
        state.finish_locked(session, conversation, run, "cancelled")
        session.commit()
        assert len(messages(session)) == 1
    with generation_session() as session:
        conversation, run = flow(session, monkeypatch)
        monkeypatch.setattr(state, "run_artifact_references", lambda *_: [])
        state.finish_locked(session, conversation, run, "cancelled")
        session.commit()
        assert messages(session) == []


def test_late_collection_deduplicates_current_run_references_after_stop(monkeypatch):
    with generation_session() as session:
        conversation, run = flow(session, monkeypatch)
        state.finish_locked(session, conversation, run, "cancelled")
        state.append_late_artifact_message(session, conversation, run, {"9101", "9102"})
        state.append_late_artifact_message(session, conversation, run, {"9102"})
        session.commit()
        assert len(messages(session)) == 1
        assert messages(session)[0].artifacts == REFERENCES
        assert run.status == "cancelled" and run.next_run_at is None


def test_late_collection_only_links_requested_candidates_and_keeps_other_scopes_isolated(
    monkeypatch,
):
    with generation_session() as session:
        conversation, run = flow(session, monkeypatch)
        other = session.get(AgentConversation, 302)
        other.next_message_seq = 804
        existing_message(session, other, REFERENCES)
        state.append_late_artifact_message(session, conversation, run, {"9102", "foreign"})
        state.append_late_artifact_message(session, conversation, run, {"9102"})
        session.commit()
        assert [message.artifacts for message in messages(session)] == [REFERENCES[1:]]


def test_cancellation_and_candidate_message_roll_back_together(monkeypatch):
    with generation_session() as session:
        conversation, run = flow(session, monkeypatch)
        state.finish_locked(session, conversation, run, "cancelled")
        session.flush()
        session.rollback()
        session.refresh(run)
        assert run.status == "running" and run.finished_at is None
        assert messages(session) == []
        assert session.scalars(select(AgentEvent).where(AgentEvent.run_id == run.id)).all() == []


def test_late_reference_deduplication_requests_a_current_mysql_locking_read(monkeypatch):
    with generation_session() as session:
        conversation, run = flow(session, monkeypatch)
        statements = []
        event.listen(
            session,
            "do_orm_execute",
            lambda execution: statements.append(
                str(execution.statement.compile(dialect=mysql.dialect()))
            ),
        )
        state.append_late_artifact_message(session, conversation, run, {"9101"})
        assert any(
            "SELECT agent_messages.artifacts" in sql and "FOR UPDATE" in sql for sql in statements
        )
