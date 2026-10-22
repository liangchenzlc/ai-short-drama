"""Shared native speech context queries and prompt construction."""

from copy import deepcopy

from sqlalchemy import select

from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.domain import Asset, MediaFile, Project, ShotAsset
from short_drama.domain.native_voice import CharacterVoice, ProjectSoundMode, ShotDialogue


def project_mode(session, project_id, settings=None):
    if not getattr(settings or Settings(), "native_video_enabled", False):
        return "legacy"
    row = session.get(ProjectSoundMode, int(project_id))
    return row.mode if row else "legacy"


def native_context(session, shot, *, strict=False, settings=None, _lookups=None):
    if not getattr(settings or Settings(), "native_video_enabled", False):
        return None
    creation = (
        _lookups["creation"] if _lookups is not None else session.get(Project, shot.project_id)
    )
    mode = (
        _lookups["mode"] if _lookups is not None else project_mode(session, creation.id, settings)
    )
    if mode != "native":
        return None
    row = (
        _lookups["dialogues"].get(shot.id)
        if _lookups is not None
        else session.get(ShotDialogue, shot.id)
    )
    doc = row.document if row else {"reviewed": False, "lines": []}
    result = {
        "mode": "native",
        "dialogue_version": row.row_version if row else 0,
        "reviewed": doc["reviewed"],
        "lines": deepcopy(doc["lines"]),
        "voices": [],
    }
    if strict and not doc["reviewed"]:
        raise WorkflowError(
            "native_dialogue_review", "请先保存并确认分镜对白；无对白镜头也需确认", 422
        )
    for cid in dict.fromkeys(line["character_id"] for line in doc["lines"]):
        asset = (
            _lookups["assets"].get(int(cid))
            if _lookups is not None
            else session.get(Asset, int(cid))
        )
        binding = (
            _lookups["bindings"].get(int(cid))
            if _lookups is not None
            else session.get(CharacterVoice, (creation.id, int(cid)))
        )
        linked = (
            (shot.id, int(cid)) in _lookups["linked"]
            if _lookups is not None
            else session.scalar(
                select(ShotAsset.id).where(
                    ShotAsset.shot_id == shot.id, ShotAsset.asset_id == int(cid)
                )
            )
        )
        if strict and (
            asset is None
            or asset.deleted_at is not None
            or asset.project_id != creation.id
            or asset.kind != "character"
            or not linked
        ):
            raise WorkflowError("native_character_scope", "说话角色必须关联到当前分镜", 422)
        media = (
            _lookups["media"].get(binding.media_id)
            if binding and binding.media_id is not None and _lookups is not None
            else session.get(MediaFile, binding.media_id)
            if binding and binding.media_id is not None
            else None
        )
        if strict and (
            media is None
            or not media.duration_ms
            or not media.checksum_sha256
            or not 3000 <= media.duration_ms <= 7500
            or not media.byte_size
            or media.byte_size > 10 * 1024**2
        ):
            raise WorkflowError(
                "native_voice_required", "角色需采用已归档的 3～7.5 秒样音（最多 10 MiB）", 422
            )
        result["voices"].append(
            {
                "character_id": cid,
                "name": asset.name if asset else "",
                "version": binding.row_version if binding else 0,
                "record_id": str(binding.record_id) if binding and binding.record_id else None,
                "media_id": str(media.id) if media else None,
                "checksum": media.checksum_sha256 if media else None,
                "duration_ms": media.duration_ms if media else None,
            }
        )
    return result


def native_contexts(session, creation, shots, *, settings=None):
    """Prefetch this page's native metadata; single-shot validation/serialization is shared."""
    shots = list(shots)
    contexts = {shot.id: None for shot in shots}
    if not shots:
        return contexts
    settings = settings or Settings()
    if not getattr(settings, "native_video_enabled", False):
        return contexts
    mode = project_mode(session, creation.id, settings)
    if mode != "native":
        return contexts
    shot_ids = [shot.id for shot in shots]
    dialogues = {
        row.shot_id: row
        for row in session.scalars(select(ShotDialogue).where(ShotDialogue.shot_id.in_(shot_ids)))
    }
    character_ids = {
        int(line["character_id"]) for row in dialogues.values() for line in row.document["lines"]
    }
    assets, bindings, linked, media = {}, {}, set(), {}
    if character_ids:
        assets = {
            row.id: row
            for row in session.scalars(
                select(Asset).where(
                    Asset.id.in_(character_ids),
                    Asset.project_id == creation.id,
                    Asset.deleted_at.is_(None),
                )
            )
        }
        bindings = {
            row.asset_id: row
            for row in session.scalars(
                select(CharacterVoice).where(
                    CharacterVoice.project_id == creation.id,
                    CharacterVoice.asset_id.in_(character_ids),
                )
            )
        }
        linked = set(
            self_row
            for self_row in session.execute(
                select(ShotAsset.shot_id, ShotAsset.asset_id).where(
                    ShotAsset.shot_id.in_(shot_ids), ShotAsset.asset_id.in_(character_ids)
                )
            ).all()
        )
        media_ids = {row.media_id for row in bindings.values() if row.media_id is not None}
        if media_ids:
            media = {
                row.id: row
                for row in session.scalars(select(MediaFile).where(MediaFile.id.in_(media_ids)))
            }
    lookups = {
        "creation": creation,
        "mode": mode,
        "dialogues": dialogues,
        "assets": assets,
        "bindings": bindings,
        "linked": linked,
        "media": media,
    }
    return {
        shot.id: native_context(session, shot, settings=settings, _lookups=lookups)
        for shot in shots
    }


def native_prompt(context):
    if not context["lines"]:
        return "\n声音要求：本镜头没有对白，不添加人声或背景音乐，可生成必要环境音。"
    parts = [
        "\n声音与对白要求：使用参考音频的固定音色；不要朗读参考样音内容。",
        "只说下列台词，严格按顺序轮流说话，不抢话，不添加台词或背景音乐。",
    ]
    for index, voice in enumerate(context["voices"], 1):
        parts.append(f"角色 {voice['name']}（ID {voice['character_id']}）使用音频{index}的音色。")
    for index, line in enumerate(context["lines"], 1):
        action = (
            "画内说话，口型与对白同步" if line["speech"] == "onscreen" else "画外音，画内人物不张嘴"
        )
        parts.append(
            f"{index}. 角色ID {line['character_id']}，{action}，"
            f"语气：{line['delivery'] or '自然'}；台词：{line['text']}"
        )
    return "\n".join(parts)
