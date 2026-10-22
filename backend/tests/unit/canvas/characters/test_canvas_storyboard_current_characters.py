"""Ordinary canvas character nodes remain usable without standard-library bindings."""

import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from short_drama.ai.types import GenerationError
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.domain import Project, ProjectCanvas
from short_drama.schemas.canvas import CanvasDocument
from short_drama.service import canvas_service
from short_drama.service.base import utcnow
from short_drama.service.canvas_service import CanvasService
from short_drama.service.canvas_storyboard import (
    canvas_storyboard_result,
    check_storyboard_context,
    storyboard_snapshot,
)
from tests.support.generation_fixtures import generation_session


def node(identifier, kind, metadata):
    return {
        "id": identifier,
        "type": kind,
        "title": identifier,
        "position": {"x": 0, "y": 0},
        "width": 320,
        "height": 220,
        "metadata": metadata,
    }


def document():
    return {
        "id": "ordinary",
        "workspaceProjectId": "10",
        "title": "Ordinary canvas",
        "nodes": [
            node(
                "hero",
                "text",
                {
                    "workflowKind": "character",
                    "characterName": "Hero",
                    "characterDefinition": {"description": "lead", "extensions": {"keep": []}},
                },
            ),
            node(
                "style",
                "text",
                {"workflowKind": "styleboard", "content": "ink drawing", "stylePresetId": "ink"},
            ),
            node("script", "script", {"storyboard": {"rows": []}}),
        ],
        "connections": [],
    }


def seed(session):
    session.info["actor"] = SimpleNamespace(user_id=1, request_id="ordinary-character")
    now = utcnow()
    with session.begin():
        project = Project(
            id=10,
            owner_user_id=1,
            name="Canvas",
            workspace_mode="infinite_canvas",
            created_at=now,
            updated_at=now,
        )
        session.add(project)
        session.flush()
        canvas = ProjectCanvas(
            id=20,
            project_id=10,
            source_key="ordinary",
            title="Ordinary canvas",
            position=0,
            row_version=1,
            schema_version=1,
            properties_json={},
            created_at=now,
            updated_at=now,
            created_by=1,
            updated_by=1,
        )
        session.add(canvas)


def test_ordinary_text_character_saves_roundtrips_and_keeps_cas(monkeypatch):
    # Only the MySQL advisory mutex is replaced; ORM save, revision and access checks run.
    monkeypatch.setattr(canvas_service, "canvas_folder_write_lock", lambda _session: nullcontext())
    with generation_session() as session:
        seed(session)
        service = CanvasService(session)
        body = {"expected_row_version": "1", "source_document": document()}
        saved = service.commit(10, 20, body, "ordinary-save")
        assert saved["row_version"] == "2"
        read = service.read(10, 20, private=True)["source_document"]
        assert (
            read["nodes"][0]["metadata"]["characterDefinition"]
            == document()["nodes"][0]["metadata"]["characterDefinition"]
        )
        assert "projectId" not in read
        with pytest.raises(WorkflowError) as stale:
            service.commit(10, 20, body, "ordinary-stale")
        assert stale.value.code == "canvas_revision_conflict"
        session.info["actor"] = SimpleNamespace(user_id=2)
        with pytest.raises(NotFound):
            service.read(10, 20, private=True)


@pytest.mark.parametrize(
    "key", ["characterAssetId", "characterVersionId", "characterVersionPolicy"]
)
def test_retired_enterprise_bindings_are_rejected_even_when_nested(key):
    source = document()
    source["nodes"][0]["metadata"]["nested"] = [{key: "100"}]
    with pytest.raises(ValidationError):
        CanvasDocument.model_validate(source)


def test_standard_source_project_cannot_be_reintroduced_through_document():
    with pytest.raises(ValidationError):
        CanvasDocument.model_validate({**document(), "projectId": "100"})


def test_storyboard_context_and_result_bind_ordinary_node_identity():
    snapshot = storyboard_snapshot(document(), "script")
    assert snapshot["characters"] == [
        {
            "nodeId": "hero",
            "name": "Hero",
            "definition": {"description": "lead", "extensions": {"keep": []}},
        }
    ]
    request = {"input": {**snapshot, "shotCount": 1, "shotDurationSeconds": 3}}
    result = canvas_storyboard_result(
        json.dumps(
            {
                "title": "Scene",
                "rows": [
                    {
                        "shotNumber": 1,
                        "durationSeconds": 3,
                        "plotDescription": "Hero walks",
                        "characters": ["Hero"],
                        "assetBindings": [{"nodeId": "hero", "role": "character", "priority": 0}],
                    }
                ],
            }
        ),
        request,
        "task-1",
    )
    assert result["rows"][0]["characters"] == [{"characterName": "Hero", "characterNodeId": "hero"}]
    changed = deepcopy(document())
    changed["nodes"][0]["metadata"]["characterDefinition"] = {"description": "changed"}
    with pytest.raises(WorkflowError) as stale:
        check_storyboard_context(request, storyboard_snapshot(changed, "script"))
    assert stale.value.code == "canvas_tool_source_changed"
    invalid = {
        "title": "Scene",
        "rows": [
            {
                "shotNumber": 1,
                "durationSeconds": 3,
                "plotDescription": "Unknown",
                "characters": ["Other"],
            }
        ],
    }
    with pytest.raises(GenerationError):
        canvas_storyboard_result(json.dumps(invalid), request, "task-1")


def test_same_name_on_different_nodes_requires_disambiguation():
    source = document()
    source["nodes"].append(
        node("second", "text", {"workflowKind": "character", "characterName": "Hero"})
    )
    with pytest.raises(WorkflowError) as ambiguous:
        storyboard_snapshot(source, "script")
    assert ambiguous.value.code == "canvas_storyboard_character_ambiguous"
