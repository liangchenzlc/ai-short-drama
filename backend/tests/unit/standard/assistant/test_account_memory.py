"""Account memory has no conversation identity and never captures paused inputs."""

import pytest
from pydantic import ValidationError

from short_drama.schemas.assistant.memory.memory import MemoryCreate, MemoryRead


def test_memory_contract_has_no_conversation_or_project_scope():
    values = {
        "kind": "preference",
        "memory_key": "对白偏好",
        "title": "对白偏好",
        "content": "以后对白简短",
    }
    memory = MemoryCreate.model_validate(values)
    assert memory.memory_key == "对白偏好"
    for extra in ({"scope_type": "user"}, {"conversation_id": "1"}, {"source_message_ids": ["2"]}):
        with pytest.raises(ValidationError):
            MemoryCreate.model_validate({**values, **extra})
    assert (
        not {"scope_type", "project_id", "conversation_id", "sources"}
        & MemoryRead.model_fields.keys()
    )


def test_extraction_only_uses_applied_inputs_from_the_current_enabled_generation():
    from short_drama.agent.memory.sources import extraction_sources

    eligible = {
        "role": "user",
        "input_applied": True,
        "memory_restricted": False,
        "explicit_memory_processed": False,
        "memory_capture_generation": 2,
    }
    inputs = [
        eligible,
        {**eligible, "memory_capture_generation": None},
        {**eligible, "memory_capture_generation": 0},
        {**eligible, "role": "assistant"},
        {**eligible, "input_applied": False},
        {**eligible, "memory_restricted": True},
    ]
    assert extraction_sources(inputs, 2) == [eligible]
    assert extraction_sources(inputs, None) == []
