from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from short_drama.core.exceptions import DatabaseUnavailable
from short_drama.dao.health_dao import HealthDAO


class HealthService:
    def __init__(self, session: Session):
        self.session = session

    def check_database(self) -> None:
        try:
            with self.session.begin():
                if not HealthDAO(self.session).ping():
                    raise DatabaseUnavailable("Database is unavailable")
        except SQLAlchemyError as exc:
            raise DatabaseUnavailable("Database is unavailable") from exc
