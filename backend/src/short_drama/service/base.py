"""Service-owned atomic operations and shared ownership/reference invariants."""

import logging
from contextlib import contextmanager, nullcontext
from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from short_drama.core.exceptions import BusinessError, Conflict, DatabaseUnavailable, NotFound
from short_drama.core.time import utcnow
from short_drama.dao.base import BaseDAO
from short_drama.domain import AIModelConfig, MediaFile
from short_drama.schemas.base import parse_identifier

logger = logging.getLogger(__name__)


class Page(BaseModel):
    items: list[Any]
    total: int
    offset: int
    limit: int


class BaseService:
    model = None
    create_schema = None
    update_schema = None
    read_schema = None
    parent_model = None
    parent_field = None
    global_ordered = False

    def __init__(self, session: Session):
        self.session = session
        self.dao = BaseDAO(session, self.model)

    @contextmanager
    def _transaction(self, *, read_only=False, lock_request_project=True):
        # 创建跨项目来源时仅延后隐式行锁，当前请求项目授权仍先核验。
        if self.session.in_transaction():
            raise BusinessError("Service operation requires its own transaction")
        try:
            mutex = (
                self._global_order_lock()
                if self.global_ordered and not read_only
                else nullcontext()
            )
            with mutex, self.session.begin():
                project_id = self.session.info.get("request_project")
                if project_id:
                    from short_drama.db.access import require_project

                    require_project(
                        self.session, project_id, lock=not read_only and lock_request_project
                    )
                yield
        except IntegrityError as error:
            self._log_database_failure(error, read_only=read_only, expected=True)
            raise Conflict("Operation conflicts with an existing or referenced record") from None
        except OperationalError as error:
            code = (getattr(error.orig, "args", ()) or (None,))[0]
            self._log_database_failure(
                error, read_only=read_only, expected=code in (1205, 1213, 3572)
            )
            if code in (1205, 1213, 3572):
                raise Conflict("Concurrent operation conflict; retry operation") from None
            raise DatabaseUnavailable("Database operation unavailable") from None
        except DBAPIError as error:
            self._log_database_failure(error, read_only=read_only)
            raise DatabaseUnavailable("Database operation unavailable") from None

    def _log_database_failure(self, error, *, read_only, expected=False):
        actor = self.session.info.get("actor")
        args = getattr(error.orig, "args", ())
        code = args[0] if args and type(args[0]) is int else "unknown"
        # DBAPIError's message, SQL, parameters and even its driver exception may
        # contain credentials or user content. Record only allowlisted diagnostics.
        logger.log(
            logging.WARNING if expected else logging.ERROR,
            "Database transaction failed service=%s operation=%s request_id=%s project_id=%s "
            "error=%s code=%s",
            type(self).__name__,
            "read" if read_only else "write",
            getattr(actor, "request_id", None),
            self.session.info.get("request_project"),
            type(error).__name__,
            code,
        )

    @contextmanager
    def _global_order_lock(self):
        # Keep the advisory lock on a dedicated connection until the service transaction
        # commits. This also serializes empty libraries, irrespective of isolation level.
        engine = self.session.get_bind().engine
        with engine.connect() as connection:
            name = connection.scalar(
                text(
                    "SELECT CONCAT('short_drama:global:', "
                    "LEFT(SHA2(CONCAT(DATABASE(), :scope), 256), 40))"
                ),
                {"scope": str(getattr(self.session.info.get("actor"), "user_id", 0))},
            )
            acquired = connection.scalar(text("SELECT GET_LOCK(:name, 5)"), {"name": name})
            if acquired != 1:
                raise Conflict("Global library is busy; retry operation")
            try:
                yield
            finally:
                connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})

    def _payload(self, schema, payload):
        if schema is None:
            raise BusinessError("This record does not support this operation")
        if isinstance(payload, BaseModel):
            if not isinstance(payload, schema):
                raise BusinessError("Incorrect payload schema")
            payload = payload.model_dump(exclude_unset=True)
        # Revalidation is mandatory even for Pydantic model_construct/model_copy inputs.
        return schema.model_validate(payload).model_dump(exclude_unset=True)

    def _read(self, entity):
        return self.read_schema.model_validate(entity)

    def _require(self, model, identifier, for_update=True):
        row = BaseDAO(self.session, model).get(parse_identifier(identifier), for_update=for_update)
        if row is None:
            raise NotFound("Referenced record does not exist")
        return row

    def _lock_parent(self, values):
        if self.parent_model is not None:
            return self._require(self.parent_model, values[self.parent_field])
        if self.global_ordered:
            # The transaction-level advisory lock serializes all global collection writes.
            list(
                self.session.scalars(
                    select(self.model).order_by(self.model.position).with_for_update()
                )
            )
        return None

    def _get_locked(self, identifier):
        identifier = parse_identifier(identifier)
        if self.parent_model is not None:
            parent_id = self.session.scalar(
                select(getattr(self.model, self.parent_field)).where(self.model.id == identifier)
            )
            if parent_id is None:
                raise NotFound("Record does not exist")
            self._lock_parent({self.parent_field: parent_id})
        elif self.global_ordered:
            self._lock_parent({})
        entity = self.dao.get(identifier, for_update=True)
        if entity is None:
            raise NotFound("Record does not exist")
        return entity

    def _validate_model(self, identifier, service_type):
        if identifier is None:
            return None
        model = self._require(AIModelConfig, identifier)
        if model.service_type != service_type or not model.enabled or model.is_deleted:
            raise BusinessError("Selected model is unavailable for this operation")
        return model

    def _validate_media(self, identifier, kind, *, for_update=True):
        if identifier is None:
            return None
        media = self._require(MediaFile, identifier, for_update=for_update)
        is_image = media.format_code == "demo:image" or media.format_code.startswith("image/")
        if (kind == "image" and not is_image) or (
            kind in {"video", "audio"} and not media.format_code.startswith(f"{kind}/")
        ):
            raise BusinessError("Media type does not match this operation")
        return media

    def _validate_create(self, values):
        pass

    def _validate_update(self, entity, values):
        pass

    def _before_delete(self, entity):
        pass

    def _creation_audit(self, values):
        now = utcnow()
        for key in ("created_at", "updated_at"):
            if key in self.dao.fields:
                values[key] = now
        for key in ("created_by", "updated_by"):
            if key in self.dao.fields:
                values[key] = getattr(self.session.info.get("actor"), "user_id", None)
        return values

    def _apply_update(self, entity, values):
        changes = {key: value for key, value in values.items() if getattr(entity, key) != value}
        if changes:
            if "updated_at" in self.dao.fields:
                changes["updated_at"] = max(utcnow(), entity.created_at or datetime.min)
            if "updated_by" in self.dao.fields:
                changes["updated_by"] = getattr(self.session.info.get("actor"), "user_id", None)
            self.dao.update(entity, changes)
        return entity

    def create(self, payload):
        values = self._payload(self.create_schema, payload)
        with self._transaction():
            self._lock_parent(values)
            self._validate_create(values)
            return self._read(self.dao.create(self._creation_audit(values)))

    def get(self, identifier):
        identifier = parse_identifier(identifier)
        with self._transaction(read_only=True):
            return self._read(self._require(self.model, identifier, for_update=False))

    def list(self, offset=0, limit=20, filters=None):
        self.dao.validate_pagination(offset, limit)
        with self._transaction(read_only=True):
            return Page(
                items=[self._read(row) for row in self.dao.list(offset, limit, filters)],
                total=self.dao.count(filters),
                offset=offset,
                limit=limit,
            )

    def update(self, identifier, payload):
        values = self._payload(self.update_schema, payload)
        with self._transaction():
            entity = self._get_locked(identifier)
            self._validate_update(entity, values)
            return self._read(self._apply_update(entity, values))

    def delete(self, identifier):
        with self._transaction():
            entity = self._get_locked(identifier)
            self._before_delete(entity)
            self.dao.delete(entity)

    def reorder(self, parent_id, identifiers):
        if "position" not in self.dao.fields:
            raise BusinessError("This record is not ordered")
        ids = [parse_identifier(identifier) for identifier in identifiers]
        if len(ids) != len(set(ids)):
            raise BusinessError("Reorder IDs must be unique")
        with self._transaction():
            filters = {}
            if self.parent_model is not None:
                filters[self.parent_field] = parse_identifier(parent_id)
            elif parent_id is not None:
                raise BusinessError("Global ordering has no parent")
            self._lock_parent(filters)
            statement = (
                select(self.model)
                .where(*self.dao._conditions(filters))
                .order_by(self.model.position, self.model.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            rows = list(self.session.scalars(statement))
            by_id = {row.id: row for row in rows}
            if set(ids) != set(by_id):
                raise BusinessError("Reorder requires every record in the collection exactly once")
            changed = {
                identifier
                for position, identifier in enumerate(ids, 1)
                if by_id[identifier].position != position
            }
            if not changed:
                return [self._read(by_id[identifier]) for identifier in ids]
            maximum = max((row.position for row in rows), default=0)
            if maximum + len(rows) > 2**32 - 1:
                raise Conflict("Insufficient temporary ordering space")
            # Move the entire collection above its old maximum before assigning final ranks.
            for position, identifier in enumerate(ids, maximum + 1):
                self.dao.update(by_id[identifier], {"position": position})
            now = utcnow()
            for position, identifier in enumerate(ids, 1):
                values = {"position": position}
                if identifier in changed and "updated_at" in self.dao.fields:
                    values["updated_at"] = max(now, by_id[identifier].created_at or datetime.min)
                    values["updated_by"] = None
                self.dao.update(by_id[identifier], values)
            return [self._read(by_id[identifier]) for identifier in ids]
