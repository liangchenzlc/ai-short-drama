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
        ProjectService(session).create({"name": "test", "aspect": "16:9", "created_by": 99})
