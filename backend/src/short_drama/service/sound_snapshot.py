"""Read immutable sound inputs and validate them without edit-service dependencies."""

from sqlalchemy import select

from short_drama.core.exceptions import BusinessError, NotFound, WorkflowError
from short_drama.domain import MediaFile
from short_drama.domain.project_sound import ProjectSound
from short_drama.schemas.project_sound import SoundDocument
from short_drama.utils.json_digest import digest


def line_hash(line):
    return digest({k: line.get(k) for k in ("text", "character", "voice", "config_id")})


def sound_duration(video):
    return sum(
        ((c["trim_out_ms"] * 30 + 500) // 1000 - (c["trim_in_ms"] * 30 + 500) // 1000) * 1000 / 30
        for c in video["clips"]
        if c["trim_out_ms"] is not None
    )


def sound_media_rows(session, identifiers, *, for_update=True, audio_only=False):
    identifiers = {int(mid) for mid in identifiers}
    if not identifiers:
        return {}
    query = select(MediaFile).where(MediaFile.id.in_(identifiers)).order_by(MediaFile.id)
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    rows = {media.id: media for media in session.scalars(query)}
    if identifiers != rows.keys():
        raise NotFound("Referenced record does not exist")
    for media in rows.values():
        if audio_only:
            if not media.format_code.startswith("audio/"):
                raise BusinessError("Media type does not match this operation")
            if not media.duration_ms or not media.storage_locator.startswith("minio://"):
                raise WorkflowError("audio_unavailable", "音频尚未验证或无法读取", 422)
        elif not media.format_code.startswith(("audio/", "video/")) or not (
            media.storage_locator.startswith("minio://")
        ):
            raise WorkflowError("audio_unavailable", "声音来源不是可读取的音视频", 422)
    return rows


def sound_snapshot(session, settings, assembly, video, *, for_update=True):
    sound = session.get(ProjectSound, assembly.id)
    if sound is None:
        return None
    document = SoundDocument.model_validate(sound.document).model_dump(mode="json")
    from short_drama.domain import Project

    from .native_voice_context import project_mode

    creation = session.get(Project, assembly.project_id)
    native = project_mode(session, creation.id, settings) == "native"
    if native:
        document["dialogue"] = []
    else:
        document["native_ducking"] = []
    ids = {line["media_id"] for line in document["dialogue"] if line.get("media_id")}
    if document["music"]:
        ids.add(document["music"]["media_id"])
    ids.update(clip["media_id"] for clip in document["audio_clips"])
    media_rows = sound_media_rows(session, ids, for_update=for_update)
    entries = []
    for mid in sorted(ids):
        media = media_rows[int(mid)]
        entries.append(
            {
                "media_id": str(mid),
                "locator": media.storage_locator,
                "checksum": media.checksum_sha256,
                "duration_ms": media.duration_ms or (media.video_metadata or {}).get("duration_ms"),
            }
        )
    from .sound_render import font_metadata

    return {
        "version": document["schema_version"],
        "mode": "native" if native else "legacy",
        "mix_version": "48k-stereo-v1",
        **(font_metadata(settings) if document["burn_subtitles"] else {}),
        "needs_review": sound.reviewed_timeline_hash != digest(video),
        "document": document,
        "media": entries,
    }


def validate_sound_snapshot(snapshot):
    sound = snapshot.get("sound")
    if not sound:
        return
    if sound["needs_review"] and sound["document"].get("schema_version", 1) < 2:
        raise WorkflowError(
            "sound_review_required", "声音或视频剪辑已变化，请核对声音与字幕时间并确认", 409
        )
    doc = sound["document"]
    if doc["burn_subtitles"] and doc["subtitles"] and not sound.get("font_checksum"):
        raise WorkflowError("subtitle_font_missing", "请先配置可用的中文字幕字体文件", 422)
    duration = sound_duration(snapshot)
    media = {m["media_id"]: m for m in sound["media"]}
    if doc.get("schema_version", 1) >= 2:
        for clip in doc["audio_clips"]:
            source = media.get(clip["media_id"])
            if not source or clip["trim_out_ms"] > source["duration_ms"]:
                raise WorkflowError("audio_timing", "声音来源或裁剪范围已失效", 422)
        return
    for line in doc["dialogue"]:
        if not line["media_id"] or line["adopted_hash"] != line_hash(line):
            raise WorkflowError("audio_dialogue_stale", "存在未采用或已过期的配音，请先处理", 422)
        if line["start_ms"] + media[line["media_id"]]["duration_ms"] > duration + 1:
            raise WorkflowError("audio_timing", "配音超出成片时长，请调整开始时间或台词", 422)
    if any(s["end_ms"] > duration + 1 for s in doc["subtitles"]):
        raise WorkflowError("subtitle_timing", "字幕超出成片时长", 422)
    if any(s["end_ms"] > duration + 1 for s in doc.get("native_ducking", [])):
        raise WorkflowError("audio_timing", "对白压低配乐的区间超出成片时长", 422)
    music = doc["music"]
    if music and (
        music["start_ms"] >= duration
        or music["trim_out_ms"] > media[music["media_id"]]["duration_ms"]
    ):
        raise WorkflowError("audio_timing", "配乐时间范围无效", 422)
