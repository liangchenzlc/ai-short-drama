import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from short_drama.core.exceptions import BusinessError
from short_drama.domain import Project
from short_drama.schemas import ProjectCreate


def test_services_reject_caller_owned_transaction():
    from short_drama.service.project_service import ProjectService

    with Session() as session, session.begin():
        with pytest.raises(BusinessError, match="transaction"):
            ProjectService(session).get(1)
        assert session.in_transaction()


def test_constructed_schema_does_not_bypass_validation():
    from short_drama.service.project_service import ProjectService

    forged = ProjectCreate.model_construct(name="", aspect="invalid")
    with Session() as session, pytest.raises(ValidationError):
        ProjectService(session).create(forged)


@pytest.mark.parametrize("filters", [{"name OR 1=1": "x"}, {"__table__": "x"}])
def test_dao_filters_only_accept_mapped_fields(filters):
    from short_drama.dao.base import BaseDAO

    with Session() as session, pytest.raises(BusinessError):
        BaseDAO(session, Project).list(filters=filters)


@pytest.mark.parametrize("offset,limit", [(-1, 20), (0, 0), (0, 501)])
def test_pagination_bounds_are_checked_before_database(offset, limit):
    from short_drama.service.project_service import ProjectService

    with Session() as session, pytest.raises(BusinessError):
        ProjectService(session).list(offset=offset, limit=limit)


def test_service_rejects_forged_audit_identity():
    from short_drama.service.project_service import ProjectService

    with Session() as session, pytest.raises(ValidationError):
        ProjectService(session).create_project({"name": "test", "created_by": 10})


@pytest.mark.parametrize(
    "error_kind,code", [("operational", 2006), ("operational", 1213), ("integrity", 1062)]
)
def test_database_errors_keep_safe_diagnostics_and_roll_back(caplog, error_kind, code):
    from types import SimpleNamespace

    from sqlalchemy.exc import IntegrityError, OperationalError

    from short_drama.core.exceptions import Conflict, DatabaseUnavailable
    from short_drama.service.project_service import ProjectService
    from tests.support.generation_fixtures import generation_session

    with generation_session() as session:
        session.info["actor"] = SimpleNamespace(user_id=1, request_id="safe-request-id")
        service = ProjectService(session)
        original = Exception(code, "secret-in-driver-message")
        database_error = IntegrityError if error_kind == "integrity" else OperationalError
        expected = Conflict if code in (1213, 1062) else DatabaseUnavailable
        with pytest.raises(expected), caplog.at_level("INFO"):
            with service._transaction():
                raise database_error(
                    "SELECT 'secret-in-sql'", {"key": "secret-parameter"}, original
                )
        assert not session.in_transaction()
        assert "ProjectService" in caplog.text
        assert "safe-request-id" in caplog.text
        assert str(code) in caplog.text
        assert "write" in caplog.text
        assert "secret-in-driver-message" not in caplog.text
        assert "secret-in-sql" not in caplog.text
        assert "secret-parameter" not in caplog.text
