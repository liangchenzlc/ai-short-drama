from sqlalchemy import text
from sqlalchemy.orm import Session


class HealthDAO:
    def __init__(self, session: Session):
        self.session = session

    def ping(self) -> bool:
        return self.session.scalar(text("SELECT 1")) == 1
