from io import BytesIO

import pytest
from PIL import Image

from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import Base
from short_drama.schemas.canvas_resource import CanvasResourceStart
from short_drama.service.canvas_resource_io import inspect_resource, resource_byte_range


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, (200, 0, 100)),
        ("", (200, 0, 100)),
        ("bytes=0-0", (206, 0, 1)),
        ("bytes=90-999", (206, 90, 10)),
        ("bytes=-12", (206, 88, 12)),
        ("bytes=-1000", (206, 0, 100)),
        ("bytes=90-", (206, 90, 10)),
        ("bytes=100-", (416, 0, 0)),
        ("bytes=20-10", (416, 0, 0)),
        ("bytes=-0", (416, 0, 0)),
        ("bytes=0-9223372036854775808", (416, 0, 0)),
        ("bytes=0-1,4-6", (200, 0, 100)),
        ("bytes=a-b", (200, 0, 100)),
        ("bytes=-", (200, 0, 100)),
        ("items=0-1", (200, 0, 100)),
    ],
)
def test_source_range_contract(value, expected):
    assert resource_byte_range(value, 100) == expected


def test_upload_inspection_uses_actual_image_dimensions_and_preserves_stream():
    stream = BytesIO()
    Image.new("RGB", (37, 19), "blue").save(stream, "PNG")
    result = inspect_resource(stream, "image.png", "image", "image/png", Settings(_env_file=None))
    assert (result.width, result.height) == (37, 19)
    assert result.size == len(stream.getvalue()) and len(result.checksum) == 64
    assert stream.tell() == 0
    with pytest.raises(WorkflowError) as invalid:
        inspect_resource(
            BytesIO(b"invalid"), "image.png", "image", "image/png", Settings(_env_file=None)
        )
    assert invalid.value.code == "canvas_resource_invalid"


def test_canvas_file_keeps_its_own_mime_and_does_not_become_fake_media():
    result = inspect_resource(BytesIO(b"glTF"), "model.glb", "file", "", Settings(_env_file=None))
    assert result.mime_type == "model/gltf-binary"
    assert result.width is None
    payload = CanvasResourceStart.model_validate(
        {"fileName": "model.glb", "kind": "file", "size": 4}
    )
    assert payload.file_name == "model.glb"


def test_foreign_key_names_are_globally_unique_before_running_mysql_ddl():
    names = [
        foreign.name
        for table in Base.metadata.tables.values()
        for foreign in table.foreign_key_constraints
    ]
    assert len(names) == len(set(names))


def test_resource_id_overflow_fails_before_a_database_query():
    from short_drama.service.canvas_document import media_references

    with pytest.raises(WorkflowError) as failure:
        list(media_references({"storageKey": "resource:18446744073709551616"}))
    assert failure.value.status_code == 422
