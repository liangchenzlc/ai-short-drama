"""Existence-only reference checks anchored to an authorized resource row."""

import json

from sqlalchemy import and_, exists, func, or_, select

from short_drama.domain import Base, CanvasResourceDeletion, CanvasResourceUpload


class CanvasResourceDeletionDAO:
    def __init__(self, session):
        self.session = session

    def receipt(self, *, user_id, identity=None, upload_id=None):
        query = select(CanvasResourceDeletion).where(CanvasResourceDeletion.user_id == user_id)
        if identity is not None:
            query = query.where(CanvasResourceDeletion.idempotency_hash == identity)
        if upload_id is not None:
            query = query.where(CanvasResourceDeletion.upload_id == upload_id)
        return self.session.scalar(query.with_for_update())

    def upload(self, resource_id: int):
        return self.session.scalar(
            select(CanvasResourceUpload)
            .where(CanvasResourceUpload.reserved_resource_id == resource_id)
            .with_for_update()
        )

    def _exists(self, resource, queries) -> bool:
        if not queries:
            return False
        # Reference rows may belong to another member. Return only a boolean,
        # never their identifiers or private payloads. The outer ORM query still
        # enforces access to the resource; locking subqueries read current state.
        model = type(resource)
        return (
            self.session.scalar(
                select(model.id).where(model.id == resource.id, or_(*queries)).with_for_update()
            )
            is not None
        )

    @staticmethod
    def _reference(table, predicate):
        return exists(select(1).select_from(table).where(predicate).with_for_update())

    def shared_by_library(self, resource, asset_id: int) -> bool:
        table = Base.metadata.tables["canvas_library_asset_references"].alias()
        field = "media_id" if resource.__tablename__ == "media_files" else "binary_id"
        return self._exists(
            resource,
            [
                self._reference(
                    table, and_(table.c[field] == resource.id, table.c.library_asset_id != asset_id)
                )
            ],
        )

    def referenced(self, resource, *, history: bool = False) -> bool:
        queries = []
        target = resource.__tablename__ + ".id"
        for original in Base.metadata.sorted_tables:
            if original.name in {"canvas_resource_uploads", "canvas_library_asset_references"}:
                continue
            fields = [
                key.parent.name for key in original.foreign_keys if key.target_fullname == target
            ]
            if not fields:
                continue
            table = original.alias()
            predicate = or_(*(table.c[field] == resource.id for field in fields))
            if original.name == "canvas_revision_media_references":
                if not history:
                    continue
            elif original.name.startswith("canvas_") and "owner_kind" in table.c:
                predicate = and_(
                    predicate,
                    table.c.owner_kind == "revision"
                    if history
                    else table.c.owner_kind != "revision",
                )
            elif history:
                continue
            queries.append(self._reference(table, predicate))
        if resource.__tablename__ == "media_files" and not history:
            for name in ("assets", "shot_scripts"):
                table = Base.metadata.tables[name].alias()
                queries.append(
                    self._reference(
                        table,
                        or_(
                            func.json_contains(
                                table.c.reference_media_ids, json.dumps(str(resource.id))
                            )
                            == 1,
                            func.json_contains(table.c.reference_media_ids, str(resource.id)) == 1,
                        ),
                    )
                )
            records = Base.metadata.tables["ai_generation_records"].alias()
            tasks = Base.metadata.tables["async_tasks"].alias()
            terms = [
                str(resource.id),
                f"resource:{resource.id}",
                resource.storage_locator,
                f"%/resources/{resource.id}/%",
                f"%/resources/{resource.id}",
            ]

            def matches(column):
                return or_(
                    *(func.json_search(column, "one", value).is_not(None) for value in terms)
                )

            queries.append(
                exists(
                    select(records.c.id)
                    .join(tasks, tasks.c.id == records.c.task_id)
                    .where(
                        or_(
                            matches(records.c.request_data),
                            and_(
                                tasks.c.status.not_in(("succeeded", "failed", "cancelled")),
                                matches(records.c.response_data),
                            ),
                        )
                    )
                    .with_for_update()
                )
            )
        return self._exists(resource, queries)
