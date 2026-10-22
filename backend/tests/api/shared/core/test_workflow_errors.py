import asyncio

import httpx

from short_drama.core.config import Settings
from short_drama.core.exceptions import WorkflowError
from short_drama.main import create_app


def test_workflow_error_preserves_reference_ids_without_leaking_extra_details():
    async def run():
        app = create_app(Settings(_env_file=None))

        @app.get("/workflow-error")
        def error():
            raise WorkflowError(
                "asset_in_use",
                "Asset is referenced",
                details={
                    "current_version": 8,
                    "references": [
                        "123",
                        {"type": "shot", "id": "456", "name": "Shot 2", "private": "secret"},
                        "invalid-secret",
                    ],
                    "credential": "secret",
                },
            )

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get("/workflow-error")
        assert response.status_code == 409
        assert response.json()["error"]["details"] == {
            "current_version": "8",
            "references": ["123", {"type": "shot", "id": "456", "name": "Shot 2"}],
        }
        assert "secret" not in response.text

    asyncio.run(run())
