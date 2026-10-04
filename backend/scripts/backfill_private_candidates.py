"""按可靠关联回填作者与已采用作品；默认 dry-run，不调用供应商。"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from short_drama.core.config import Settings
from short_drama.db.session import build_engine
from short_drama.domain import (
    AgentArtifact,
    AIGenerationRecord,
    Asset,
    AssetImageCandidate,
    AsyncTask,
    CharacterVoice,
    Episode,
    EpisodeAssembly,
    EpisodeAssemblyClip,
    EpisodeNovel,
    EpisodeRenderJob,
    EpisodeScript,
    EpisodeSound,
    GenerationBatchJob,
    MediaAsset,
    MediaFile,
    MediaRecycleBin,
    NovelScriptRecord,
    ScriptShotRecord,
    ShotImage,
    ShotScript,
    ShotVideo,
    SoundMediaReference,
)
from short_drama.domain.collaboration import ResourceImport
from short_drama.service.base import utcnow
from short_drama.service.publication import public_voice_context


def identifier(value: object) -> int | None:
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        try:
            result = int(value)
            return result if result > 0 else None
        except ValueError:
            return None
    return None


def backfill(session: Session, *, now: datetime | None = None) -> dict[str, int]:
    """调用者负责系统事务和 commit/rollback；未知作者始终保持隔离。"""
    if session.info.get("actor") or session.info.get("legacy_user_id"):
        raise ValueError("回填必须使用独立系统 Session")
    for model, statuses in (
        (AsyncTask, {"queued", "running"}),
        (EpisodeRenderJob, {"queued", "running"}),
        (GenerationBatchJob, {"running"}),
        (AIGenerationRecord, {"sent", "unknown"}),
    ):
        if session.scalar(select(model.id).where(model.status.in_(statuses)).limit(1)):
            raise ValueError("仍有活动或受理情况不明的任务；停止进程并处理后再回填")
    timestamp, counts = now or utcnow(), Counter()
    tasks = {row.id: row for row in session.scalars(select(AsyncTask))}
    episodes = {row.id: row for row in session.scalars(select(Episode))}
    novels = {row.id: row for row in session.scalars(select(EpisodeNovel))}
    scripts = {row.id: row for row in session.scalars(select(EpisodeScript))}
    shots = {row.id: row for row in session.scalars(select(ShotScript))}
    assets = {row.id: row for row in session.scalars(select(Asset))}
    media = {row.id: row for row in session.scalars(select(MediaFile))}
    records = {row.id: row for row in session.scalars(select(AIGenerationRecord))}
    by_task: dict[int, list[AIGenerationRecord]] = defaultdict(list)
    for record in records.values():
        by_task[record.task_id].append(record)

    def assign(row: object, field: str, evidence: set[int], label: str) -> None:
        current = getattr(row, field)
        if current is not None:
            return
        if len(evidence) == 1:
            setattr(row, field, next(iter(evidence)))
            counts[label] += 1
        else:
            counts[f"quarantined_{label}"] += 1

    def publish_media(mid: object, project_id: int | None) -> None:
        row = media.get(identifier(mid))
        if row and project_id and row.project_id == project_id and row.scope_user_id is None:
            if row.published_at is None:
                row.published_at = timestamp
                counts["published_media"] += 1

    def task_owner(task_id: int, project_id: int | None) -> int | None:
        task = tasks.get(task_id)
        if task and task.project_id == project_id:
            return task.initiated_by
        return None

    # Personal ownership is explicit evidence; project ownership is never guessed.
    for row in [*tasks.values(), *session.scalars(select(GenerationBatchJob))]:
        if row.initiated_by is None and row.scope_user_id:
            row.initiated_by = row.scope_user_id
            counts["personal_initiators"] += 1

    script_owners: dict[int, set[int]] = defaultdict(set)
    for provenance in session.scalars(select(NovelScriptRecord)):
        script, novel = scripts.get(provenance.script_id), novels.get(provenance.novel_id)
        episode = episodes.get(script.episode_id) if script else None
        evidence = set()
        if episode and novel and novel.episode_id == episode.id:
            for record in by_task[provenance.batch_id]:
                source = record.request_data.get("source") or {}
                if (
                    source.get("scene") == "novel_script"
                    and identifier(source.get("episode_id")) == episode.id
                    and identifier(source.get("novel_id")) == novel.id
                ):
                    owner = task_owner(record.task_id, episode.project_id)
                    if owner:
                        evidence.add(owner)
            if provenance.created_by:
                evidence.add(provenance.created_by)
            script_owners[script.id].update(evidence)
        assign(provenance, "created_by", evidence, "novel_record_authors")

    for artifact in session.scalars(select(AgentArtifact)):
        script = scripts.get(artifact.script_id)
        if script and (script.episode_id, artifact.project_id) == (
            artifact.episode_id,
            episodes[script.episode_id].project_id,
        ):
            if artifact.created_by:
                script_owners[script.id].add(artifact.created_by)
            if (
                artifact.status == "applied"
                and artifact.apply_receipt
                and script.published_at is None
            ):
                script.published_at = timestamp
                counts["published_scripts"] += 1
    for script in scripts.values():
        assign(script, "created_by", script_owners[script.id], "script_authors")
        episode = episodes.get(script.episode_id)
        if episode and (episode.editing_script_id == script.id or script.state == "confirmed"):
            if script.published_at is None:
                script.published_at = timestamp
                counts["published_scripts"] += 1
    for provenance in session.scalars(select(ScriptShotRecord)):
        script, shot = scripts.get(provenance.script_id), shots.get(provenance.shot_id)
        episode = episodes.get(script.episode_id) if script else None
        evidence = set()
        if episode and shot and shot.episode_id == episode.id:
            for record in by_task[provenance.batch_id]:
                source = record.request_data.get("source") or {}
                if (
                    source.get("scene") == "script_shots"
                    and identifier(source.get("episode_id")) == episode.id
                    and identifier(source.get("script_id")) == script.id
                ):
                    owner = task_owner(record.task_id, episode.project_id)
                    if owner:
                        evidence.add(owner)
        assign(provenance, "created_by", evidence, "shot_record_authors")

    generated = {row.media_id: row for row in session.scalars(select(MediaAsset))}
    for row in media.values():
        evidence = {row.scope_user_id} if row.scope_user_id else set()
        output = generated.get(row.id)
        record = records.get(output.record_id) if output else None
        if record:
            task = tasks.get(record.task_id)
            if task and (task.project_id, task.scope_user_id) == (
                row.project_id,
                row.scope_user_id,
            ):
                if task.initiated_by:
                    evidence.add(task.initiated_by)
        for job in session.scalars(
            select(EpisodeRenderJob).where(EpisodeRenderJob.output_media_id == row.id)
        ):
            assembly = session.get(EpisodeAssembly, job.assembly_id)
            episode = episodes.get(assembly.episode_id) if assembly else None
            if episode and row.project_id == episode.project_id and job.initiated_by:
                evidence.add(job.initiated_by)
        assign(row, "created_by", evidence, "media_authors")
    for candidate in session.scalars(select(AssetImageCandidate)):
        asset, output = assets.get(candidate.asset_id), generated.get(candidate.media_id)
        record = records.get(output.record_id) if output else None
        evidence = set()
        if asset and record:
            source = record.request_data.get("source") or {}
            manifest = (record.response_data or {}).get("media_manifest") or []
            archived = any(
                identifier(item.get("asset_id")) == output.id
                and item.get("candidate_status") == "linked"
                and item.get("saved") is True
                for item in manifest
                if isinstance(item, dict)
            )
            if (
                source.get("scene") == "asset_image"
                and identifier(source.get("asset_id")) == asset.id
                and archived
            ):
                owner = task_owner(record.task_id, asset.project_id)
                if owner:
                    evidence.add(owner)
        assign(candidate, "created_by", evidence, "image_candidate_authors")

    # Only explicit, persisted business pointers publish work, never a candidate list.
    for asset in assets.values():
        for mid in [asset.media_id, *(asset.reference_media_ids or [])]:
            publish_media(mid, asset.project_id)
    for shot in shots.values():
        episode = episodes.get(shot.episode_id)
        for mid in shot.reference_media_ids or []:
            publish_media(mid, episode.project_id if episode else None)
    for model in (ShotImage, ShotVideo, MediaRecycleBin):
        for slot in session.scalars(select(model)):
            shot = shots.get(slot.shot_id)
            episode = episodes.get(shot.episode_id) if shot else None
            publish_media(slot.media_id, episode.project_id if episode else None)
    assemblies = {row.id: row for row in session.scalars(select(EpisodeAssembly))}
    for assembly in assemblies.values():
        episode = episodes.get(assembly.episode_id)
        publish_media(assembly.current_media_id, episode.project_id if episode else None)
        jobs = (
            list(
                session.scalars(
                    select(EpisodeRenderJob).where(
                        EpisodeRenderJob.assembly_id == assembly.id,
                        EpisodeRenderJob.output_media_id == assembly.current_media_id,
                        EpisodeRenderJob.status == "succeeded",
                    )
                )
            )
            if assembly.current_media_id
            else []
        )
        hashes = {job.context_hash for job in jobs}
        current = media.get(assembly.current_media_id)
        if (
            current
            and len(hashes) == 1
            and not (current.video_metadata or {}).get("publication_context_hash")
        ):
            current.video_metadata = {
                **(current.video_metadata or {}),
                "publication_context_hash": next(iter(hashes)),
            }
            counts["published_render_contexts"] += 1
    for clip in session.scalars(select(EpisodeAssemblyClip)):
        assembly = assemblies.get(clip.assembly_id)
        episode = episodes.get(assembly.episode_id) if assembly else None
        publish_media(clip.media_id, episode.project_id if episode else None)
    for voice in session.scalars(select(CharacterVoice)):
        publish_media(voice.media_id, voice.project_id)
    for sound in session.scalars(select(EpisodeSound)):
        assembly = assemblies.get(sound.assembly_id)
        episode = episodes.get(assembly.episode_id) if assembly else None
        mids = {line.get("media_id") for line in sound.document.get("dialogue", [])}
        if sound.document.get("music"):
            mids.add(sound.document["music"].get("media_id"))
        for mid in mids:
            publish_media(mid, episode.project_id if episode else None)
            reference = (
                session.get(SoundMediaReference, (sound.assembly_id, identifier(mid)))
                if identifier(mid)
                else None
            )
            if reference:
                publish_media(reference.proxy_media_id, episode.project_id if episode else None)
    for job in session.scalars(select(ResourceImport).where(ResourceImport.status == "succeeded")):
        for copied in job.snapshot.get("media", []):
            publish_media(copied.get("copy_id"), job.project_id)
    for row in media.values():
        output = generated.get(row.id)
        record = records.get(output.record_id) if output else None
        native = (
            (record.request_data.get("source_snapshot") or {}).get("native_speech")
            if record
            else None
        )
        if (
            row.published_at
            and native
            and not (row.video_metadata or {}).get("adopted_native_speech")
        ):
            row.video_metadata = {
                **(row.video_metadata or {}),
                "adopted_native_speech": public_voice_context(native),
            }
            counts["published_native_contexts"] += 1
    session.flush()
    return dict(sorted(counts.items()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="提交回填；默认仅预览并回滚")
    args = parser.parse_args()
    engine = build_engine(Settings())
    try:
        with Session(engine, autoflush=False) as session:
            result = backfill(session)
            if args.apply:
                session.commit()
            else:
                session.rollback()
        sys.stdout.write(
            json.dumps({"applied": args.apply, "counts": result}, ensure_ascii=False) + "\n"
        )
    except ValueError as error:
        sys.stderr.write(str(error) + "\n")
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
