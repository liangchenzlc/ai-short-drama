"""Run tests in a NEW disposable schema on the configured MySQL server.

Only the server credentials are reused; the application database is never used
as a test schema. See tests/integration/conftest.py for creation and cleanup.
"""

import os
import subprocess
import sys

from short_drama.core.config import Settings

environment = os.environ.copy()
environment["TEST_DATABASE_URL"] = (
    Settings().database_url.set(database="short_drama_test").render_as_string(hide_password=False)
)
raise SystemExit(
    subprocess.call(
        [sys.executable, "-m", "pytest", *(sys.argv[1:] or ["tests/integration", "-q"])],
        env=environment,
    )
)
