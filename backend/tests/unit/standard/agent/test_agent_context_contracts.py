"""Bounded Agent context and local media processing, without paid model calls."""

import base64
import hashlib
import io
import subprocess
import wave
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from short_drama.agent.context.capabilities import input_capabilities
from short_drama.agent.context.media import inspect_attachment, materialize_prompt
from short_drama.agent.skill.packages import builtin_skill_packages
from short_drama.core.exceptions import NotFound, WorkflowError
from short_drama.core.identity import ActorContext
from short_drama.schemas.assistant.context.context import SkillPatch, SkillSelection
from short_drama.schemas.assistant.operations.runtime import MessageCreate
from short_drama.service.assistant.context.attachment import (
    AgentAttachmentService,
    freeze_attachments,
)
from short_drama.service.creation.assembly.rendering.renderer import executable
from short_drama.service.skills.catalog.skill import AgentSkillService
from short_drama.service.skills.catalog.versions import markdown_content


def test_bundled_methods_cover_nine_capabilities_with_valid_independent_packages():
    packages = builtin_skill_packages()
    assert len(packages) == 12
    assert {item.capability for item in packages} == {
        "planning",
        "writing",
        "assets",
        "storyboard",
        "image_prompts",
        "video_prompts",
        "voice",
        "canvas",
        "continuity",
    }
    for item in packages:
        assert item.content.instructions and item.content.description and item.content.exclusions
        resources = {resource.path: resource for resource in item.content.resources}
        assert resources["assets/output-template.md"].kind == "template"
        assert resources["references/method.md"].content
        if item.key != "canvas.v1":
            assert (
                "Copyright (c) 2026 drama-skills contributors"
                in resources["references/upstream-license.txt"].content
            )
    assert next(item for item in packages if item.key == "canvas.v1").content.modes == [
        "infinite_canvas"
    ]


def test_bundled_publication_keeps_original_eight_builtin_versions_exact():
    from scripts.agent_system_migration import LEGACY_BUILTIN_TEXTS

    assert len(LEGACY_BUILTIN_TEXTS) == 8
    upgraded = {item.key: item for item in builtin_skill_packages()}
    assert all(upgraded[key].content_version == 2 for key in LEGACY_BUILTIN_TEXTS)
    assert all(
        upgraded[key].content.instructions != body for key, body in LEGACY_BUILTIN_TEXTS.items()
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"attachment_ids": ["1", "1"]},
        {"attachment_ids": [str(index) for index in range(1, 18)]},
        {"skills": [{"id": "script.v1", "content_version": "1"}] * 2},
        {"skills": [{"id": str(index), "content_version": "1"} for index in range(9)]},
        {"video_audio": "ignore"},
    ],
)
def test_message_rejects_unbounded_or_ambiguous_context(changes):
    with pytest.raises(ValidationError):
        MessageCreate(content="Review these inputs", **changes)


@pytest.mark.parametrize(
    ("data", "filename"),
    [
        (b"", "empty.md"),
        (b" ", "blank.md"),
        (b"a\x00b", "nul.md"),
        (b"\xff", "invalid.md"),
        (b"a" * 65537, "large.md"),
        (b"# Skill", "skill.zip"),
    ],
    ids=["empty", "blank", "nul", "encoding", "size", "suffix"],
)
def test_skill_upload_requires_single_bounded_utf8_markdown(data, filename):
    with pytest.raises(WorkflowError) as caught:
        markdown_content(data, filename)
    assert caught.value.code == "invalid_agent_skill"


def test_skill_upload_normalizes_filename_and_keeps_only_instructions():
    assert markdown_content(b"\xef\xbb\xbf# Instructions\n", "folder\\story.md") == (
        "story.md",
        "# Instructions",
    )


def test_skill_local_links_allow_sibling_files_and_report_malformed_targets():
    from short_drama.schemas.skills.catalog.skill import SkillResourceInput
    from short_drama.service.skills.packages.skill_package import local_reference_issues

    resources = [
        SkillResourceInput(path="references/guide.md", content="[模板](../assets/shot.json)"),
        SkillResourceInput(path="assets/shot.json", kind="template", content="{}"),
    ]
    assert local_reference_issues("[指南](references/guide.md)", resources) == []
    assert "无法识别" in local_reference_issues("[错误](http://[)", resources)[0]


def test_skill_online_content_defaults_and_resource_paths():
    from short_drama.schemas.skills.catalog.skill import SkillCreate

    created = SkillCreate(
        name="分镜方法",
        slug="shot-method",
        description="将主动选入的正文拆为分镜。",
        instructions="先核对输入，再规划镜头。",
        resources=[{"path": "references/shots.md", "content": "# 镜头语言"}],
    )
    assert created.enabled and "invocation_mode" not in created.model_dump()
    assert created.modes == ["standard", "infinite_canvas"]
    assert created.resources[0].kind == "reference"
    for path in ("../secret.md", "C:/secret.md", "a\\b.md", "/secret.md", "a/%2e%2e/x.md"):
        with pytest.raises(ValidationError):
            SkillCreate(**{**created.model_dump(), "resources": [{"path": path, "content": "x"}]})


def test_skill_markdown_import_reads_frontmatter_without_execution():
    from short_drama.service.skills.packages.skill_package import parse_skill_import

    preview = parse_skill_import(
        b"---\nname: quiet-writing\ndescription: Concise dialogue.\n"
        b"metadata:\n  short-drama-display-name: Quiet writing\n---\n# Method\nKeep it short.",
        "SKILL.md",
    )
    assert preview.compatible and preview.issues == []
    assert preview.draft.slug == "quiet-writing"
    assert preview.draft.name == "Quiet writing"
    assert preview.draft.instructions == "# Method\nKeep it short."
    assert preview.draft.enabled and "invocation_mode" not in preview.draft.model_dump()


@pytest.mark.parametrize(
    "resource",
    [
        {"path": "scripts/guide.md", "kind": "reference", "content": "text"},
        {"path": "references/guide.md", "kind": "script", "content": "text"},
        {"path": "references/run.py", "kind": "reference", "content": "text"},
    ],
)
def test_skill_direct_writes_cannot_bypass_script_rejection(resource):
    from short_drama.schemas.skills.catalog.skill import SkillCreate, SkillPackagePatch

    with pytest.raises(ValidationError):
        SkillCreate(
            name="Method",
            slug="method",
            description="Method",
            instructions="Body",
            resources=[resource],
        )
    with pytest.raises(ValidationError):
        SkillPackagePatch(row_version="1", resources=[resource])


def test_skill_import_requires_metadata_and_distinguishes_absent_tags():
    from short_drama.service.skills.packages.skill_package import parse_skill_import

    with pytest.raises(WorkflowError, match="缺少名称"):
        parse_skill_import(b"# Method", "method.md")
    absent = parse_skill_import(b"---\nname: method\ndescription: Method\n---\nBody", "method.md")
    explicit = parse_skill_import(
        b"---\nname: method\ndescription: Method\nmetadata:\n  short-drama-tags: []\n---\nBody",
        "method.md",
    )
    assert not absent.tags_provided and explicit.tags_provided


def test_skill_zip_import_validates_complete_package_and_rejects_traversal():
    from zipfile import ZIP_DEFLATED, ZipFile

    from short_drama.service.skills.packages.skill_package import parse_skill_import

    def archive(files):
        data = io.BytesIO()
        with ZipFile(data, "w", ZIP_DEFLATED) as stream:
            for path, content in files:
                stream.writestr(path, content)
        return data.getvalue()

    preview = parse_skill_import(
        archive(
            [
                (
                    "shots/SKILL.md",
                    "---\nname: shots\ndescription: Plan shots.\n---\n[模板](assets/shots.json)",
                ),
                ("shots/assets/shots.json", '{"shots":[]}'),
            ]
        ),
        "shots.zip",
    )
    assert preview.compatible and len(preview.draft.resources) == 1
    assert preview.draft.resources[0].path == "assets/shots.json"
    assert preview.draft.resources[0].kind == "template"
    with pytest.raises(WorkflowError) as caught:
        parse_skill_import(archive([("../SKILL.md", "bad")]), "bad.zip")
    assert caught.value.code == "invalid_agent_skill_package"
    with pytest.raises(WorkflowError, match="暂不支持脚本"):
        parse_skill_import(
            archive(
                [
                    ("SKILL.md", "---\nname: method\ndescription: Read rules.\n---\n# Method"),
                    ("scripts/run.py", "raise SystemExit()"),
                ]
            ),
            "scripts.zip",
        )


@pytest.mark.parametrize(
    "content",
    [
        "---\nname: first\nname: second\ndescription: test\n---\nMethod",
        "---\nname: &shared method\ndescription: *shared\n---\nMethod",
        "---\nname: method\nmetadata: []\n---\nMethod",
        "---\nname: method\ndescription: !!python/object:os.system {}\n---\nMethod",
    ],
    ids=["duplicate-key", "alias", "metadata-shape", "python-tag"],
)
def test_skill_import_rejects_ambiguous_or_executable_yaml(content):
    from short_drama.service.skills.packages.skill_package import parse_skill_import

    with pytest.raises(WorkflowError) as caught:
        parse_skill_import(content.encode(), "SKILL.md")
    assert caught.value.code == "invalid_agent_skill_package"


@pytest.mark.parametrize("case", ["duplicate", "symlink", "too-many", "large", "multiple-roots"])
def test_skill_zip_import_enforces_archive_limits(case):
    from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

    from short_drama.service.skills.packages.skill_package import parse_skill_import

    data = io.BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as stream:
        stream.writestr("SKILL.md", "# Method")
        if case == "duplicate":
            stream.writestr("references/one.md", "one")
            stream.writestr("references/ONE.md", "two")
        elif case == "symlink":
            entry = ZipInfo("references/link.md")
            entry.create_system = 3
            entry.external_attr = 0o120777 << 16
            stream.writestr(entry, "../../private")
        elif case == "too-many":
            for index in range(32):
                stream.writestr(f"references/{index}.md", "x")
        elif case == "large":
            stream.writestr("references/large.md", "x" * 131073)
        else:
            stream.writestr("another/SKILL.md", "# Other")
    with pytest.raises(WorkflowError):
        parse_skill_import(data.getvalue(), "package.zip")


def test_skill_exported_maximum_instructions_and_custom_resource_kind_round_trip():
    from short_drama.schemas.skills.catalog.skill import SkillCreate, SkillMetadata
    from short_drama.service.skills.packages.skill_package import (
        package_markdown,
        parse_skill_import,
    )

    content = SkillCreate(
        name="方法", slug="method", description="任务方法。", instructions="x" * 65536
    )
    metadata = {
        key: value
        for key, value in content.model_dump().items()
        if key in SkillMetadata.model_fields
    }
    exported = package_markdown(metadata, content.instructions, [])
    preview = parse_skill_import(exported.encode(), "SKILL.md")
    assert preview.compatible and preview.draft.instructions == content.instructions
    missing = parse_skill_import(
        b"---\nname: method\ndescription: Test\nallowed-tools: execute_python\n---\nMethod",
        "SKILL.md",
    )
    assert not missing.compatible and any("execute_python" in issue for issue in missing.issues)


@pytest.mark.parametrize(
    "instructions", [" ", "a\x00b", "中" * 22000], ids=["blank", "nul", "bytes"]
)
def test_skill_edits_have_the_same_content_boundaries_as_upload(instructions):
    with pytest.raises(ValidationError):
        SkillPatch(row_version="1", instructions=instructions)


def test_unknown_skill_identifier_is_not_an_internal_server_error():
    session = SimpleNamespace(
        info={"actor": ActorContext(1, "creator", "test@example.test", True, 1, "hash", "test")}
    )
    service = AgentSkillService(session, SimpleNamespace(agent_enabled=True))
    with pytest.raises(NotFound):
        service._skill("unknown.v1")


def test_selected_skill_and_attachment_ids_remain_decimal_strings_on_the_wire():
    request = MessageCreate(
        content="Review",
        attachment_ids=["9007199254740993"],
        skills=[SkillSelection(id="9007199254740995", content_version="2")],
    )
    assert request.model_dump(mode="json")["attachment_ids"] == ["9007199254740993"]
    assert request.model_dump(mode="json")["skills"][0]["content_version"] == "2"


def config(protocol="chat", model="gpt-4o-audio-preview"):
    route = "responses" if protocol == "responses" else "chat/completions"
    return {
        "service_type": "text",
        "model_key": model,
        "base_url": f"https://model.invalid/v1/{route}",
        "row_version": 1,
        "credential_identity": "identity",
    }


def test_input_declaration_is_version_bound_and_responses_cannot_claim_audio():
    snapshot = config("responses", "custom")
    snapshot["capability_cache"] = {
        "agent_inputs": {
            **{
                field: snapshot[field]
                for field in ("row_version", "model_key", "base_url", "credential_identity")
            },
            "image": True,
            "audio": True,
        }
    }
    assert input_capabilities(snapshot) == {
        "context_window_tokens": 65536,
        "context_window_evidence": "platform_default",
        "text": True,
        "image": True,
        "audio": False,
        "video": "sampled_frames",
        "evidence": "declared",
    }
    snapshot["row_version"] = 2
    assert input_capabilities(snapshot)["evidence"] == "runtime"


@pytest.mark.parametrize(
    ("kind", "video_audio"),
    [
        ("image", "include"),
        ("audio", "include"),
        ("video", "include"),
    ],
)
def test_freezing_preserves_media_without_guessing_model_capability(kind, video_audio):
    attachment = SimpleNamespace(
        id=1,
        kind=kind,
        name="input",
        mime_type="application/octet-stream",
        media_id=None,
        input_metadata={"has_audio": True},
        text_content=None,
        checksum_sha256="checksum",
    )
    session = SimpleNamespace(scalar=lambda *_: attachment)
    snapshot = config(model="gpt-4o" if kind == "video" else "text-only")
    references, frozen, rows = freeze_attachments(
        session, SimpleNamespace(id=1, owner_user_id=1), [1], snapshot, video_audio
    )
    assert references[0]["kind"] == frozen[0]["kind"] == kind
    assert rows == [attachment]


def test_inline_image_is_normalized_and_file_checksum_is_verified():
    source = io.BytesIO()
    Image.new("RGBA", (1500, 900), "red").save(source, "PNG")
    data = source.getvalue()
    storage = SimpleNamespace(open=lambda *_: io.BytesIO(data))
    prompt = {
        "codec": "agent.attachments",
        "content": "Review",
        "video_audio": "include",
        "attachments": [
            {
                "kind": "image",
                "name": "image.png",
                "storage_locator": "managed",
                "checksum_sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
    }
    parts = materialize_prompt(prompt, SimpleNamespace(), storage)
    assert parts[-1]["media_type"] == "image/jpeg"
    with Image.open(io.BytesIO(base64.urlsafe_b64decode(parts[-1]["data"]))) as normalized:
        assert normalized.width == 1280 and normalized.mode == "RGB"
    prompt["attachments"][0]["checksum_sha256"] = "wrong"
    with pytest.raises(WorkflowError) as caught:
        materialize_prompt(prompt, SimpleNamespace(), storage)
    assert caught.value.code == "agent_attachment_unavailable"


@pytest.mark.parametrize("state", ["durable", "unknown", "delete_failed", "uncommitted"])
def test_failed_upload_cleanup_never_deletes_a_durable_or_uncertain_object(state, caplog):
    deleted = []

    def lookup(*_):
        if state == "unknown":
            raise RuntimeError("Database receipt is unavailable")
        return 1 if state == "durable" else None

    def remove(*_, **__):
        if state == "delete_failed":
            raise RuntimeError("Storage deletion is unavailable")
        deleted.append(True)

    service = AgentAttachmentService(
        SimpleNamespace(scalar=lookup),
        SimpleNamespace(
            minio_image_bucket="images", minio_video_bucket="videos", minio_audio_bucket="audio"
        ),
        SimpleNamespace(remove=remove),
    )
    service._transaction = lambda **_: nullcontext()
    service._cleanup_uncommitted(
        SimpleNamespace(storage_locator="minio://images/file.png", version_id="uploaded-version")
    )
    assert bool(deleted) is (state == "uncommitted")
    assert bool(caplog.records) is (state in {"unknown", "delete_failed"})


def media_settings():
    try:
        return SimpleNamespace(
            render_ffmpeg_path=executable("ffmpeg"), render_ffprobe_path=executable("ffprobe")
        )
    except RuntimeError:
        pytest.skip("Local FFmpeg/ffprobe is unavailable; media processing is not verified")


@pytest.mark.parametrize("reported_duration_ms", [None, 1000])
def test_referenced_audio_actual_duration_is_checked_before_transcoding(reported_duration_ms):
    settings = media_settings()
    source = io.BytesIO()
    with wave.open(source, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000 * 121)
    data = source.getvalue()
    prompt = {
        "codec": "agent.attachments",
        "content": "Review the complete audio",
        "video_audio": "include",
        "attachments": [
            {
                "kind": "audio",
                "name": "referenced.wav",
                "storage_locator": "managed",
                "checksum_sha256": hashlib.sha256(data).hexdigest(),
                "metadata": {"duration_ms": reported_duration_ms},
            }
        ],
    }
    with pytest.raises(ValueError, match="120"):
        inspect_attachment(data, "referenced.wav", settings)
    with pytest.raises(WorkflowError) as caught:
        materialize_prompt(prompt, settings, SimpleNamespace(open=lambda *_: io.BytesIO(data)))
    assert caught.value.code == "agent_attachment_unavailable"
    assert isinstance(caught.value.__cause__, ValueError)
    assert "120" in str(caught.value.__cause__)


def test_local_audio_and_video_are_understood_as_inline_bytes(tmp_path):
    settings = media_settings()
    wav = tmp_path / "audio.wav"
    with wave.open(str(wav), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 16000)
    video = tmp_path / "video.mp4"
    subprocess.run(
        [
            settings.render_ffmpeg_path,
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:r=10:d=1",
            "-i",
            str(wav),
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    data = video.read_bytes()
    kind, mime, _, metadata = inspect_attachment(data, "video.mp4", settings)
    assert kind == "video" and mime == "video/mp4" and metadata["has_audio"]
    prompt = {
        "codec": "agent.attachments",
        "content": "Review",
        "video_audio": "include",
        "attachments": [
            {
                "kind": kind,
                "name": "video.mp4",
                "storage_locator": "managed",
                "checksum_sha256": hashlib.sha256(data).hexdigest(),
            }
        ],
    }
    storage = SimpleNamespace(open=lambda *_: io.BytesIO(data))
    included = materialize_prompt(prompt, settings, storage)
    assert {item["media_type"] for item in included if isinstance(item, dict)} == {
        "image/jpeg",
        "audio/mpeg",
    }
    prompt["video_audio"] = "visual_only"
    visual = materialize_prompt(prompt, settings, storage)
    assert all(item["media_type"] != "audio/mpeg" for item in visual if isinstance(item, dict))
    assert any("不理解视频声音" in item for item in visual if isinstance(item, str))
    assert inspect_attachment(wav.read_bytes(), "audio.wav", settings)[0] == "audio"
    prompt["attachments"][0].update(
        kind="audio", name="audio.wav", checksum_sha256=hashlib.sha256(wav.read_bytes()).hexdigest()
    )
    audio = materialize_prompt(
        prompt, settings, SimpleNamespace(open=lambda *_: io.BytesIO(wav.read_bytes()))
    )
    assert audio[-1]["media_type"] == "audio/mpeg"
