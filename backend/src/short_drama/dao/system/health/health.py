from sqlalchemy import text
from sqlalchemy.orm import Session


class HealthDAO:
    def __init__(self, session: Session):
        self.session = session

    def ping(self) -> bool:
        # Constant infrastructure probe; no application table or caller SQL is exposed.
        return self.session.connection().scalar(text("SELECT 1")) == 1
