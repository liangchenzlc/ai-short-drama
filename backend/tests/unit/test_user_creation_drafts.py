from types import SimpleNamespace

import pytest
from generation_fixtures import config, generation_session, settings
from pydantic import ValidationError
from sqlalchemy import select
from test_episode_storyboard import setup_storyboard
from test_shot_video_generation import current, request, setup_video

from short_drama.core.exceptions import BusinessError, NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.domain import AIModelConfig, MediaFile, UserCreationDraft
from short_drama.domain.collaboration import ProjectMember, User
from short_drama.schemas.ai_generation import ImageGenerationCreate
from short_drama.schemas.user_creation_draft import UserCreationDraftCreate
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.asset_library_service import AssetLibraryService
from short_drama.service.base import utcnow
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.episode_writing_service import EpisodeWritingService
from short_drama.service.user_creation_draft_service import UserCreationDraftService


def actor(session, project, user_id=1):
    session.info["actor"] = ActorContext(
        user_id, "tester", "test@example.test", True, 1, "", "draft"
    )
    session.info["resource_scope"] = (None, project)
    session.info["request_project"] = project


def setup_draft(session, kind="image"):
    model = config(session, kind)
    project, episode = setup_storyboard(session)
    storyboard = EpisodeStoryboardService(session)
    shot = storyboard.create(
        project,
        episode,
        {
            "storyboard_version": "1",
            "title": "夜雨",
            "script": "林晚走进雨夜街口。",
            "image_settings": {"resolution": "2K", "aspect": "inherit", "layout": "four"},
        },
        "draft-shot",
    )["shot"]
    actor(session, project)
    return SimpleNamespace(
        project=project,
        episode=episode,
        model=model.id,
        shot=shot,
        storyboard=storyboard,
        drafts=UserCreationDraftService(session),
        generations=AIGenerationService(session, settings),
    )


def image_request(flow, draft):
    return {
        "source": {
            "scene": "shot_image",
            "shot_id": flow.shot["id"],
            "layout": "four",
            "context_mode": "saved",
            "row_version": flow.shot["row_version"],
            "context_hash": flow.shot["context_hash"],
            "draft_id": draft["id"],
            "draft_version": draft["draft_version"],
        },
        "input": {"prompt": ""},
        "parameters": {"aspect": "16:9", "resolution": "2K", "count": 1},
    }


def test_drafts_are_per_object_purpose_owner_and_versioned():
    with generation_session() as session:
        flow = setup_draft(session)
        draft = flow.drafts.create_for_scope(
            flow.project,
            flow.episode,
            {
                "object_id": flow.shot["id"],
                "purpose": "storyboard_image",
                "model_config_id": str(flow.model),
                "input_text": "冷光",
            },
        )
        assert draft["row_version"] == draft["draft_version"] == "1"
        assert draft["source_version"] == flow.shot["row_version"]
        assert (
            flow.drafts.get_for_scope(
                flow.project, flow.episode, flow.shot["id"], "storyboard_video"
            )
            is None
        )
        with pytest.raises(WorkflowError, match="草稿已存在"):
            flow.drafts.create_for_scope(
                flow.project,
                flow.episode,
                {
                    "object_id": flow.shot["id"],
                    "purpose": "storyboard_image",
                },
            )
        saved = flow.drafts.update_for_scope(
            flow.project,
            flow.episode,
            flow.shot["id"],
            "storyboard_image",
            {
                "row_version": "1",
                "input_text": "暖光",
                "image_settings": {"count": 2},
            },
        )
        assert saved["row_version"] == saved["draft_version"] == "2"
        with pytest.raises(WorkflowError) as conflict:
            flow.drafts.update_for_scope(
                flow.project,
                flow.episode,
                flow.shot["id"],
                "storyboard_image",
                {"row_version": "1", "input_text": "旧"},
            )
        assert conflict.value.code == "draft_version_conflict"
        assert (
            flow.drafts.get_for_scope(
                flow.project, flow.episode, flow.shot["id"], "storyboard_image"
            )["input_text"]
            == "暖光"
        )
        owned, _ = flow.generations.create("image", image_request(flow, saved), "owner-only")
        with session.begin():
            session.add(
                User(
                    id=2,
                    username="other",
                    display_name="Other",
                    email="o@example.test",
                    password_hash="test",
                    status="active",
                    created_at=utcnow(),
                )
            )
            session.add(
                ProjectMember(
                    id=2, project_id=flow.project, user_id=2, status="active", joined_at=utcnow()
                )
            )
        actor(session, flow.project, 2)
        assert flow.generations.lookup_by_key("image", "owner-only") is None
        with pytest.raises(NotFound):
            flow.generations.detail(owned["generation_id"])
        assert flow.drafts.list_for_episode(flow.project, flow.episode) == []
        assert (
            flow.drafts.get_for_scope(
                flow.project, flow.episode, flow.shot["id"], "storyboard_image"
            )
            is None
        )
        with session.begin():
            assert (
                session.scalar(
                    select(UserCreationDraft).where(UserCreationDraft.id == int(draft["id"]))
                )
                is None
            )
        with pytest.raises(NotFound):
            flow.generations.create("image", image_request(flow, saved), "foreign-draft")


def test_saved_private_image_values_enter_frozen_request_without_mutating_work():
    with generation_session() as session:
        flow = setup_draft(session)
        draft = flow.drafts.create_for_scope(
            flow.project,
            flow.episode,
            {
                "object_id": flow.shot["id"],
                "purpose": "storyboard_image",
                "model_config_id": str(flow.model),
                "input_text": "只使用侧光",
                "image_settings": {"aspect": "9:16", "resolution": "4K", "count": 2},
            },
        )
        body = image_request(flow, draft)
        result, created = flow.generations.create("image", body, "private-image")
        assert created
        detail = flow.generations.detail(result["generation_id"])
        assert detail["parameters"] == {"aspect": "9:16", "resolution": "4K", "count": 2}
        assert "林晚走进雨夜" in detail["input"]["prompt"]
        assert "只使用侧光" in detail["input"]["prompt"]
        assert detail["source"]["layout"] == "four"
        assert detail["source_snapshot"]["creation_draft"]["draft_version"] == "1"
        assert flow.storyboard.get(flow.project, flow.episode, flow.shot["id"])["shot"] == flow.shot
        assert flow.generations.lookup_by_key("image", "unknown") is None
        assert flow.generations.lookup_by_key("video", "private-image") is None
        assert (
            flow.generations.lookup_by_key("image", "private-image")["generation_id"]
            == result["generation_id"]
        )
        flow.drafts.update_for_scope(
            flow.project,
            flow.episode,
            flow.shot["id"],
            "storyboard_image",
            {"row_version": "1", "input_text": "新输入"},
        )
        assert (
            flow.generations.detail(result["generation_id"])["source_snapshot"]
            == detail["source_snapshot"]
        )
        replay, created = flow.generations.create("image", body, "private-image")
        assert not created and replay["generation_id"] == result["generation_id"]
        with pytest.raises(WorkflowError) as changed:
            flow.generations.create("image", body, "new-with-old-draft")
        assert changed.value.code == "draft_version_conflict"


@pytest.mark.parametrize("prompt_length", [6, 16000])
def test_private_video_keeps_current_adopted_image_script_and_source_checks(prompt_length):
    with generation_session() as session:
        flow = setup_video(session)
        with session.begin():
            frame = session.get(MediaFile, 101)
            frame.scope_user_id, frame.project_id, frame.published_at = None, flow.project, utcnow()
        actor(session, flow.project)
        shot = current(flow)
        drafts = UserCreationDraftService(session)
        prompt = "缓慢推近人物" + "x" * (prompt_length - 6)
        draft = drafts.create_for_scope(
            flow.project,
            flow.episode,
            {
                "object_id": flow.shot_id,
                "purpose": "storyboard_video",
                "model_config_id": "1",
                "input_text": prompt,
                "video_settings": {"duration_ms": 7000, "resolution": "480p"},
            },
        )
        body = request(shot)
        body["source"].update(draft_id=draft["id"], draft_version=draft["draft_version"])
        result, _ = flow.generations.create("video", body, "private-video")
        detail = flow.generations.detail(result["generation_id"])
        assert detail["parameters"]["duration_ms"] == 7000
        assert detail["parameters"]["resolution"] == "480p"
        assert detail["input"]["reference_media_ids"] == ["101"]
        assert prompt in detail["input"]["prompt"]
        assert "林晚先抬眼" in detail["input"]["prompt"]
        assert current(flow)["duration_ms"] == 5000
        assert current(flow)["video_prompt"] == shot["video_prompt"]
        assert current(flow)["video_settings"] == shot["video_settings"]
        body["source"]["reference_media_id"] = "102"
        with pytest.raises(WorkflowError) as conflict:
            flow.generations.create("video", body, "changed-image")
        assert conflict.value.code == "shot_version_conflict"


def test_private_references_are_persistent_deduplicated_and_limited_with_shared():
    with generation_session() as session:
        flow = setup_draft(session)
        with session.begin():
            session.add_all(
                [
                    MediaFile(
                        id=100 + i,
                        format_code="image/png",
                        storage_locator=f"minio://images/{i}.png",
                        original_name="private",
                        scope_user_id=1 if i else None,
                        project_id=None if i else flow.project,
                        published_at=None if i else utcnow(),
                    )
                    for i in range(17)
                ]
            )
        asset, _ = AssetLibraryService(session).create(
            "episode",
            flow.episode,
            flow.project,
            {
                "kind": "prop",
                "name": "伞",
                "description": "红伞",
            },
            "reference-prop",
        )
        # 共享来源有一张，私人草稿额外有16张，总计17必须拒绝。
        with session.begin():
            from short_drama.domain import Asset

            shared = session.get(Asset, asset.id)
            shared.reference_media_ids = ["100"]
        draft = flow.drafts.create_for_scope(
            flow.project,
            flow.episode,
            {
                "object_id": str(asset.id),
                "purpose": "asset_image",
                "model_config_id": "1",
                "reference_media_ids": [str(i) for i in range(101, 117)],
            },
        )
        assert len(draft["reference_images"]) == 16
        body = {
            "source": {
                "scene": "asset_image",
                "asset_id": str(asset.id),
                "project_id": str(flow.project),
                "episode_id": str(flow.episode),
                "row_version": str(asset.row_version),
                "draft_id": draft["id"],
                "draft_version": draft["draft_version"],
            },
            "input": {"prompt": ""},
        }
        with pytest.raises(WorkflowError) as limit:
            flow.generations.create("image", body, "17-references")
        assert limit.value.code == "reference_limit_exceeded"
        saved = flow.drafts.update_for_scope(
            flow.project,
            flow.episode,
            asset.id,
            "asset_image",
            {
                "row_version": "1",
                "reference_media_ids": [str(i) for i in range(100, 116)],
            },
        )
        body["source"]["draft_version"] = saved["draft_version"]
        result, _ = flow.generations.create("image", body, "16-references")
        assert (
            len(flow.generations.detail(result["generation_id"])["input"]["reference_media_ids"])
            == 16
        )


def test_novel_draft_uses_saved_text_and_parameters_never_change_shared_novel():
    with generation_session() as session:
        flow = setup_draft(session, "text")
        writing = EpisodeWritingService(session)
        saved = writing.save_novel(
            flow.project, flow.episode, {"content_version": "1", "content": "夜雨中的故事。"}
        )
        draft = flow.drafts.create_for_scope(
            flow.project,
            flow.episode,
            {
                "object_id": str(flow.episode),
                "purpose": "novel_adaptation",
                "model_config_id": "1",
                "input_text": "保留悬念",
            },
        )
        result, _ = flow.generations.create(
            "text",
            {
                "source": {
                    "scene": "novel_script",
                    "project_id": str(flow.project),
                    "episode_id": str(flow.episode),
                    "content_version": saved["content_version"],
                    "draft_id": draft["id"],
                    "draft_version": "1",
                }
            },
            "private-novel",
        )
        detail = flow.generations.detail(result["generation_id"])
        assert detail["source_snapshot"]["content"] == "夜雨中的故事。"
        assert detail["source_snapshot"]["creation_draft"]["input_text"] == "保留悬念"
        assert writing.get(flow.project, flow.episode)["novel"]["content"] == "夜雨中的故事。"


@pytest.mark.parametrize(
    "extra",
    [
        {"user_id": "2"},
        {"project_id": "3"},
        {"input_text": "x" * 4001},
        {"image_settings": {"layout": "nine"}},
        {"reference_media_ids": [str(i) for i in range(1, 18)]},
        {"image_settings": {"count": 3}},
    ],
)
def test_draft_contract_rejects_client_ownership_and_invalid_parameters(extra):
    with pytest.raises(ValidationError):
        UserCreationDraftCreate.model_validate(
            {"object_id": "1", "purpose": "asset_image", **extra}
        )


def test_source_draft_tokens_are_paired_and_require_saved_business_context():
    for source in [
        {"scene": "asset_image", "asset_id": "1", "row_version": "1", "draft_id": "2"},
        {
            "scene": "shot_image",
            "shot_id": "1",
            "layout": "single",
            "draft_id": "2",
            "draft_version": "1",
        },
    ]:
        with pytest.raises(ValidationError):
            ImageGenerationCreate.model_validate({"source": source, "input": {"prompt": "x"}})


def test_shared_source_changes_require_current_tokens_without_rebasing_private_content():
    with generation_session() as session:
        flow = setup_draft(session)
        draft = flow.drafts.create_for_scope(
            flow.project,
            flow.episode,
            {
                "object_id": flow.shot["id"],
                "purpose": "storyboard_image",
                "model_config_id": str(flow.model),
                "input_text": "保留侧光",
            },
        )
        old_request = image_request(flow, draft)
        flow.shot = flow.storyboard.update(
            flow.project,
            flow.episode,
            flow.shot["id"],
            {"row_version": "1", "script": "人物离开街口。"},
        )["shot"]
        with pytest.raises(WorkflowError) as stale:
            flow.generations.create("image", old_request, "stale-shared")
        assert stale.value.code == "shot_version_conflict"
        result, _ = flow.generations.create("image", image_request(flow, draft), "latest-shared")
        snapshot = flow.generations.detail(result["generation_id"])["source_snapshot"]
        assert snapshot["creation_draft"]["source_version"] == "1"
        assert snapshot["creation_draft"]["current_source_version"] == "2"
        assert "人物离开" in snapshot["shot"]["script"]
        assert snapshot["creation_draft"]["input_text"] == "保留侧光"


@pytest.mark.parametrize("wrong_scope", ["object", "purpose"])
def test_generation_cannot_use_another_object_or_purpose_draft(wrong_scope):
    with generation_session() as session:
        flow = setup_draft(session)
        draft = flow.drafts.create_for_scope(
            flow.project,
            flow.episode,
            {
                "object_id": flow.shot["id"],
                "purpose": "storyboard_video" if wrong_scope == "purpose" else "storyboard_image",
            },
        )
        body = image_request(flow, draft)
        if wrong_scope == "object":
            second = flow.storyboard.create(
                flow.project,
                flow.episode,
                {"storyboard_version": "2", "title": "另一镜", "script": "场景切换。"},
                "another-shot",
            )["shot"]
            body["source"]["shot_id"] = second["id"]
        with pytest.raises(WorkflowError) as scope:
            flow.generations.create("image", body, f"wrong-{wrong_scope}")
        assert scope.value.code == "creation_draft_scope_mismatch"


def test_unfinished_draft_can_be_saved_but_generation_requires_available_model():
    with generation_session() as session:
        flow = setup_draft(session)
        draft = flow.drafts.create_for_scope(
            flow.project,
            flow.episode,
            {"object_id": flow.shot["id"], "purpose": "storyboard_image", "input_text": "侧光"},
        )
        with pytest.raises(WorkflowError) as missing:
            flow.generations.create("image", image_request(flow, draft), "no-model")
        assert missing.value.code == "creation_draft_model_required"
        draft = flow.drafts.update_for_scope(
            flow.project,
            flow.episode,
            flow.shot["id"],
            "storyboard_image",
            {"row_version": "1", "model_config_id": str(flow.model)},
        )
        with session.begin():
            model = session.get(AIModelConfig, flow.model)
            model.enabled, model.is_default = 0, 0
        with pytest.raises(BusinessError, match="Selected model is unavailable"):
            flow.generations.create("image", image_request(flow, draft), "disabled-model")


@pytest.mark.parametrize("foreign_reference", ["model", "media"])
def test_draft_cannot_store_another_users_model_or_private_media(foreign_reference):
    with generation_session() as session:
        flow = setup_draft(session)
        session.info.pop("actor")
        session.info["legacy_user_id"] = 2
        with session.begin():
            session.add(
                User(
                    id=2,
                    username="foreign",
                    display_name="Foreign",
                    email="foreign@example.test",
                    password_hash="test-only",
                    status="active",
                    created_at=utcnow(),
                )
            )
            session.flush()
            if foreign_reference == "model":
                session.add(
                    AIModelConfig(
                        id=2,
                        service_type="image",
                        name="Foreign",
                        model_key="model",
                        provider="test",
                        base_url="https://invalid.test",
                        apikey="test-only",
                        owner_user_id=2,
                        enabled=1,
                        is_default=0,
                        is_deleted=0,
                        row_version=1,
                    )
                )
            else:
                session.add(
                    MediaFile(
                        id=2,
                        format_code="image/png",
                        original_name="Foreign",
                        storage_locator="minio://images/foreign.png",
                        scope_user_id=2,
                        published_at=None,
                    )
                )
        session.info["legacy_user_id"] = 1
        actor(session, flow.project)
        extra = (
            {"model_config_id": "2"}
            if foreign_reference == "model"
            else {"reference_media_ids": ["2"]}
        )
        with pytest.raises(NotFound):
            flow.drafts.create_for_scope(
                flow.project,
                flow.episode,
                {"object_id": flow.shot["id"], "purpose": "storyboard_image", **extra},
            )
        assert flow.drafts.list_for_episode(flow.project, flow.episode) == []


def test_legacy_draft_without_source_version_keeps_null_in_frozen_snapshot():
    with generation_session() as session:
        flow = setup_draft(session)
        draft = flow.drafts.create_for_scope(
            flow.project,
            flow.episode,
            {
                "object_id": flow.shot["id"],
                "purpose": "storyboard_image",
                "model_config_id": str(flow.model),
            },
        )
        with session.begin():
            session.get(UserCreationDraft, int(draft["id"])).source_version = None
        result, _ = flow.generations.create("image", image_request(flow, draft), "legacy-version")
        frozen = flow.generations.detail(result["generation_id"])["source_snapshot"][
            "creation_draft"
        ]
        assert frozen["source_version"] is None
        assert frozen["current_source_version"] == "1"


@pytest.mark.parametrize(
    ("purpose", "length"),
    [
        ("novel_adaptation", 4000),
        ("asset_image", 4000),
        ("storyboard_image", 4000),
        ("storyboard_video", 4001),
        ("storyboard_video", 16000),
    ],
)
def test_draft_prompt_limits_match_each_purpose(purpose, length):
    parsed = UserCreationDraftCreate.model_validate(
        {"object_id": "1", "purpose": purpose, "input_text": "x" * length}
    )
    assert len(parsed.input_text) == length


@pytest.mark.parametrize(
    ("purpose", "length"),
    [
        ("novel_adaptation", 4001),
        ("asset_image", 4001),
        ("storyboard_image", 4001),
        ("storyboard_video", 16001),
    ],
)
def test_oversize_private_prompt_is_rejected_for_each_purpose(purpose, length):
    with pytest.raises(ValidationError):
        UserCreationDraftCreate.model_validate(
            {"object_id": "1", "purpose": purpose, "input_text": "x" * length}
        )


@pytest.mark.parametrize("purpose", ["storyboard_image", "storyboard_video"])
def test_private_prompt_patch_uses_purpose_limit_and_preserves_draft_on_rejection(purpose):
    with generation_session() as session:
        flow = setup_draft(session)
        draft = flow.drafts.create_for_scope(
            flow.project,
            flow.episode,
            {"object_id": flow.shot["id"], "purpose": purpose},
        )
        maximum = 16000 if purpose == "storyboard_video" else 4000
        saved = flow.drafts.update_for_scope(
            flow.project,
            flow.episode,
            flow.shot["id"],
            purpose,
            {"row_version": draft["row_version"], "input_text": "x" * maximum},
        )
        with pytest.raises((ValidationError, WorkflowError)) as rejected:
            flow.drafts.update_for_scope(
                flow.project,
                flow.episode,
                flow.shot["id"],
                purpose,
                {"row_version": saved["row_version"], "input_text": "x" * (maximum + 1)},
            )
        if purpose == "storyboard_image":
            assert isinstance(rejected.value, WorkflowError)
            assert rejected.value.code == "invalid_creation_draft"
            assert rejected.value.status_code == 422
        assert (
            flow.drafts.get_for_scope(flow.project, flow.episode, flow.shot["id"], purpose) == saved
        )
