from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.support.generation_fixtures import config, generation_session, settings


def request(**overrides):
    body = {
        "input": {"prompt": ""},
        "source": {"scene": "asset_image", "asset_id": "101", "row_version": "7"},
    }
    body.update(overrides)
    return body


def test_asset_image_accepts_empty_supplement_and_defaults_to_one():
    from short_drama.schemas.generations.admission.generation import ImageGenerationCreate

    parsed = ImageGenerationCreate.model_validate(request())
    assert parsed.source.scene == "asset_image"
    assert parsed.parameters.count == 1


@pytest.mark.parametrize(
    "change",
    [
        {"name": "other"},
        {"description": "other"},
        {"prompt": "other"},
        {"label": "other"},
        {"tags": ["other"]},
        {"scene_time": "night"},
    ],
)
def test_asset_content_hash_changes_for_generation_content(change):
    from short_drama.service.creation.assets.images.image_context import (
        asset_image_content,
        asset_image_content_hash,
    )

    values = dict(
        kind="scene",
        name="station",
        description="wet",
        prompt="cinematic",
        label="exterior",
        tags=["rain", "night"],
        scene_time="dusk",
        row_version=1,
        state="unconfirmed",
        media_id=None,
        model_id=None,
    )
    original = SimpleNamespace(**values)
    changed = SimpleNamespace(**{**values, **change})
    assert asset_image_content_hash(asset_image_content(original)) != asset_image_content_hash(
        asset_image_content(changed)
    )


def test_asset_content_hash_ignores_state_image_version_and_tag_order():
    from short_drama.service.creation.assets.images.image_context import (
        asset_image_content,
        asset_image_content_hash,
    )

    base = dict(
        kind="character",
        name="Lin",
        description="detective",
        prompt="coat",
        label="lead",
        tags=["adult", "calm"],
        scene_time="",
        row_version=1,
        state="unconfirmed",
        media_id=None,
        model_id=None,
    )
    changed = {
        **base,
        "tags": ["calm", "adult", "adult"],
        "row_version": 9,
        "state": "confirmed",
        "media_id": 8,
        "model_id": 7,
    }
    assert asset_image_content_hash(asset_image_content(SimpleNamespace(**base))) == (
        asset_image_content_hash(asset_image_content(SimpleNamespace(**changed)))
    )


def test_asset_profile_is_in_generation_context_and_changes_the_hash():
    from short_drama.service.creation.assets.images.image_context import (
        asset_image_content,
        asset_image_content_hash,
    )
    from short_drama.service.creation.storyboard.management.context import normalize_shot_context

    values = {
        "id": 101,
        "kind": "character",
        "name": "Lin",
        "description": "reporter",
        "prompt": "",
        "label": "",
        "tags": [],
        "scene_time": "",
        "profile": {"appearance": "short black hair"},
        "media_id": None,
        "state": "unconfirmed",
    }
    original = SimpleNamespace(**values)
    changed = SimpleNamespace(**{**values, "profile": {"appearance": "white hair"}})
    content = asset_image_content(original)
    assert content["profile"] == values["profile"]
    assert asset_image_content_hash(content) != asset_image_content_hash(
        asset_image_content(changed)
    )
    shot = normalize_shot_context(shot_id=1, script="", duration_ms=3000, assets=[original])
    assert shot["assets"][0]["profile"] == values["profile"]


@pytest.mark.parametrize(
    "body",
    [
        request(input={"prompt": "x" * 4001}),
        {"input": {"prompt": ""}, "source": {"scene": "asset_image", "asset_id": "1"}},
        {"input": {"prompt": ""}},
    ],
)
def test_asset_image_rejects_invalid_client_context(body):
    from short_drama.schemas.generations.admission.generation import ImageGenerationCreate

    with pytest.raises(ValidationError):
        ImageGenerationCreate.model_validate(body)


def test_asset_image_create_freezes_saved_content_without_mutating_asset():
    from sqlalchemy import select

    from short_drama.domain import AIGenerationRecord, Asset
    from short_drama.service.generations.admission.generation import AIGenerationService

    with generation_session() as session:
        config(session)
        from short_drama.service.projects.management.project import ProjectService

        project = ProjectService(session).create({"name": "Image generation"})
        asset = Asset(
            id=101,
            project_id=int(project.id),
            position=1,
            kind="prop",
            name="umbrella",
            label="weather",
            description="red",
            prompt="studio photo",
            model_id=None,
            media_id=None,
            row_version=7,
            state="unconfirmed",
            tags=["rain"],
            scene_time="",
            creation_key=None,
            creation_hash=None,
        )
        session.add(asset)
        session.commit()
        summary, created = AIGenerationService(session, settings).create(
            "image",
            request(
                input={"prompt": "umbrella studio photo"},
                source={
                    "scene": "asset_image",
                    "asset_id": "101",
                    "row_version": "7",
                    "project_id": str(project.id),
                },
            ),
            "asset-image-1",
        )
        record = session.scalar(select(AIGenerationRecord))
        assert created and summary["source"]["scene"] == "asset_image"
        assert record.request_data["source_snapshot"]["asset"]["name"] == "umbrella"
        assert record.request_data["source_snapshot"]["asset_content_hash"]
        assert "umbrella studio photo" in record.request_data["input"]["prompt"]
        assert "red" in record.request_data["input"]["prompt"]
        assert (asset.media_id, asset.model_id, asset.state, asset.row_version) == (
            None,
            None,
            "unconfirmed",
            7,
        )


def test_asset_image_generation_uses_saved_profile_in_the_model_prompt():
    from sqlalchemy import select

    from short_drama.domain import AIGenerationRecord, Asset
    from short_drama.service.generations.admission.generation import AIGenerationService
    from short_drama.service.projects.management.project import ProjectService

    with generation_session() as session:
        config(session)
        project = ProjectService(session).create({"name": "Profile generation"})
        asset = Asset(
            id=101,
            project_id=int(project.id),
            position=1,
            kind="character",
            name="Lin",
            profile={"appearance": "short black hair"},
            row_version=1,
        )
        session.add(asset)
        session.commit()
        AIGenerationService(session, settings).create(
            "image",
            request(
                input={"prompt": "dramatic lighting"},
                source={
                    "scene": "asset_image",
                    "asset_id": "101",
                    "row_version": "1",
                    "project_id": str(project.id),
                },
            ),
            "asset-profile-image-1",
        )
        generated = session.scalar(select(AIGenerationRecord)).request_data
        assert generated["source_snapshot"]["asset"]["profile"] == asset.profile
        assert "short black hair" in generated["input"]["prompt"]
        assert "dramatic lighting" in generated["input"]["prompt"]


def test_asset_image_accepts_reference_inputs():
    from short_drama.schemas.generations.admission.generation import ImageGenerationCreate

    parsed = ImageGenerationCreate.model_validate(
        request(input={"prompt": "", "reference_media_ids": ["9"]})
    )
    assert parsed.input.reference_media_ids == [9]
