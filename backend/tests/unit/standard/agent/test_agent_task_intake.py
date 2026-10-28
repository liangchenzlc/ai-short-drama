"""Server-authored requirement drafts: deterministic, quote-backed, bounded."""

from short_drama.agent.task.intake import draft_requirements


def test_two_characters_and_one_scene_keep_their_own_counts():
    drafts = draft_requirements("保存两个角色和一个场景，并回读角色名称。")
    by_asset = {draft.asset_kind: draft for draft in drafts if draft.kind == "write"}
    assert by_asset["character"].minimum_count == 2
    assert by_asset["scene"].minimum_count == 1
    for draft in drafts:
        assert draft.quote_end > draft.quote_start


def test_absent_action_words_produce_no_drafts():
    assert draft_requirements("今天天气不错") == []
    assert draft_requirements("") == []


def test_quote_span_slices_the_user_sentence_exactly():
    content = "先读取创作目标目录，再保存两个角色。"
    drafts = draft_requirements(content)
    assert drafts and drafts[-1].kind == "write"
    quote = content[drafts[-1].quote_start : drafts[-1].quote_end]
    assert "保存两个角色" in quote
    assert quote == drafts[-1].description


def test_draft_keys_are_unique_and_bounded():
    drafts = draft_requirements("保存角色、场景、道具、分镜、台词、图片和视频。")
    assert len(drafts) <= 32
    assert len({draft.key for draft in drafts}) == len(drafts)
