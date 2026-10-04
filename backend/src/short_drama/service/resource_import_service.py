"""Durable cross-scope copies; never create writable cross-project references."""

import copy
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import inspect, or_, select

from short_drama.core.exceptions import Conflict, NotFound
from short_drama.db.access import require_project, scope_of, scoped_key
from short_drama.domain import Asset, MediaFile, ProjectAsset
from short_drama.domain.collaboration import AuditEvent, ResourceImport
from short_drama.storage.models import ObjectLocation
from short_drama.utils.snowflake import next_id

from .base import BaseService, utcnow
from .storage_service import StorageService


def dto(job):
    return {
        "id": str(job.id),
        "status": job.status,
        "source_type": job.source_type,
        "result_id": str(job.result_id) if job.result_id else None,
    }


class ResourceImportService(BaseService):
    model = ResourceImport

    def create(self, project_id, source_type, source_id, key):
        with self._transaction():
            require_project(self.session, project_id)
            actor = self.session.info["actor"]
            key = scoped_key(self.session, key)
            old = self.session.scalar(
                select(ResourceImport).where(ResourceImport.idempotency_key == key)
            )
            if old:
                if (old.project_id, old.source_type, old.source_id) != (
                    int(project_id),
                    source_type,
                    int(source_id),
                ):
                    raise Conflict("Idempotency-Key belongs to another import")
                return dto(old)
            model = Asset if source_type == "asset" else MediaFile
            source = self._require(model, source_id, for_update=False)
            source_scope = scope_of(self.session, source)
            if source_scope == (None, int(project_id)):
                raise Conflict("Resource already belongs to this project")
            # Snapshot every owned media file; even reference images are copied.
            fields = {
                c.key: copy.deepcopy(getattr(source, c.key))
                for c in inspect(model).columns
                if c.key
                not in {
                    "created_at",
                    "updated_at",
                    "created_by",
                    "updated_by",
                    "scope_user_id",
                    "project_id",
                    "model_id",
                    "creation_key",
                    "creation_hash",
                }
            }
            media_ids = (
                {source.id}
                if source_type == "media"
                else set(map(int, source.reference_media_ids or []))
                | ({source.media_id} if source.media_id else set())
            )
            media = []
            for identifier in sorted(media_ids):
                row = self._require(MediaFile, identifier, for_update=False)
                if scope_of(self.session, row) != source_scope:
                    raise NotFound("Reference media is outside the source scope")
                values = {
                    c.key: copy.deepcopy(getattr(row, c.key))
                    for c in inspect(MediaFile).columns
                    if c.key
                    not in {
                        "created_at",
                        "updated_at",
                        "created_by",
                        "updated_by",
                        "scope_user_id",
                        "project_id",
                    }
                }
                # Stable copy destination survives retries and uncertain responses.
                values["copy_id"] = next_id()
                media.append(values)
            job = ResourceImport(
                id=next_id(),
                project_id=int(project_id),
                initiated_by=actor.user_id,
                source_type=source_type,
                source_id=int(source_id),
                idempotency_key=key,
                snapshot={"resource": fields, "media": media, "asset_id": next_id()},
                status="pending",
                attempts=0,
                created_at=utcnow(),
            )
            self.session.add(job)
            self.session.flush()
            return dto(job)

    def detail(self, project_id, identifier):
        with self._transaction():
            job = self._require(ResourceImport, identifier, for_update=False)
            if job.project_id != int(project_id):
                raise NotFound("Import does not exist")
            return dto(job)


def process_import(factory, storage, settings):
    from .task_access import lock_resource_project, may_submit

    now = utcnow()
    with factory.begin() as session:
        eligible = (
            select(ResourceImport.id)
            .where(
                or_(
                    ResourceImport.status == "pending",
                    (ResourceImport.status == "copying") & (ResourceImport.lease_until <= now),
                )
            )
            .order_by(ResourceImport.id)
            .limit(1)
        )
        identifier = session.scalar(eligible)
        if identifier is None:
            return False
        lock_resource_project(session, ResourceImport, identifier)
        job = session.scalar(
            select(ResourceImport)
            .where(ResourceImport.id == identifier)
            .with_for_update(skip_locked=True)
        )
        if job is None or not (
            job.status == "pending"
            or (job.status == "copying" and job.lease_until is not None and job.lease_until <= now)
        ):
            return False
        if job.attempts >= 5 or not may_submit(session, job):
            job.status = "failed" if job.attempts >= 5 else "cancelled"
            return True
        token = uuid4().hex
        job.status, job.lease_token, job.lease_until = "copying", token, now + timedelta(minutes=10)
        job.attempts += 1
        identifier, snapshot = job.id, copy.deepcopy(job.snapshot)
    primitive = StorageService(storage, settings)
    written = []
    cleanup = False
    try:
        copied = {}
        for entry in snapshot["media"]:
            location = primitive._location(entry["storage_locator"])
            target_name = f"imports/{identifier}/{entry['copy_id']}"
            written.append(ObjectLocation(location.bucket, target_name).locator)
            stored = storage.copy(location.bucket, location.object_name, target_name)
            if entry.get("byte_size") is not None and entry["byte_size"] != stored.size:
                raise ValueError("Copy size mismatch")
            values = {
                k: v for k, v in entry.items() if k not in {"id", "copy_id", "storage_locator"}
            }
            # Preview manifests reference source objects; copied files are probed again.
            values["video_metadata"] = None
            copied[entry["id"]] = (entry["copy_id"], stored.storage_locator, values)
        with factory.begin() as session:
            lock_resource_project(session, ResourceImport, identifier)
            job = session.scalar(
                select(ResourceImport).where(ResourceImport.id == identifier).with_for_update()
            )
            if job.lease_token != token:
                if job.status in {"cancelled", "failed"}:
                    # A delayed worker may have recreated objects after the janitor.
                    job.cleanup_at = None
                return True
            if job.status == "cancelled" or not may_submit(session, job):
                job.status, job.lease_token, job.lease_until = "cancelled", None, None
                cleanup = True
            else:
                _complete_import(session, job, snapshot, copied)
    except Exception:
        with factory.begin() as session:
            job = session.scalar(
                select(ResourceImport).where(ResourceImport.id == identifier).with_for_update()
            )
            if job and job.lease_token == token:
                job.status = (
                    "cancelled"
                    if job.status == "cancelled"
                    else ("pending" if job.attempts < 5 else "failed")
                )
                job.lease_token = job.lease_until = None
                # A retry uses the same object names. Leave partial objects for
                # that attempt; a concurrent retry must not lose its destination.
                cleanup = job.status in {"cancelled", "failed"}
            elif job and job.status in {"cancelled", "failed"}:
                job.cleanup_at = None
    if cleanup:
        # A stale worker must never delete the new lease owner's stable destination.
        # If COMMIT was uncertain, persisted references protect completed copies.
        with factory() as session:
            referenced = set(
                session.scalars(
                    select(MediaFile.storage_locator).where(MediaFile.storage_locator.in_(written))
                )
            )
        for locator in written:
            if locator not in referenced:
                try:
                    primitive.delete(locator)
                except Exception:
                    pass
    return True


def _complete_import(session, job, snapshot, copied):
    now = utcnow()
    for mid, locator, values in copied.values():
        values = {**values, "published_at": now}
        session.add(
            MediaFile(
                id=mid,
                **values,
                storage_locator=locator,
                project_id=job.project_id,
                scope_user_id=None,
                created_by=job.initiated_by,
                updated_by=job.initiated_by,
                created_at=now,
                updated_at=now,
            )
        )
    session.flush()
    if job.source_type == "asset":
        values = {k: v for k, v in snapshot["resource"].items() if k != "id"}
        values["media_id"] = copied[values["media_id"]][0] if values["media_id"] else None
        values["reference_media_ids"] = [
            str(copied[int(mid)][0]) for mid in values.get("reference_media_ids", [])
        ]
        values["row_version"] = 1
        asset_id = snapshot["asset_id"]
        session.add(
            Asset(
                id=asset_id,
                **values,
                model_id=None,
                project_id=job.project_id,
                scope_user_id=None,
                created_by=job.initiated_by,
                updated_by=job.initiated_by,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        last = (
            session.scalar(
                select(ProjectAsset.position)
                .where(ProjectAsset.project_id == job.project_id)
                .order_by(ProjectAsset.position.desc())
                .limit(1)
            )
            or 0
        )
        session.add(
            ProjectAsset(
                id=next_id(),
                project_id=job.project_id,
                asset_id=asset_id,
                position=last + 1,
                created_by=job.initiated_by,
                created_at=now,
            )
        )
        job.result_id = asset_id
    else:
        job.result_id = copied[job.source_id][0]
    job.status, job.lease_token, job.lease_until = "succeeded", None, None
    session.add(
        AuditEvent(
            id=next_id(),
            project_id=job.project_id,
            actor_user_id=job.initiated_by,
            object_type="resource_imports",
            object_id=str(job.id),
            action="import",
            request_id="worker",
            created_at=now,
        )
    )


def cleanup_imports(factory, storage, settings):
    """Retry orphan cleanup for terminal receipts, including a crashed copy worker."""
    primitive = StorageService(storage, settings)
    now = utcnow()
    with factory() as session:
        jobs = list(
            session.scalars(
                select(ResourceImport)
                .where(
                    ResourceImport.status.in_(["cancelled", "failed"]),
                    ResourceImport.cleanup_at.is_(None),
                    or_(
                        ResourceImport.lease_until.is_(None),
                        ResourceImport.lease_until < now - timedelta(minutes=5),
                    ),
                )
                .order_by(ResourceImport.id)
                .limit(50)
            )
        )
    for job in jobs:
        locators = []
        for entry in job.snapshot["media"]:
            location = primitive._location(entry["storage_locator"])
            locators.append(
                ObjectLocation(location.bucket, f"imports/{job.id}/{entry['copy_id']}").locator
            )
        with factory() as session:
            retained = set(
                session.scalars(
                    select(MediaFile.storage_locator).where(
                        MediaFile.storage_locator.in_(locators),
                    )
                )
            )
        try:
            for locator in set(locators) - retained:
                primitive.delete(locator)
        except Exception:
            continue  # The receipt remains pending cleanup for the next tick.
        with factory.begin() as session:
            current = session.get(ResourceImport, job.id, with_for_update=True)
            if current.status in {"cancelled", "failed"}:
                current.cleanup_at = utcnow()
                current.lease_token = current.lease_until = None
