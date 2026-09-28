"""Exercise the five prompt entry points via HTTP + MySQL + controlled generation.

No paid provider is called. These tests verify transport, snapshots, execution,
JSON projection and image archival; they do not judge model creativity.
"""

import asyncio
import json
from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select
from test_asset_image_generation import ImageProvider, create_asset, drain
from test_asset_image_generation import flow as flow

from short_drama.ai import GenerationResult
from short_drama.api.dependencies import get_session
from short_drama.domain import AIGenerationRecord
from short_drama.main import create_app
from short_drama.service.ai_model_config_service import AIModelConfigService
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.episode_writing_service import EpisodeWritingService
from short_drama.service.generation_execution_service import GenerationExecutionService

pytestmark = pytest.mark.integration

SCRIPT = "## SC001 内 · 老宅客厅 · 日\n林晚拿起红伞。"
SHOT = (
    "场景：老宅客厅，日。\n画面：中景，平视。\n起点：林晚右手空置，红伞在桌上。"
    "\n动作：右手握住伞柄并拿起。\n终点：红伞在右手。\n声音：无对白。"
    "\n冻结首帧：中式写实，林晚站在桌边，右手空置，红伞平放桌上。"
)
ASSET_PROMPTS = {
    "character": "[骨相] 宽额、柔和下颌。\n[服装] 灰色长衫。\n[四视图版式] 面部特写及全身三视图。",
    "scene": (
        "[空间布局] 入口朝向木桌，窗在桌右。\n[光源] 日光从右窗照入。\n[用途] 一张完整场景图。"
    ),
    "prop": "[整体形制] 收拢的长柄伞。\n[材质] 红色织物与木柄。\n[四视图版式] 正侧背面与细节。",
}


class BusinessProvider(ImageProvider):
    def submit(self, snapshot, request, *args, **kwargs):
        scene = request["source"]["scene"]
        if scene in {"asset_image", "shot_image"}:
            return super().submit(snapshot, request, *args, **kwargs)
        self.calls.append(deepcopy(request))
        if scene == "novel_script":
            content = SCRIPT
        elif scene == "script_assets":
            content = json.dumps(
                {
                    "schema_version": 1,
                    "items": [
                        {
                            "kind": kind,
                            "name": {"character": "林晚", "scene": "老宅客厅", "prop": "红伞"}[
                                kind
                            ],
                            "description": "剧情素材与稳定视觉设计。",
                            "prompt": ASSET_PROMPTS[kind],
                            "importance": "core",
                            "story_function": "承载拿伞行动及其视觉连续性。",
                        }
                        for kind in request["source_snapshot"]["extraction"]["kinds"]
                    ],
                },
                ensure_ascii=False,
            )
        else:
            content = json.dumps(
                {
                    "shots": [
                        {
                            "title": "拿起红伞",
                            "source_excerpt": "林晚拿起红伞。",
                            "story_beat": "红伞从桌上转移到林晚手中。",
                            "script": SHOT,
                            "duration_ms": 3000,
                            "asset_ids": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return GenerationResult(
            status="succeeded", adapter="openai_chat.v1", text=content, finish_reason="stop"
        )


def submit_through_http(flow, kind, body):
    """Use real routes/services with isolated DB sessions, then execute the queued work."""
    provider = BusinessProvider()
    flow.provider = provider
    flow.executor = GenerationExecutionService(flow.factory, flow.settings, provider, flow.storage)

    async def run():
        app = create_app(flow.settings)
        app.state.settings = flow.settings
        app.state.storage = flow.storage

        def session():
            with flow.factory() as database:
                yield database

        app.dependency_overrides[get_session] = session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://testserver"
        ) as client:
            path = f"/api/v1/ai/generations/{kind}"
            headers = {"Idempotency-Key": str(uuid4())}
            created = await client.post(path, json=body, headers=headers)
            assert created.status_code == 202, created.text
            task_id = created.json()["generation_id"]
            replay = await client.post(path, json=body, headers=headers)
            assert replay.status_code == 200, replay.text
            assert replay.json()["generation_id"] == task_id
            assert drain(flow, task_id) == "succeeded"
            detail = await client.get(f"/api/v1/ai/generations/{task_id}")
            assert detail.status_code == 200, detail.text
            assert detail.json()["status"] == "succeeded"
            return task_id, detail.json()

    task_id, detail = asyncio.run(run())
    assert len(provider.calls) == 1
    with flow.factory() as session:
        record = session.scalar(
            select(AIGenerationRecord).where(AIGenerationRecord.task_id == int(task_id))
        )
        return deepcopy(record.request_data), deepcopy(record.response_data), detail


@pytest.mark.parametrize("scene", ["novel_script", "script_assets", "script_shots"])
def test_text_business_prompt_http_execution_and_structured_results(flow, scene):
    config = AIModelConfigService(flow.session).create(
        {
            "service_type": "text",
            "name": "controlled text",
            "model_key": "fixture",
            "provider": "ark",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        }
    )
    writing = EpisodeWritingService(flow.session)
    p, e = flow.project.id, flow.episode.id
    source = {"scene": scene, "project_id": str(p), "episode_id": str(e)}
    if scene == "novel_script":
        saved = writing.save_novel(p, e, {"content_version": "1", "content": "林晚拿起红伞。"})
        source["content_version"] = saved["content_version"]
    else:
        saved = writing.save_script(
            p, e, {"content_version": "1", "script_id": None, "content": SCRIPT}
        )
        source["script_id"] = saved["script"]["id"]
        confirmed = writing.confirm(
            p, e, source["script_id"], {"content_version": saved["content_version"]}
        )
        source["content_version"] = confirmed["content_version"]
    body = {"config_id": str(config.id), "source": source, "instructions": "保留红伞"}
    request, response, _ = submit_through_http(flow, "text", body)
    assert request["template_version"] == scene.replace("_", "-") + "-v2"
    messages = request["input"]["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    assert json.loads(messages[1]["content"])["additional_requirements"] == "保留红伞"
    result = response["business_result"]
    if scene == "script_assets":
        assert {item["draft"]["kind"] for item in result["items"]} == set(ASSET_PROMPTS)
        for item in result["items"]:
            assert item["draft"]["prompt"] == ASSET_PROMPTS[item["draft"]["kind"]]
        assert "[骨相]" in messages[0]["content"]
        assert "[空间布局]" in messages[0]["content"]
        assert "[结构与功能]" in messages[0]["content"]
    elif scene == "script_shots":
        assert result["shots"][0]["script"] == SHOT
        assert result["shots"][0]["source_excerpt"] in SCRIPT
        assert "冻结首帧" in messages[0]["content"]
    else:
        assert writing.candidates(p, e, script_id=result["script_id"])["content"] == SCRIPT
        assert "只返回剧本正文" in messages[0]["content"]


@pytest.mark.parametrize(
    ("kind", "view_title"),
    [("character", "人物四视图版式"), ("prop", "道具四视图版式"), ("scene", "场景单图版式")],
)
def test_asset_image_http_execution_selects_layout_and_archives_one_sheet(flow, kind, view_title):
    asset = create_asset(flow, kind)
    asset = flow.library.patch(
        asset.id, {"row_version": str(asset.row_version), "prompt": ASSET_PROMPTS[kind]}
    )
    request, _, detail = submit_through_http(
        flow,
        "image",
        {
            "config_id": str(flow.config.id),
            "input": {"prompt": "柔和光线"},
            "parameters": {"count": 1, "aspect": "16:9"},
            "source": {
                "scene": "asset_image",
                "asset_id": str(asset.id),
                "row_version": str(asset.row_version),
            },
        },
    )
    assert request["source_snapshot"]["template_version"] == "asset-image-v2"
    assert request["source_snapshot"]["asset"]["prompt"] == ASSET_PROMPTS[kind]
    assert f"## {view_title}" in request["input"]["prompt"]
    assert "柔和光线" in request["input"]["prompt"]
    assert len(detail["result"]["assets"]) == 1
    assert flow.images.list(asset.id)["total"] == 1
    assert flow.library.get(asset.id).media_id is None


@pytest.mark.parametrize("layout", ["single", "four", "five", "nine"])
def test_shot_image_http_execution_preserves_layout_and_frozen_start(flow, layout):
    board = EpisodeStoryboardService(flow.session)
    created = board.create(
        flow.project.id,
        flow.episode.id,
        {
            "storyboard_version": "1",
            "script": SHOT,
            "image_settings": {"resolution": "2K", "aspect": "inherit", "layout": layout},
        },
        str(uuid4()),
    )
    shot = created["shot"]
    request, _, detail = submit_through_http(
        flow,
        "image",
        {
            "config_id": str(flow.config.id),
            "input": {"prompt": ""},
            "parameters": {"count": 1, "aspect": "16:9", "resolution": "2K"},
            "source": {
                "scene": "shot_image",
                "shot_id": shot["id"],
                "layout": layout,
                "context_mode": "saved",
                "row_version": shot["row_version"],
                "context_hash": shot["context_hash"],
            },
        },
    )
    assert request["template_version"] == "shot-image-v2"
    assert request["source_snapshot"]["shot"]["script"] == SHOT
    assert f"## 本次布局：{layout}" in request["input"]["prompt"]
    assert len(detail["result"]["assets"]) == 1
