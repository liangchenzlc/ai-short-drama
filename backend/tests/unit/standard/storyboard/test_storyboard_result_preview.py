import json

import pytest
from sqlalchemy import select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AIGenerationRecord, AsyncTask
from short_drama.service.creation.assets.management.assets import AssetService
from short_drama.service.creation.assets.management.library import AssetLibraryService
from short_drama.service.generations.admission.business import GenerationBusinessService
from short_drama.service.generations.admission.generation import AIGenerationService
from short_drama.service.generations.runtime.lifecycle import finish
from tests.support.generation_fixtures import config, generation_session, settings
from tests.unit.standard.writing.test_project_writing import setup


def test_storyboard_preview_preserves_complete_candidates_and_frozen_asset_names_across_pages():
    with generation_session() as session:
        config(session, "text")
        project_id, writing = setup(session)
        saved = writing.save_script(
            project_id,
            {"content_version": "1", "script_id": None, "content": "first second"},
        )
        asset = AssetLibraryService(session).create(
            "project", project_id, project_id, {"kind": "prop", "name": "old name"}, "asset-preview"
        )[0]
        task, _ = AIGenerationService(session, settings).create(
            "text",
            {
                "source": {
                    "scene": "script_shots",
                    "project_id": str(project_id),
                    "script_id": saved["script"]["id"],
                    "content_version": "2",
                }
            },
            "preview-shots",
        )
        task_id = int(task["generation_id"])
        with session.begin():
            record = session.scalar(select(AIGenerationRecord))
            record.text_content = json.dumps(
                {
                    "shots": [
                        {
                            "title": "first title",
                            "script": "first shot",
                            "duration_ms": 2500,
                            "source_excerpt": "first",
                            "story_beat": "opening",
                            "asset_ids": [str(asset.id)],
                        },
                        {
                            "title": "second title",
                            "script": "second shot",
                            "duration_ms": 4000,
                            "source_excerpt": "second",
                            "story_beat": "turn",
                            "asset_ids": [],
                        },
                    ]
                }
            )
            record.response_data = {"finish_reason": "stop"}
            GenerationBusinessService(session).save_text_result(task_id, record.id)
            finish(session.get(AsyncTask, task_id), "succeeded")
        AssetService(session).update(
            asset.id, {"name": "new name", "row_version": str(asset.row_version)}
        )
        business = GenerationBusinessService(session)
        first = business.storyboard_result_page(project_id, task_id, 0, 1)
        assert first["total_duration_ms"] == 6500
        assert first["total"] == 2 and first["applied"] is None
        assert first["items"][0] == {
            "position": 1,
            "title": "first title",
            "shot_size": None,
            "script": "first shot",
            "duration_ms": 2500,
            "source_excerpt": "first",
            "story_beat": "opening",
            "asset_ids": [str(asset.id)],
            "assets": [
                {
                    "id": str(asset.id),
                    "kind": "prop",
                    "name": "old name",
                    "available": True,
                    "snapshot_missing": False,
                }
            ],
        }
        second = business.storyboard_result_page(project_id, task_id, 1, 1)
        assert second["items"][0]["position"] == 2
        assert second["items"][0]["duration_ms"] == 4000
        assert second["total_duration_ms"] == first["total_duration_ms"]
        AssetService(session).delete(asset.id)
        missing = business.storyboard_result_page(project_id, task_id, 0, 1)
        assert missing["items"][0]["assets"][0]["available"] is False
        assert missing["items"][0]["assets"][0]["name"] == "old name"
        with pytest.raises(WorkflowError) as mismatch:
            business.storyboard_result_page(project_id + 1, task_id)
        assert mismatch.value.code == "not_found"
