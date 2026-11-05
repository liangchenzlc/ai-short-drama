from fastapi.testclient import TestClient
from starlette.datastructures import State

from short_drama.ai.prompts.registry import assistant_prompt
from short_drama.api.mcp.server import create_mcp_app
from short_drama.core.config import Settings
from short_drama.main import create_app


def test_openapi_uses_the_shared_brand_instead_of_environment_override(monkeypatch):
    monkeypatch.setenv("APP_NAME", "Obsolete brand override")
    app = create_app(Settings(_env_file=None, auth_enabled=False, agent_enabled=False))

    assert app.openapi()["info"]["title"] == "Miniart"


def test_mcp_initialization_reports_the_shared_brand():
    with TestClient(create_mcp_app(State())) as client:
        response = client.post(
            "/",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "brand-test", "version": "1"},
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "Miniart"


def test_assistant_identity_is_branded_without_formatting_json(monkeypatch):
    from short_drama.ai.prompts import registry

    original_load = registry.load_prompt

    def template_with_json(path):
        text = original_load(path)
        return text + '\n示例：{"schema_version": 1}' if path == "assistant/system.md" else text

    monkeypatch.setattr(registry, "load_prompt", template_with_json)
    instructions = '保留这个 JSON：{"name": "用户作品", "items": []}'
    prompt = assistant_prompt(instructions)

    assert prompt.startswith("你是 Miniart 的通用创作助手。")
    assert prompt.endswith(instructions)
    assert '{"schema_version": 1}' in prompt
