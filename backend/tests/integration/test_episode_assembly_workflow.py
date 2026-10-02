"""Canonical MySQL schema + HTTP + real FFmpeg, without model API calls."""

import asyncio
import subprocess
from io import BytesIO
from uuid import uuid4

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
    missing = storyboard.create(
        flow.project.id,
        flow.episode.id,
        {
            "storyboard_version": storyboard.get(flow.project.id, flow.episode.id, shot["id"])[
                "storyboard_version"
            ],
            "script": "A shot without adopted video",
            "duration_ms": 5000,
        },
        "assembly-missing-source",
    )["shot"]
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
            assert empty.json()["source_count"] == 1
            initialized = await client.post(root + "/initialize")
            assert initialized.status_code == 200, initialized.text
            state = initialized.json()
            missing_clip = next(c for c in state["clips"] if c["shot_id"] == missing["id"])
            assert missing_clip["included"] is True and missing_clip["issue"] == "missing"
            probe = state["jobs"][0]
            executor.execute(probe["id"], 1)
            state = (await client.get(root)).json()
            assert state["clips"][0]["duration_ms"] == 2000
            assert state["clips"][0]["issue"] is None
            assert state["clips"][0]["filmstrip"]["count"] == 2
            assert state["clips"][0]["filmstrip"]["url"]
            # Reserved shots remain in the draft but never enter a render snapshot.
            preview = await client.post(
                root + "/previews",
                json={
                    "row_version": state["assembly"]["row_version"],
                    "source_hash": state["source_hash"],
                },
                headers={"Idempotency-Key": "preview-with-reserved-shot"},
            )
            assert preview.status_code == 202, preview.text
            assert [c["media_id"] for c in preview.json()["timeline"]] == ["301"]
            executor.execute(preview.json()["id"], 1)
            ready_preview = (await client.get(root + f"/exports/{preview.json()['id']}")).json()
            assert ready_preview["status"] == "succeeded", ready_preview
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
            # A split creates a second persistent instance of the same source.
            split_id = str(uuid4())
            split_body = {
                **edit,
                "row_version": state["assembly"]["row_version"],
                "request_id": "split-once",
                "clips": [
                    {**edit["clips"][0], "trim_out_ms": 1000},
                    {
                        **edit["clips"][0],
                        "id": split_id,
                        "source_clip_id": state["clips"][0]["id"],
                        "trim_in_ms": 1000,
                    },
                ],
            }
            response = await client.patch(root, json=split_body)
            assert response.status_code == 200, response.text
            state = response.json()
            replay = await client.patch(root, json=split_body)
            assert replay.status_code == 200
            assert replay.json()["assembly"]["row_version"] == state["assembly"]["row_version"]
            assert len((await client.get(root)).json()["clips"]) == 2
            prepared = (await client.post(root + "/initialize")).json()
            assert prepared["assembly"]["row_version"] == state["assembly"]["row_version"]
            assert prepared["clips"] == state["clips"]
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
            assert ready["timeline"][0]["media_id"] == "301"
            assert ready["timeline"][0]["filmstrip"]["url"]
            assert ready["aspect"] == "16:9"
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


def test_timeline_migration_is_repeatable_and_preserves_existing_edits(flow):
    import importlib.util
    from pathlib import Path

    from sqlalchemy import text

    from short_drama.service.episode_assembly_service import EpisodeAssemblyService

    storyboard = EpisodeStoryboardService(flow.session)
    storyboard.create(
        flow.project.id,
        flow.episode.id,
        {"storyboard_version": "1", "script": "Migration test", "duration_ms": 5000},
        "migration-source",
    )
    svc = EpisodeAssemblyService(flow.session)
    state = svc.initialize(flow.project.id, flow.episode.id)
    flow.session.commit()
    engine = flow.session.get_bind()
    with engine.begin() as connection:
        # Reconstruct the immediately preceding schema in this disposable test database.
        connection.exec_driver_sql(
            "ALTER TABLE episode_assembly_clips ADD UNIQUE KEY "
            "uk_assembly_clips_shot (assembly_id, shot_id)"
        )
        connection.exec_driver_sql(
            "ALTER TABLE episode_assembly_clips DROP INDEX uk_assembly_clips_client, "
            "DROP INDEX idx_assembly_clips_shot, DROP CHECK ck_assembly_clips_removed, "
            "DROP COLUMN client_key, DROP COLUMN removed"
        )
        connection.exec_driver_sql("ALTER TABLE episode_assemblies DROP COLUMN last_edit_receipt")
        connection.exec_driver_sql(
            "ALTER TABLE episode_render_jobs DROP CHECK ck_render_jobs_kind, "
            "ADD CONSTRAINT ck_render_jobs_kind CHECK (kind IN ('probe','export'))"
        )
        spec = importlib.util.spec_from_file_location(
            "timeline_migration", Path("scripts/apply_timeline_migration.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.migrate(connection)
        module.migrate(connection)
        assert (
            connection.scalar(text("SELECT COUNT(*) FROM episode_assembly_clips WHERE removed = 0"))
            == 1
        )
    flow.session.expire_all()
    restored = svc.get(flow.project.id, flow.episode.id)
    assert restored["clips"][0]["id"] == state["clips"][0]["id"]
