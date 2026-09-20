"""SQLAlchemy persistence operations. Transaction ownership belongs to services."""

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from short_drama.core.exceptions import BusinessError
from short_drama.utils.snowflake import next_id


class BaseDAO:
    def __init__(self, session: Session, model):
        self.session = session
        self.model = model
        self.fields = {
            column.key: getattr(model, column.key) for column in inspect(model).column_attrs
        }

    def _conditions(self, filters):
        conditions = []
        for name, value in (filters or {}).items():
            if name not in self.fields:
                raise BusinessError("Unknown filter field")
            conditions.append(self.fields[name] == value)
        return conditions

    @staticmethod
    def validate_pagination(offset, limit):
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 500:
            raise BusinessError("Pagination requires offset >= 0 and limit between 1 and 500")

    def get(self, identifier, for_update=False):
        statement = select(self.model).where(self.model.id == identifier)
        if for_update:
            statement = statement.with_for_update().execution_options(populate_existing=True)
        return self.session.scalar(statement)

    def list(self, offset=0, limit=20, filters=None):
        self.validate_pagination(offset, limit)
        order = (
            [self.fields["position"], self.model.id]
            if "position" in self.fields
            else [self.model.id]
        )
        statement = (
            select(self.model)
            .where(*self._conditions(filters))
            .order_by(*order)
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def count(self, filters=None):
        statement = select(func.count()).select_from(self.model).where(*self._conditions(filters))
        return self.session.scalar(statement)

    def _validate_values(self, values):
        if any(name not in self.fields for name in values):
            raise BusinessError("Unknown persistence field")

    def create(self, values):
        self._validate_values(values)
        entity = self.model(**{**values, "id": next_id()})
        self.session.add(entity)
        self.session.flush()
        return entity

    def update(self, entity, values):
        self._validate_values(values)
        for name, value in values.items():
            setattr(entity, name, value)
        self.session.flush()
        return entity

    def delete(self, entity):
        self.session.delete(entity)
        self.session.flush()
