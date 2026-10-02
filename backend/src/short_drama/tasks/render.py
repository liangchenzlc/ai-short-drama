"""Durable, lease-fenced non-AI media jobs with a dedicated Celery queue."""

import json
import shutil
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import case, select

from short_drama.domain import EpisodeRenderJob, MediaFile
from short_drama.service.base import utcnow
from short_drama.service.storage_service import StorageService
from short_drama.service.video_render import RenderCancelled, VideoRenderer, checksum
from short_drama.tasks.celery_app import make_celery, render_queue
from short_drama.utils.snowflake import next_id


def restore_completed_render(renderer, root, directory, candidates, snapshot):
    """A partial or damaged recovery cache must not prevent a fresh render."""
    for candidate in candidates:
        for manifest in root.glob(f"{candidate}-*/completed.json"):
            try:
                saved = json.loads(manifest.read_text(encoding="utf-8"))
                cached = manifest.parent / "output.mp4"
                if (
                    not isinstance(saved, dict)
                    or saved.get("snapshot") != snapshot
                    or not cached.is_file()
                    or saved.get("checksum") != checksum(cached)
                ):
                    continue
                metadata = renderer.probe(cached)
            except (OSError, ValueError, RuntimeError):
                # Interrupted manifest writes and invalid cached media are replaceable.
                continue
            output = directory / "output.mp4"
            shutil.copyfile(cached, output)
            return output, metadata
    return None, None


class RenderPublisher:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings
        self.app = make_celery(settings)

    def tick(self):
        now = utcnow()
        with self.factory.begin() as session:
            expired = session.scalars(
                select(EpisodeRenderJob)
                .where(EpisodeRenderJob.status == "running", EpisodeRenderJob.locked_until < now)
                .with_for_update(skip_locked=True)
            )
            for job in expired:
                job.status = (
                    "cancelled"
                    if job.cancel_requested
                    else "failed"
                    if job.attempts >= 3
                    else "queued"
                )
                job.stage = job.status
                job.lease_token = None
                job.message_version += 1
                job.next_run_at = now
                if job.status in ("failed", "cancelled"):
                    job.finished_at = now
                    job.error = {"message": "合成服务中断，可重试此任务"}
            job = session.scalar(
                select(EpisodeRenderJob)
                .where(EpisodeRenderJob.status == "queued", EpisodeRenderJob.next_run_at <= now)
                .order_by(
                    case(
                        (EpisodeRenderJob.kind == "export", 0),
                        (EpisodeRenderJob.kind == "probe", 1),
                        else_=2,
                    ),
                    EpisodeRenderJob.next_run_at,
                    EpisodeRenderJob.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not job:
                return False
            job.next_run_at = now + timedelta(seconds=15)
            job_id, version = str(job.id), str(job.message_version)
        queue = render_queue(self.settings)
        self.app.send_task(
            "short_drama.execute_render",
            args=[job_id, version],
            queue=queue.name,
            routing_key="render",
            retry=False,
        )
        return True


class RenderExecutor:
    def __init__(self, factory, settings, storage):
        self.factory, self.settings = factory, settings
        self.storage = StorageService(storage, settings)

    def execute(self, job_id, version):
        token = uuid4().hex
        with self.factory.begin() as session:
            from types import SimpleNamespace

            from short_drama.db.access import scope_of
            from short_drama.domain import Project
            from short_drama.service.task_access import may_submit

            preview = session.get(EpisodeRenderJob, int(job_id))
            project_id = scope_of(session, preview)[1] if preview else None
            if project_id:
                session.scalar(select(Project).where(Project.id == project_id).with_for_update())
            job = session.scalar(
                select(EpisodeRenderJob)
                .where(EpisodeRenderJob.id == int(job_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if not job or job.status != "queued" or job.message_version != int(version):
                return
            if self.settings.auth_enabled and not may_submit(
                session,
                SimpleNamespace(
                    initiated_by=job.initiated_by,
                    project_id=project_id,
                    scope_user_id=None,
                ),
            ):
                job.status, job.stage, job.finished_at = "cancelled", "cancelled", utcnow()
                return
            job.status, job.stage, job.lease_token = "running", "preparing", token
            job.locked_until = utcnow() + timedelta(seconds=120)
            job.attempts += 1
            snapshot, kind, retry_of = job.snapshot, job.kind, job.retry_of_id
        # Each lease owns a separate directory. An expired process cannot corrupt its successor.
        root = Path(self.settings.render_scratch_root).resolve()
        directory = root / f"{int(job_id)}-{token}"
        directory.mkdir(parents=True, exist_ok=True)
        last_beat = 0

        def heartbeat(stage, progress, force=False):
            nonlocal last_beat
            if not force and time.monotonic() - last_beat < 1:
                return
            with self.factory.begin() as session:
                current = session.scalar(
                    select(EpisodeRenderJob)
                    .where(EpisodeRenderJob.id == int(job_id))
                    .with_for_update()
                )
                if (
                    current.lease_token != token
                    or current.status != "running"
                    or current.cancel_requested
                ):
                    raise RenderCancelled()
                current.stage, current.progress = stage, progress
                current.locked_until = utcnow() + timedelta(seconds=120)
                current.updated_at = utcnow()
            last_beat = time.monotonic()

        renderer = VideoRenderer(self.settings, heartbeat)
        stored = None
        pending_derivatives = []
        committed = False
        try:
            # Recover a verified completed file after upload failure, without encoding again.
            output, metadata = None, None
            candidates = [int(job_id)] + ([retry_of] if retry_of else [])
            if kind in ("export", "preview"):
                output, metadata = restore_completed_render(
                    renderer, root, directory, candidates, snapshot
                )
            if output is None:
                entries = snapshot["media"] if kind == "probe" else snapshot["clips"]
                if kind != "probe":
                    entries = entries + snapshot.get("sound", {}).get("media", [])
                sources = {}
                for index, entry in enumerate(entries):
                    heartbeat("preparing", int(index / max(1, len(entries)) * 10), True)
                    mid = entry.get("id") or entry["media_id"]
                    if mid in sources:
                        continue
                    path = directory / f"source-{int(mid)}.media"
                    size = 0
                    with self.storage.open(entry["locator"]) as response, path.open("wb") as file:
                        for chunk in response.stream(1024 * 1024):
                            size += len(chunk)
                            if (
                                size > self.settings.render_max_source_bytes
                                or sum(p.stat().st_size for p in directory.iterdir() if p.is_file())
                                > self.settings.render_max_scratch_bytes
                            ):
                                raise RuntimeError("视频素材超过合成空间上限")
                            heartbeat("preparing", 5)
                            file.write(chunk)
                    if entry.get("checksum") and checksum(path) != entry["checksum"]:
                        raise RuntimeError("来源视频校验不一致，请同步视频后重试")
                    sources[mid] = path
                    if kind == "probe":
                        try:
                            info = renderer.probe(path)
                        except (RuntimeError, ValueError):
                            info = {"probe_version": 1, "error": "无法读取实际视频，请替换来源"}
                        if not info.get("error"):
                            proxy, thumbnail, filmstrip = renderer.preview_assets(
                                path, info, directory
                            )
                            for asset, mime, key in (
                                (proxy, "video/mp4", "preview_locator"),
                                (thumbnail, "image/jpeg", "thumbnail_locator"),
                                (filmstrip, "image/jpeg", "filmstrip_locator"),
                            ):
                                with asset.open("rb") as stream:
                                    derivative = self.storage.upload(
                                        stream, length=asset.stat().st_size, content_type=mime
                                    )
                                pending_derivatives.append(derivative.storage_locator)
                                info[key] = derivative.storage_locator
                            info["preview_version"] = 2
                        heartbeat("preparing", int((index + 1) / len(entries) * 90), True)
                        with self.factory.begin() as session:
                            current = session.scalar(
                                select(EpisodeRenderJob)
                                .where(EpisodeRenderJob.id == int(job_id))
                                .with_for_update()
                            )
                            if current.lease_token != token or current.cancel_requested:
                                raise RenderCancelled()
                            media = session.get(MediaFile, int(mid))
                            quality = (media.video_metadata or {}).get("native_quality")
                            media.video_metadata = {
                                **info,
                                **({"native_quality": quality} if quality is not None else {}),
                            }
                        pending_derivatives.clear()
                        path.unlink()
                if kind in ("export", "preview"):
                    render_snapshot = (
                        {**snapshot, "resolution": "preview"} if kind == "preview" else snapshot
                    )
                    output, metadata = renderer.render(render_snapshot, sources, directory)
            if output:
                sha = checksum(output)
                (directory / "completed.json").write_text(
                    json.dumps({"snapshot": snapshot, "checksum": sha})
                )
                heartbeat("uploading", 95, True)
                with output.open("rb") as file:
                    stored = self.storage.upload(
                        file, length=output.stat().st_size, content_type="video/mp4"
                    )
                heartbeat("uploading", 98, True)
            with self.factory.begin() as session:
                job = session.scalar(
                    select(EpisodeRenderJob)
                    .where(EpisodeRenderJob.id == int(job_id))
                    .with_for_update()
                )
                if job.lease_token != token or job.cancel_requested:
                    raise RenderCancelled()
                if stored:
                    from short_drama.db.access import scope_of

                    scope = scope_of(session, job)
                    media = MediaFile(
                        scope_user_id=scope[0],
                        project_id=scope[1],
                        created_by=job.initiated_by,
                        updated_by=job.initiated_by,
                        id=next_id(),
                        format_code="video/mp4",
                        storage_locator=stored.storage_locator,
                        original_name=f"episode-{job.assembly_id}.mp4",
                        byte_size=stored.size,
                        width=metadata["width"],
                        height=metadata["height"],
                        duration_ms=metadata["duration_ms"],
                        checksum_sha256=sha,
                        video_metadata=metadata,
                        created_at=utcnow(),
                        updated_at=utcnow(),
                    )
                    session.add(media)
                    session.flush()
                    job.output_media_id = media.id
                    job.manifest = {"checksum": sha, "metadata": metadata}
                job.status, job.stage, job.progress, job.finished_at = (
                    "succeeded",
                    "complete",
                    100,
                    utcnow(),
                )
                job.lease_token, job.locked_until = None, None
            committed = True
            shutil.rmtree(directory)
        except Exception as error:
            for locator in pending_derivatives:
                try:
                    self.storage.delete(locator)
                except Exception:
                    pass
            if stored and not committed:
                try:
                    self.storage.delete(stored.storage_locator)
                except Exception:
                    pass
            with self.factory.begin() as session:
                job = session.scalar(
                    select(EpisodeRenderJob)
                    .where(EpisodeRenderJob.id == int(job_id))
                    .with_for_update()
                )
                if job.lease_token == token:
                    job.status = "cancelled" if isinstance(error, RenderCancelled) else "failed"
                    job.stage, job.finished_at = job.status, utcnow()
                    job.error = {
                        "message": str(error)
                        if isinstance(error, RuntimeError)
                        else "合成未完成，请检查存储和合成服务后重试"
                    }
                    job.lease_token, job.locked_until = None, None
            # Keep only a verified output for upload retry. Source media is inexpensive to fetch
            # again.
            for path in directory.iterdir():
                if path.is_file() and path.name not in ("output.mp4", "completed.json"):
                    path.unlink()


def cleanup_render_scratch(settings):
    """Only our terminal attempt directories older than seven days are eligible."""
    import re

    root = Path(settings.render_scratch_root).resolve()
    if not root.exists():
        return
    for path in root.iterdir():
        if (
            re.fullmatch(r"[0-9]+-[a-f0-9]{32}", path.name)
            and path.is_dir()
            and not path.is_symlink()
            and path.resolve().parent == root
            and time.time() - path.stat().st_mtime > 7 * 86400
        ):
            shutil.rmtree(path)
