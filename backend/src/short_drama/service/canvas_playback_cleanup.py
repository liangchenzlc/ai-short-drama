"""兼容预览写入前记录对象；取消、租约中断或进程重启后继续清理未登记对象。"""

import logging
import re

from sqlalchemy import or_, select

from short_drama.domain import CanvasLocalJob, MediaFile
from short_drama.storage.models import ObjectLocation

from .base import utcnow

logger = logging.getLogger(__name__)


def cleanup_canvas_playback(factory, settings, storage, *, job_id=None, token=None) -> int:
    cleaned = 0
    with factory.begin() as session:
        statement = select(CanvasLocalJob).where(
            CanvasLocalJob.task_type == "resource_playback",
            CanvasLocalJob.result_json["playback_staging"].as_string().is_not(None),
        )
        if job_id is not None:
            statement = statement.where(CanvasLocalJob.id == job_id)
        if token is None:
            statement = statement.where(
                or_(CanvasLocalJob.status != "running", CanvasLocalJob.locked_until <= utcnow())
            )
        jobs = session.scalars(statement.limit(50).with_for_update(skip_locked=True))
        for job in jobs:
            if token is not None and job.lease_token not in {None, token}:
                continue
            remaining = []
            for locator in (job.result_json or {}).get("playback_staging", []):
                try:
                    location = ObjectLocation.parse(locator, {settings.minio_video_bucket})
                    if not re.fullmatch(
                        rf"canvas-playback/{job.id}/[a-f0-9]{{32}}\.mp4", location.object_name
                    ):
                        raise ValueError("Invalid playback staging identity")
                    registered = session.scalar(
                        select(MediaFile.id)
                        .where(MediaFile.storage_locator == locator)
                        .with_for_update()
                    )
                    if registered is None:
                        storage.remove(location.bucket, location.object_name)
                    cleaned += 1
                except Exception as error:
                    remaining.append(locator)
                    logger.warning(
                        "Canvas playback cleanup deferred: job=%s error=%s",
                        job.id,
                        type(error).__name__,
                    )
            result = {**job.result_json}
            if remaining:
                result["playback_staging"] = remaining
            else:
                result.pop("playback_staging", None)
            job.result_json = result
    return cleaned
