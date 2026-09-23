import json

import pytest
from pydantic import ValidationError

from short_drama import schemas


@pytest.mark.parametrize("value", [True, False, 1.5, 1.0, "1.5", "-1", "1e3", " 1", 0, 2**64])
def test_ids_reject_non_decimal_or_out_of_range_inputs(value):
    with pytest.raises(ValidationError):
        schemas.EpisodeNovelCreate(episode_id=value)


@pytest.mark.parametrize("value", [1, "1", 2**64 - 1, str(2**64 - 1)])
def test_ids_accept_ints_and_decimal_strings(value):
    novel = schemas.EpisodeNovelCreate(episode_id=value)
    assert novel.episode_id == int(value)
    assert novel.model_dump()["episode_id"] == int(value)
    assert json.loads(novel.model_dump_json())["episode_id"] == str(value)


def test_patch_distinguishes_omitted_nullable_and_invalid_null():
    assert schemas.ProjectUpdate().model_dump(exclude_unset=True) == {}
    assert schemas.ProjectUpdate(last_opened_at=None).model_dump(exclude_unset=True) == {
        "last_opened_at": None
    }
    with pytest.raises(ValidationError):
        schemas.ProjectUpdate(name=None)
    with pytest.raises(ValidationError):
        schemas.EpisodeUpdate(project_id=3)
    with pytest.raises(ValidationError):
        schemas.EpisodeScriptUpdate(state="confirmed")


def test_read_handles_legacy_nullable_times_and_large_identifiers():
    from short_drama.domain import EpisodeNovel

    item = EpisodeNovel(id=2**63, episode_id=2**63 + 1, content="text")
    result = schemas.EpisodeNovelRead.model_validate(item)
    assert result.created_at is None
    assert result.model_dump(mode="json")["id"] == str(2**63)


def test_ai_secrets_and_version_controls():
    item = schemas.AIModelConfigCreate(
        service_type="text", name="test", model_key="m", provider="p", apikey="private"
    )
    assert item.apikey.get_secret_value() == "private"
    assert "private" not in repr(item)
    with pytest.raises(ValidationError):
        schemas.AIModelConfigUpdate(name="changed")
    for key in ("service_type", "is_deleted", "is_default", "created_at"):
        with pytest.raises(ValidationError):
            schemas.AIModelConfigUpdate(row_version=1, **{key: 1})
    assert "apikey" not in schemas.AIModelConfigRead.model_fields
    assert schemas.AIModelConfigRead.model_fields["has_api_key"].default is False


@pytest.mark.parametrize(
    "values",
    [
        {"name": " "},
        {"name": "x" * 121},
        {"aspect": "1:1"},
    ],
)
def test_project_bounds(values):
    data = {"name": "project", "aspect": "16:9"} | values
    with pytest.raises(ValidationError):
        schemas.ProjectCreate(**data)


def test_mediumtext_limit_counts_utf8_bytes():
    text = "a" * (2**24 - 1)
    assert schemas.EpisodeNovelCreate(episode_id=1, content=text).content == text
    with pytest.raises(ValidationError):
        schemas.EpisodeNovelCreate(episode_id=1, content="😀" * (2**22))


def test_media_metadata_constraints_and_immutable_identity():
    for values in (
        {"width": 0},
        {"height": 2**32},
        {"duration_ms": 5},
        {"checksum_sha256": "short"},
        {"format_code": "audio/wav"},
    ):
        with pytest.raises(ValidationError):
            schemas.MediaFileCreate(
                **({"format_code": "image/png", "storage_locator": "a"} | values)
            )
    with pytest.raises(ValidationError):
        schemas.MediaFileUpdate(storage_locator="other")
    assert (
        schemas.MediaFileCreate(
            format_code="video/mp4", storage_locator="a", duration_ms=1
        ).duration_ms
        == 1
    )


@pytest.mark.parametrize("checksum", ["g" * 64, "z" + "0" * 63, "\x00" * 64, " " * 64])
@pytest.mark.parametrize("schema_name", ["MediaFileCreate", "MediaFileUpdate", "MediaFileRead"])
def test_media_checksum_rejects_nonhex_and_control_characters(checksum, schema_name):
    values = {"checksum_sha256": checksum}
    if schema_name != "MediaFileUpdate":
        values.update(format_code="image/png", storage_locator="a")
    if schema_name == "MediaFileRead":
        values.update(id=1, original_name="")
    with pytest.raises(ValidationError):
        getattr(schemas, schema_name)(**values)


@pytest.mark.parametrize(
    "format_code",
    [
        "image/",
        "video/",
        "image/png x",
        "image/a/b",
        "video/mp4;codecs=h264",
        "image/\x00",
        "image/.png",
    ],
)
@pytest.mark.parametrize("schema_name", ["MediaFileCreate", "MediaFileRead"])
def test_media_mime_rejects_empty_and_invalid_subtypes(format_code, schema_name):
    values = {"format_code": format_code, "storage_locator": "a"}
    if schema_name == "MediaFileRead":
        values.update(id=1, original_name="")
    with pytest.raises(ValidationError):
        getattr(schemas, schema_name)(**values)


@pytest.mark.parametrize(
    "format_code", ["image/png", "image/svg+xml", "video/mp4", "video/vnd.example-v1", "demo:image"]
)
@pytest.mark.parametrize("schema_name", ["MediaFileCreate", "MediaFileRead"])
def test_media_mime_and_hex_checksum_accept_canonical_values(format_code, schema_name):
    checksum = "0123456789abcdef" * 3 + "0123456789ABCDEF"
    values = {"format_code": format_code, "storage_locator": "a", "checksum_sha256": checksum}
    if schema_name == "MediaFileRead":
        values.update(id=1, original_name="")
    result = getattr(schemas, schema_name)(**values)
    assert result.format_code == format_code
    assert result.checksum_sha256 == checksum
    assert schemas.MediaFileUpdate(checksum_sha256=checksum).checksum_sha256 == checksum


def test_recycle_requires_exactly_image_or_video_parameter_shape():
    base = {"shot_id": 1, "media_id": 2, "resolution": "2K", "reason": "discarded"}
    for values in ({}, {"layout": "single"}, {"layout": "single", "aspect": "1:1", "duration": 5}):
        with pytest.raises(ValidationError):
            schemas.MediaRecycleBinCreate(**(base | values))
    assert schemas.MediaRecycleBinCreate(**base, duration=1).duration == 1
    assert schemas.MediaRecycleBinCreate(**base, layout="single", aspect="1:1").aspect == "1:1"


def test_records_are_immutable_and_creation_rejects_system_fields():
    for name in ("ScriptShotRecord", "NovelScriptRecord", "MediaRecycleBin"):
        assert not hasattr(schemas, name + "Update")
    for values in ({"id": 1}, {"created_at": None}, {"updated_by": 2}):
        with pytest.raises(ValidationError):
            schemas.EpisodeNovelCreate(episode_id=1, **values)


@pytest.mark.parametrize(
    "class_name", [name for name in schemas.__all__ if name.endswith("Update")]
)
def test_every_update_rejects_system_fields_and_null_for_nonnullable_columns(class_name):
    from short_drama import domain

    model = getattr(domain, class_name.removesuffix("Update"))
    schema = getattr(schemas, class_name)
    initial = {"row_version": 1} if class_name == "AIModelConfigUpdate" else {}
    for field in ("id", "created_at", "created_by", "updated_at", "updated_by"):
        with pytest.raises(ValidationError):
            schema(**(initial | {field: 1}))
    for field in schema.model_fields:
        if not model.__table__.c[field].nullable:
            with pytest.raises(ValidationError):
                schema(**(initial | {field: None}))


@pytest.mark.parametrize("class_name", [name for name in schemas.__all__ if name.endswith("Read")])
def test_read_schema_covers_exact_domain_columns_except_secret(class_name):
    from short_drama import domain

    model = getattr(domain, class_name.removesuffix("Read"))
    expected = set(model.__table__.columns.keys())
    if class_name == "AIModelConfigRead":
        expected.remove("apikey")
        expected.remove("capability_cache")
        expected.add("has_api_key")
    if class_name == "AssetRead":
        expected -= {
            "creation_key",
            "creation_hash",
            "model_id",
            "created_by",
            "updated_by",
        }
        expected -= {"reference_media_ids"}  # Exposed by the dedicated input-image endpoint.
        expected |= {"image", "reference_count"}
    if class_name == "ShotScriptRead":
        expected -= {"active_position", "creation_key", "creation_hash", "reference_media_ids"}
    assert set(getattr(schemas, class_name).model_fields) == expected
