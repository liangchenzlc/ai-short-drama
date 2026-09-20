from test_ai_validation import send


def test_task_status_filter_only_accepts_five_states():
    document = send("GET", "/openapi.json").json()
    parameters = document["paths"]["/api/v1/ai/generations"]["get"]["parameters"]
    status = next(p for p in parameters if p["name"] == "status")
    schema = next(item for item in status["schema"]["anyOf"] if "enum" in item)
    assert schema["enum"] == ["queued", "running", "succeeded", "failed", "cancelled"]
    assert send("GET", "/api/v1/ai/generations?status=unknown").status_code == 422


def test_config_capabilities_has_a_read_only_endpoint():
    document = send("GET", "/openapi.json").json()
    path = document["paths"].get("/api/v1/ai-model-configs/{config_id}/capabilities", {})
    assert set(path) == {"get"}


def test_three_separate_generation_schemas_and_required_idempotency_header():
    document = send("GET", "/openapi.json").json()
    references = []
    for kind, payload in [
        ("text", {"input": {"messages": [{"role": "user", "content": "hi"}]}}),
        ("image", {"input": {"prompt": "rain"}}),
        ("video", {"input": {"prompt": "rain"}}),
    ]:
        path = f"/api/v1/ai/generations/{kind}"
        assert path in document["paths"]
        references.append(
            document["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"][
                "$ref"
            ]
        )
        assert send("POST", path, json=payload).status_code == 422
    assert len(set(references)) == 3


def test_unknown_generation_source_and_missing_adoption_expectation_rejected():
    assert (
        send(
            "POST",
            "/api/v1/ai/generations/image",
            headers={"Idempotency-Key": "test"},
            json={"input": {"prompt": "rain"}, "source": {"scene": "demo", "shot_id": "abc"}},
        ).status_code
        == 422
    )
    assert (
        send(
            "POST",
            "/api/v1/media-library/items/1/apply",
            json={"target": {"type": "shot_image", "id": "1"}},
        ).status_code
        == 422
    )
