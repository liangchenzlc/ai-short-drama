"""原导演台真实WebGL、永久PNG/白膜视频、场景恢复与权限；不调用供应商。"""

import hashlib
import json
import os
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageChops
from sqlalchemy import select

from short_drama.domain import AsyncTask, CanvasResourceUpload, MediaFile
from short_drama.domain.canvas import CanvasEdge, CanvasNode
from short_drama.domain.canvas_library import CanvasLibraryAsset, CanvasLibraryAssetReference
from short_drama.service.canvas_timeline_renderer import CanvasTimelineRenderer
from short_drama.storage.models import ObjectLocation
from tests.integration.test_canvas_browser import isolated_api, stop_browser
from tests.integration.test_canvas_library import database_errors as database_errors
from tests.integration.test_canvas_resources import ROOT, canvas
from tests.integration.test_canvas_resources import resource_app as resource_app
from tests.integration.test_canvas_timeline_ffmpeg import ffmpeg
from tests.integration.test_identity_collaboration import PASSWORD, account, join
from tests.integration.test_identity_collaboration import identity_app as identity_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_CANVAS_RESOURCE_MINIO") != "1"
        or os.environ.get("RUN_CANVAS_BROWSER_INTEGRATION") != "1",
        reason="Enable original director WebGL with isolated HTTP/MySQL/MinIO",
    ),
]


@pytest.mark.parametrize("width", [1440, 600])
@pytest.mark.parametrize(
    ("scene_kind", "output_kind"),
    [
        ("geometry", "image"),
        ("geometry", "video"),
        ("actor", "video"),
        ("multitrack", "video"),
        ("render_views", "image"),
    ],
)
def test_original_director_webgl_persists_scene_screenshot_and_canvas_output(
    resource_app, database_errors, tmp_path, width, scene_kind, output_kind
):
    if output_kind == "video" and os.environ.get("RUN_CANVAS_TIMELINE_FFMPEG") != "1":
        pytest.skip("Enable real FFmpeg verification for recorded clay video")
    video_output = output_kind == "video"
    node = shutil.which("node")
    assert node, "Node.js is required for original director UI verification"
    frontend = Path(__file__).resolve().parents[3] / "frontend/canvas"
    owner, owner_user = account(resource_app, "director_owner")
    member, user = account(resource_app, "director_member")
    outsider, _ = account(resource_app, "director_outsider")
    project, path, document = canvas(owner, "director-project")
    join(resource_app, owner, member, project["id"], user["id"])
    settings = resource_app[2].model_copy(update={"public_origin": "http://127.0.0.1:4199"})
    with isolated_api(resource_app[1], settings, resource_app[0].state.storage) as api:
        config = {
            "apiUrl": api,
            "projectId": project["id"],
            "canvasId": project["primary_canvas_id"],
            "sourceKey": document["id"],
            "username": "director_owner",
            "memberUsername": "director_member",
            "password": PASSWORD,
            "width": width,
            "outputKind": output_kind,
            "sceneKind": scene_kind,
            "outputDirectory": str(tmp_path / "browser"),
        }
        process = subprocess.Popen(
            [node, "scripts/verify-director-python.mjs"],
            cwd=frontend,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            output, errors = process.communicate(json.dumps(config), timeout=240)
        except subprocess.TimeoutExpired:
            stop_browser(process)
            output, errors = process.communicate(timeout=10)
            pytest.fail(f"Director browser timed out: {errors}\n{output}")
        finally:
            if process.poll() is None:
                stop_browser(process)
                process.communicate(timeout=10)
        assert process.returncode == 0, (
            f"Director browser failed: {errors}\n{output}\nDatabase: {database_errors}"
        )
        report = json.loads(output)
    assert report["step"] == "complete"
    assert report["page_errors"] == report["http_errors"] == database_errors == []
    resource_count = 5 if video_output else 4 if scene_kind == "render_views" else 3
    assert report["resource_posts"] == resource_count and report["task_posts"] == 0
    assert report["created_from_original_menu"]
    assert report["cookie_only_recovery"] and report["member_result_seen"]
    if scene_kind == "render_views":
        assert report["original_render_mode_gate"] and report["fresh_render_mode_reset"]
        assert report["beauty_screenshot_in_depth_view"]
        views = report["render_views"]
        assert [item["mode"] for item in views] == ["beauty", "depth", "normal"]
        assert len({item["checksum"] for item in views}) == 3
        for item in views:
            raw = (tmp_path / "browser" / item["filename"]).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == item["checksum"]
            with Image.open(BytesIO(raw)) as image:
                assert image.size == (item["width"], item["height"])
                assert image.width > 100 and image.height > 100
                pixels = list(image.convert("RGB").getdata())
                if item["mode"] == "depth":
                    gray = sum(max(pixel) - min(pixel) <= 3 for pixel in pixels)
                    assert gray > len(pixels) * 0.8
                elif item["mode"] == "normal":
                    colored = sum(max(pixel) - min(pixel) >= 60 for pixel in pixels)
                    assert colored > len(pixels) * 0.05
        # 原截图动作始终保存beauty；与之前从构图预览截图的真实像素逐项比较。
        with (
            Image.open(tmp_path / "browser" / "director-screenshot.png") as image,
            Image.open(tmp_path / "browser" / "director-reference-screenshot.png") as reference,
        ):
            assert image.size == reference.size
            assert (
                ImageChops.difference(image.convert("RGB"), reference.convert("RGB")).getbbox()
                is None
            )
    final = owner.get(path + "/my-document").json()["source_document"]
    assert len(final["nodes"]) == (3 if video_output else 2)
    assert len(final["connections"]) == (2 if video_output else 1)
    assert final["directorScenes"] == [report["scene"]]
    scene = final["directorScenes"][0]
    assert scene["id"] == report["scene_id"] and scene["activeShotId"] == report["shot_id"]
    assert len(scene["objects"]) == (2 if scene_kind in {"actor", "multitrack"} else 1)
    if scene_kind in {"actor", "multitrack"}:
        actor = scene["objects"][0]
        assert actor["id"] == report["actor_id"] and actor["actorPreset"] == "standard_male"
        assert actor["pose"] == "walk" and actor["rig"]["status"] == "ready"
        assert len(actor["rig"]["boneMap"]) >= 8 and actor["rig"]["animationNames"] == []
        assert actor["motionPath"]["kind"] == "line" and actor["motionPath"]["duration"] == 10
        assert len(actor["keyframes"]) == 2
        assert [frame["time"] for frame in actor["keyframes"]] == [0, 10]
        start, end = [frame["transform"]["position"] for frame in actor["keyframes"]]
        assert end == [start[0], start[1], start[2] + 4]
        assert len(scene["cameras"]) == 2
        assert scene["objects"][1]["primitive"] == "box"
        camera = next(
            item for item in scene["cameras"] if item["id"] == scene["shots"][0]["cameraId"]
        )
        assert camera["followObjectId"] == camera["lookAtObjectId"] == actor["id"]
        assert scene["shots"][0]["prompt"] == "演员姿势、直线路径与跟拍恢复验证"
        if scene_kind == "multitrack":
            prop = scene["objects"][1]
            assert prop["id"] == report["prop_id"] == report["multitrack"]["prop_id"]
            assert prop["animationTrackEnabled"] and prop["motionPath"]["kind"] == "rectangle"
            assert prop["motionPath"]["duration"] == 10
            assert [frame["time"] for frame in prop["keyframes"]] == [0, 2.5, 5, 7.5, 10]
            assert camera["id"] == report["multitrack"]["camera_id"]
            assert camera["drawnPath"]["kind"] == "rectangle"
            assert [frame["time"] for frame in camera["keyframes"]] == [0, 2.5, 5, 7.5, 10]
            assert report["multitrack"]["history_restored"]
            assert [item["label"] for item in report["multitrack_transport"]] == [
                "produced",
                "restored",
                "member",
            ]
            for transport in report["multitrack_transport"]:
                assert transport["keyboard_bounds"] and transport["zoom_bounds"]
                assert transport["scene_unchanged"] and transport["duration"] == 10
                assert 0.2 < transport["paused_time"] < 10
            for pair in report["multitrack_frames"]:
                first = Image.open(tmp_path / "browser" / pair["first"]["filename"]).convert("RGB")
                middle = Image.open(tmp_path / "browser" / pair["middle"]["filename"]).convert(
                    "RGB"
                )
                assert first.size == middle.size
                assert (first.width, first.height) == (
                    pair["first"]["width"],
                    pair["first"]["height"],
                )
                changed = sum(
                    sum(abs(a - b) for a, b in zip(left, right, strict=True)) > 30
                    for left, right in zip(first.getdata(), middle.getdata(), strict=True)
                )
                assert changed > first.width * first.height * 0.005, (pair["label"], changed)
    else:
        assert scene["objects"][0]["primitive"] == "box"
        assert scene["shots"][0]["prompt"] == "立方体构图与永久截图验证"
    assert scene["aspectRatio"] == "16:9"
    assert len(scene["shots"][0]["screenshots"]) == (2 if scene_kind == "render_views" else 1)
    source = next(item for item in final["nodes"] if item["id"] == report["director_node_id"])
    assert source["metadata"]["directorPreviewNodeId"] == report["preview"]["node_id"]
    assert (
        source["metadata"]["directorCoverStorageKey"]
        == "resource:" + report["cover"]["resource_id"]
    )
    assert source["updatedAt"] >= report["director_node_version"]
    if video_output:
        video = next(item for item in final["nodes"] if item["id"] == report["video"]["node_id"])
        assert source["metadata"]["directorClayVideoNodeId"] == video["id"]
        assert set(source["metadata"]["referenceAssetNodeIds"]) == {
            report["preview"]["node_id"],
            video["id"],
        }
        assert video["metadata"]["hasAudio"] is False
        assert video["metadata"]["videoPreview"]["sourceKey"] == video["metadata"]["storageKey"]
        assert [item["label"] for item in report["video_playback"]] == [
            "produced",
            "restored",
            "member",
        ]
    shared = member.get(path + "/my-document").json()["source_document"]
    assert shared["directorScenes"][0]["objects"] == scene["objects"]
    assert (
        shared["directorScenes"][0]["shots"][0]["screenshots"] == scene["shots"][0]["screenshots"]
    )
    assert "prompt" not in shared["directorScenes"][0]["shots"][0]
    assert outsider.get(path + "/my-document").status_code == 404
    with resource_app[1]() as session:
        assert session.scalar(select(AsyncTask.id)) is None
        assert len(list(session.scalars(select(MediaFile.id)))) == resource_count
        assert len(list(session.scalars(select(CanvasResourceUpload.id)))) == resource_count
        outputs = {report["preview"]["node_id"]}
        if video_output:
            outputs.add(report["video"]["node_id"])
        assert {
            (edge.from_node_key, edge.to_node_key) for edge in session.scalars(select(CanvasEdge))
        } == {(report["director_node_id"], output) for output in outputs}
        stored = session.scalar(select(CanvasNode).where(CanvasNode.node_key == source["id"]))
        assert (
            stored.content_json["metadata"]["directorPreviewNodeId"] == report["preview"]["node_id"]
        )
        fields = [
            *(["reference_screenshot"] if scene_kind == "render_views" else []),
            "screenshot",
            "preview",
            "cover",
            *(["poster", "video"] if video_output else []),
        ]
        for field in fields:
            item = report[field]
            media = session.get(MediaFile, int(item["resource_id"]))
            assert media.created_by == int(owner_user["id"])
            assert media.project_id == int(project["id"])
            location = ObjectLocation.parse(
                media.storage_locator, {settings.minio_image_bucket, settings.minio_video_bucket}
            )
            assert location.bucket == (
                settings.minio_video_bucket if field == "video" else settings.minio_image_bucket
            )
            with resource_app[0].state.storage.open(
                location.bucket, location.object_name
            ) as response:
                data = response.read()
            assert media.byte_size == len(data)
            assert media.checksum_sha256 == hashlib.sha256(data).hexdigest() == item["checksum"]
            assert owner.get(f"{ROOT}/{media.id}/file").content == data
            assert member.get(f"{ROOT}/{media.id}/file").content == data
            assert outsider.get(f"{ROOT}/{media.id}/file").status_code == 404
            if field == "video":
                assert media.format_code == item["mime_type"]
                artifact = tmp_path / "browser" / item["filename"]
                assert artifact.read_bytes() == data
                probe = CanvasTimelineRenderer(settings).probe(artifact)
                assert probe.facts.has_video and not probe.facts.has_audio
                duration_ms = scene["shots"][0]["duration"] * 1000
                assert duration_ms - 500 <= probe.facts.duration_ms <= duration_ms + 1500
                assert abs(probe.facts.duration_ms - item["duration_ms"]) < 100
                assert (probe.width, probe.height) == (media.width, media.height)
                assert (probe.width, probe.height) == (item["width"], item["height"])
                frame_bytes = ffmpeg(
                    "-ss",
                    "1",
                    "-i",
                    artifact,
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                )
                (tmp_path / "browser" / "director-clay-frame.png").write_bytes(frame_bytes)
                if scene_kind in {"actor", "multitrack"}:
                    later = ffmpeg(
                        "-ss",
                        "7",
                        "-i",
                        artifact,
                        "-frames:v",
                        "1",
                        "-f",
                        "image2pipe",
                        "-vcodec",
                        "png",
                        "-",
                    )
                    first = Image.open(BytesIO(frame_bytes)).convert("RGB")
                    last = Image.open(BytesIO(later)).convert("RGB")
                    assert first.size == last.size
                    changed = sum(
                        sum(abs(a - b) for a, b in zip(left, right, strict=True)) > 30
                        for left, right in zip(first.getdata(), last.getdata(), strict=True)
                    )
                    assert changed > first.width * first.height * 0.005, changed
                    (tmp_path / "browser" / "director-clay-later.png").write_bytes(later)
                image_bytes = frame_bytes
            else:
                assert media.format_code == ("image/jpeg" if field == "poster" else "image/png")
                image_bytes = data
            with Image.open(BytesIO(image_bytes)) as image:
                assert image.size == (media.width, media.height)
                assert image.width > 100 and image.height > 100
                # 源裁切按像素取整，H.264录制再取偶数；封面保持实际视频比例。
                pixel_tolerance = 2 if field in ("video", "poster") else 1
                assert abs(image.width * 9 / 16 - image.height) <= pixel_tolerance
                if field == "poster":
                    assert image.format == "JPEG"
                    assert (
                        abs(
                            image.width * report["video"]["height"]
                            - image.height * report["video"]["width"]
                        )
                        <= report["video"]["width"]
                    )
                assert len(set(image.convert("RGB").resize((80, 45)).getdata())) > 20
            if field in ("screenshot", "reference_screenshot"):
                assert (media.width, media.height) == (item["width"], item["height"])
                asset = session.scalar(
                    select(CanvasLibraryAsset).where(
                        CanvasLibraryAsset.title == item["name"],
                        CanvasLibraryAsset.user_id == int(owner_user["id"]),
                    )
                )
            elif field in ("preview", "video"):
                asset = session.scalar(
                    select(CanvasLibraryAsset).where(
                        CanvasLibraryAsset.source_key == item["asset_id"]
                    )
                )
            else:
                continue
            assert asset and asset.user_id == int(owner_user["id"])
            assert session.scalar(
                select(CanvasLibraryAssetReference.id).where(
                    CanvasLibraryAssetReference.library_asset_id == asset.id,
                    CanvasLibraryAssetReference.media_id == media.id,
                )
            )
