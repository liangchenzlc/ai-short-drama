import asyncio

import httpx


def request_paths(app, paths):
    from short_drama.db.session import build_engine, session_factory

    engine = build_engine(app.state.settings)
    app.state.session_factory = session_factory(engine)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return [await client.get(path) for path in paths]

    try:
        return asyncio.run(request())
    finally:
        engine.dispose()


def test_liveness_does_not_require_database():
    from short_drama.core.config import Settings
    from short_drama.main import create_app

    response, schema = request_paths(
        create_app(Settings(db_host="127.0.0.1", db_port=1)),
        ["/api/v1/test", "/openapi.json"],
    )
    assert response.status_code == 200
    assert response.json() == {"message": "ok"}
    assert "/api/v1/test/db" in schema.json()["paths"]


def test_database_failure_is_sanitized():
    from short_drama.core.config import Settings
    from short_drama.main import create_app

    settings = Settings(db_host="127.0.0.1", db_port=1, db_password="hidden-password")
    (response,) = request_paths(create_app(settings), ["/api/v1/test/db"])
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_unavailable"
    assert "hidden-password" not in response.text
    assert "pymysql" not in response.text
