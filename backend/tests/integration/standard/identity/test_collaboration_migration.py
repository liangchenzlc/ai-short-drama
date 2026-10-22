"""Account ownership remains strict after retiring collaboration and episodes."""

import pytest

from short_drama.core.config import Settings
from short_drama.db.readiness import assert_identity_ready

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        (
            ["ALTER TABLE media_files ALTER CHECK ck_media_file_scope NOT ENFORCED"],
            "scope constraints",
        ),
        (
            [
                "ALTER TABLE media_files DROP CHECK ck_media_file_scope",
                "ALTER TABLE media_files ADD CONSTRAINT ck_media_file_scope CHECK (1=1)",
            ],
            "scope constraints",
        ),
        (
            [
                "ALTER TABLE project_render_jobs DROP FOREIGN KEY "
                "fk_project_render_jobs_initiated_by"
            ],
            "foreign keys",
        ),
        (["ALTER TABLE users DROP INDEX uk_users_email"], "uniqueness constraints"),
    ],
)
def test_readiness_rejects_disabled_or_changed_account_constraints(
    migration_mysql_engine,
    changes,
    message,
):
    settings = Settings(auth_enabled=True)
    assert_identity_ready(migration_mysql_engine, settings)
    with migration_mysql_engine.begin() as connection:
        for statement in changes:
            connection.exec_driver_sql(statement)
    with pytest.raises(RuntimeError, match=message):
        assert_identity_ready(migration_mysql_engine, settings)
