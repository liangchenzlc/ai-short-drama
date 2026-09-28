"""Canonical MySQL schema + HTTP + real FFmpeg, without model API calls."""

import asyncio
import subprocess
from io import BytesIO

import httpx
import pytest
from sqlalchemy import inspect
from test_asset_image_generation import flow as flow

from short_drama.api.dependencies import get_session
from short_drama.domain import Base, MediaFile, ShotVideo
from short_drama.main import create_app
from short_drama.service.episode_storyboard_service import EpisodeStoryboardService
from short_drama.service.video_render import VideoRenderer, executable
from short_drama.tasks.render import RenderExecutor

pytestmark = pytest.mark.integration


def test_canonical_schema_covers_every_table_column_constraint(mysql_engine):
    inspector = inspect(mysql_engine)
    assert set(inspector.get_table_names()) == set(Base.metadata.tables)
    for table in Base.metadata.sorted_tables:
        actual = {c["name"]: c for c in inspector.get_columns(table.name)}
        assert set(actual) == set(table.columns.keys())
        for col in table.columns:
            assert actual[col.name]["nullable"] == col.nullable
        assert {c["name"] for c in inspector.get_check_constraints(table.name)} == {
            c.name for c in table.constraints if c.__class__.__name__ == "CheckConstraint"
        }
        assert {c["name"] for c in inspector.get_foreign_keys(table.name)} == {
            c.name for c in table.foreign_key_constraints
        }


def test_http_probe_export_retry_download_apply_and_duplicate_worker(flow, tmp_path, monkeypatch):
    settings = flow.settings.model_copy(update={"render_scratch_root": str(tmp_path / "render")})
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            executable("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x240:rate=24:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        timeout=30,
    )
    data = source.read_bytes()
    flow.storage.put(
        settings.minio_video_bucket, "assembly.mp4", BytesIO(data), len(data), "video/mp4"
    )
    storyboard = EpisodeStoryboardService(flow.session)
    shot = storyboard.create(
        flow.project.id,
        flow.episode.id,
        {"storyboard_version": "1", "script": "A quiet morning", "duration_ms": 5000},
        "assembly-source",
    )["shot"]
    flow.session.add(
        MediaFile(
            id=301,
            format_code="video/mp4",
            original_name="",
            storage_locator=f"minio://{settings.minio_video_bucket}/assembly.mp4",
        )
    )
    flow.session.flush()
    flow.session.add(
        ShotVideo(
            id=401,
            episode_id=flow.episode.id,
            shot_id=int(shot["id"]),
            media_id=301,
            resolution="720p",
            duration=5000,
            context_hash=shot["video_context_hash"],
        )
    )
    flow.session.commit()
    executor = RenderExecutor(flow.factory, settings, flow.storage)
    app = create_app(settings)
    app.state.settings, app.state.storage = settings, flow.storage
    app.dependency_overrides[get_session] = lambda: flow.session
    root = f"/api/v1/projects/{flow.project.id}/episodes/{flow.episode.id}/assembly"

    async def scenario():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            empty = await client.get(root)
            assert empty.status_code == 200 and empty.json()["assembly"] is None
            initialized = await client.post(root + "/initialize")
            assert initialized.status_code == 200, initialized.text
            state = initialized.json()
            probe = state["jobs"][0]
            executor.execute(probe["id"], 1)
            state = (await client.get(root)).json()
            assert state["clips"][0]["duration_ms"] == 2000
            assert state["clips"][0]["issue"] is None
            edit = {
                "row_version": state["assembly"]["row_version"],
                "resolution": "720p",
                "clips": [
                    {
                        "id": state["clips"][0]["id"],
                        "included": True,
                        "muted": True,
                        "trim_in_ms": 300,
                        "trim_out_ms": 1800,
                    }
                ],
            }
            edited = await client.patch(root, json=edit)
            assert edited.status_code == 200, edited.text
            state = edited.json()
            conflict = await client.patch(root, json=edit)
            assert conflict.status_code == 409
            body = {
                "row_version": state["assembly"]["row_version"],
                "source_hash": state["source_hash"],
            }
            submitted = await client.post(
                root + "/exports", json=body, headers={"Idempotency-Key": "export-one"}
            )
            assert submitted.status_code == 202, submitted.text
            job = submitted.json()
            original_put = flow.storage.put

            def fail_upload(*_):
                raise OSError("storage temporarily offline")

            monkeypatch.setattr(flow.storage, "put", fail_upload)
            executor.execute(job["id"], 1)
            failed = (await client.get(root + f"/exports/{job['id']}")).json()
            assert failed["status"] == "failed"
            assert list((tmp_path / "render").glob("*/completed.json"))
            monkeypatch.setattr(flow.storage, "put", original_put)
            retried = await client.post(
                root + f"/exports/{job['id']}/retry", headers={"Idempotency-Key": "retry-one"}
            )
            assert retried.status_code == 202, retried.text
            retry_job = retried.json()

            def no_encode(*_):
                raise AssertionError("retry must reuse verified rendered output")

            monkeypatch.setattr(VideoRenderer, "render", no_encode)
            executor.execute(retry_job["id"], 1)
            executor.execute(retry_job["id"], 1)
            ready = (await client.get(root + f"/exports/{retry_job['id']}")).json()
            assert ready["status"] == "succeeded", ready
            assert abs(ready["duration_ms"] - 1500) < 120
            downloaded = await client.get(root + f"/exports/{retry_job['id']}/download")
            assert downloaded.status_code == 200 and downloaded.content[4:8] == b"ftyp"
            assert "attachment" in downloaded.headers["content-disposition"]
            state = (await client.get(root)).json()
            applied = await client.post(
                root + f"/exports/{retry_job['id']}/apply",
                json={
                    "row_version": state["assembly"]["row_version"],
                    "context_hash": state["context_hash"],
                },
            )
            assert applied.status_code == 200, applied.text
            state = (await client.get(root)).json()
            assert state["assembly"]["current_media_id"] == ready["media_id"]
            forbidden = await client.get(
                root.replace(f"/projects/{flow.project.id}/", "/projects/999/")
                + f"/exports/{retry_job['id']}/download"
            )
            assert forbidden.status_code == 404

    asyncio.run(scenario())
