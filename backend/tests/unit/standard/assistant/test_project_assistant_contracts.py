"""Project chat accepts context, never creative execution instructions."""

import base64
import hashlib
import io
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from short_drama.agent.context.media import MAX_INPUT_BYTES, materialize_prompt
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.schemas.assistant.tasks.task import TaskSubjectReference
from short_drama.service.assistant.context.context import _freeze_node_media


def test_subject_reference_checks_kind_and_stable_identifiers():
    reference = TaskSubjectReference(
        project_id="1",
        source_kind="canvas",
        source_id="canvas-key",
        kind="node",
        id="node",
        revision="9007199254740993",
    )
    assert reference.model_dump(mode="json")["revision"] == "9007199254740993"
    with pytest.raises(ValidationError):
        TaskSubjectReference(
            project_id="1",
            source_kind="project",
            source_id="1",
            kind="node",
            id="node",
            revision="1",
        )


def media_fixture(kind="image"):
    row = SimpleNamespace(
        kind=kind,
        node_key="node-1",
        content_json={
            "metadata": {
                "storageKey": "resource:12",
                "content": "/api/v1/canvas-runtime/resources/12/file",
            }
        },
    )
    media = SimpleNamespace(
        id=12,
        project_id=7,
        format_code=f"{kind}/{'png' if kind == 'image' else 'mpeg'}",
        byte_size=100,
        checksum_sha256="a" * 64,
        storage_locator="minio://managed/object",
        original_name="input",
        duration_ms=1000 if kind != "image" else None,
    )
    return row, media


@pytest.mark.parametrize("kind", ["image", "video", "audio"])
def test_explicit_node_freezes_managed_media_instead_of_display_url(kind):
    row, media = media_fixture(kind)
    looked_up = []
    dao = SimpleNamespace(media=lambda identifier: looked_up.append(identifier) or media)
    frozen = _freeze_node_media(dao, 7, row)
    assert looked_up == [12]
    assert frozen["storage_locator"] == media.storage_locator
    assert frozen["media_id"] == "12" and frozen["kind"] == kind
    assert "/api/" not in frozen["storage_locator"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("byte_size", None),
        ("byte_size", MAX_INPUT_BYTES["image"] + 1),
        ("checksum_sha256", None),
        ("checksum_sha256", "g" * 64),
        ("format_code", "video/mp4"),
    ],
)
def test_invalid_selected_media_is_rejected(field, value):
    row, media = media_fixture()
    setattr(media, field, value)
    with pytest.raises(WorkflowError) as caught:
        _freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)
    assert caught.value.code == "assistant_node_media_unavailable"


@pytest.mark.parametrize("duration", [None, 120001])
def test_unknown_or_long_selected_media_duration_is_rejected(duration):
    row, media = media_fixture("video")
    media.duration_ms = duration
    with pytest.raises(WorkflowError):
        _freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)


@pytest.mark.parametrize("media_project", [None, 8])
def test_canvas_reference_cannot_read_personal_or_other_project_media(media_project):
    row, media = media_fixture()
    media.project_id = media_project
    with pytest.raises(NotFound):
        _freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)
    with pytest.raises(NotFound):
        _freeze_node_media(SimpleNamespace(media=lambda _: None), 7, row)


def test_empty_generator_is_text_context_and_arbitrary_media_url_is_rejected():
    row, _media = media_fixture()
    dao = SimpleNamespace(media=lambda _: pytest.fail("Unmanaged URL must not be fetched"))
    row.content_json = {"metadata": {}}
    assert _freeze_node_media(dao, 7, row) is None
    row.content_json = {"metadata": {"content": "https://external.invalid/image.png"}}
    with pytest.raises(WorkflowError):
        _freeze_node_media(dao, 7, row)


def test_frozen_node_image_is_materialized_with_pixels_and_hash_failure_stops_input():
    row, media = media_fixture()
    source = io.BytesIO()
    Image.new("RGB", (16, 12), "blue").save(source, "PNG")
    data = source.getvalue()
    media.checksum_sha256 = hashlib.sha256(data).hexdigest()
    frozen = _freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)
    prompt = {
        "codec": "agent.attachments",
        "content": "分析",
        "video_audio": "include",
        "attachments": [frozen],
    }
    storage = SimpleNamespace(open=lambda _: io.BytesIO(data))
    parts = materialize_prompt(prompt, SimpleNamespace(), storage)
    assert parts[-1]["media_type"] == "image/jpeg"
    with Image.open(io.BytesIO(base64.urlsafe_b64decode(parts[-1]["data"]))) as pixels:
        assert pixels.size == (16, 12)
    media.checksum_sha256 = "b" * 64
    prompt["attachments"] = [_freeze_node_media(SimpleNamespace(media=lambda _: media), 7, row)]
    with pytest.raises(WorkflowError) as caught:
        materialize_prompt(prompt, SimpleNamespace(), storage)
    assert caught.value.code == "agent_attachment_unavailable"
