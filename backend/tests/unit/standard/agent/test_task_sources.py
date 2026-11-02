from short_drama.agent.task.sources import source_projection


def test_sources_keep_bounded_verbatim_text_and_skip_forgotten_inputs():
    sources = [
        {"id": "101", "seq": 1, "content": "原始用户要求\n" * 3000},
        *[
            {"id": str(101 + i), "seq": i + 1, "content": f"更新要求{i}：补充真实约束。" * 8}
            for i in range(1, 40)
        ],
    ]
    result = source_projection(sources, {139})
    assert len(result["items"]) <= 32 and result["omitted_messages"] > 0
    assert result["estimated_tokens"] <= result["quota_tokens"] == 4000
    original = result["items"][0]
    assert original["id"] == "101" and original["truncated"]
    assert sources[0]["content"].startswith(original["content"])
    assert result["items"][-1]["id"] == "140"
    assert all(item["id"] != "139" for item in result["items"])
    assert not source_projection(sources[:1], {101})["items"]
