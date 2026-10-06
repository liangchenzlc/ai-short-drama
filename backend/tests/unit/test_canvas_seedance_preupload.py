"""Seedance 预上传经过本机 HTTP；不代表真实供应商验收。"""

import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_canvas_video_transport import (
    NEWAPI,
    SEEDANCE,
    canvas_credential,
    frozen_video,
    video_request,
)
from test_generation_adapters import gateway as gateway
from test_generation_adapters import reference_image

from short_drama.ai.canvas_seedance_preupload import (
    JSON_LIMIT,
    JSON_OVERHEAD,
    inline_canvas_references,
    read_canvas_reference,
)
from short_drama.ai.types import GenerationError


@pytest.fixture
def upload_provider():
    calls = []
    state = {"uploads": [], "headers": {"X-Upload-Token": "upload-ticket"}}

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = raw if self.command == "PUT" else json.loads(raw) if raw else None
            calls.append((self.command, self.path, dict(self.headers), body))
            status, result = 200, {}
            if self.path == "/v1/video-references/uploads":
                state["uploads"].append(body)
                index = len(state["uploads"])
                status = state.get("create_status", {}).get(index, 200)
                result = {
                    "ticket": f"ticket-{index}",
                    "upload_url": state.get("upload_url", base + f"/upload/{index}"),
                    "upload_method": state.get("method", "PUT"),
                    "required_headers": state["headers"],
                }
                if state.get("create_disconnect"):
                    self.connection.close()
                    return
            elif self.command == "PUT":
                status = state.get("put_status", 200)
            elif self.path == "/v1/video-references/uploads/complete":
                status = state.get("complete_status", 200)
                reference = state["uploads"][int(body["ticket"].split("-")[-1]) - 1]
                result = {
                    **reference,
                    "url": base + "/public/reference",
                    **state.get("complete", {}),
                }
            elif self.path == "/v1/videos":
                status = state.get("paid_status", 200)
                result = state.get("paid_body", {"id": "paid-job", "status": "queued"})
                if state.get("disconnect"):
                    self.connection.close()
                    return
            elif self.path == "/v1/videos/paid-job":
                result = {"id": "paid-job", "status": "running"}
            data = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            if 300 <= status < 400:
                self.send_header("Location", base + "/redirect-target")
            self.end_headers()
            self.wfile.write(data)

        do_POST = handle_request
        do_PUT = handle_request
        do_GET = handle_request

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield base, state, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def submit_seedance(gateway, provider, *, images=None, loader=None, snapshot=None):
    base, _, _ = provider
    gateway.settings.canvas_beefapi_test_origin = base
    snapshot = snapshot or frozen_video(base, "seedance-2.5", managed=True)
    snapshot["submission_key"] = "persisted-attempt-key"
    payload = video_request(
        images=images or [{"storageKey": "resource:12", "type": "image/png"}],
        operation="image_to_video",
    )
    return gateway.submit(
        snapshot,
        payload,
        canvas_credential(),
        SEEDANCE,
        canvas_reference_loader=loader or (lambda *_args: reference_image()),
    )


def paid_calls(calls):
    return [call for call in calls if call[:2] == ("POST", "/v1/videos")]


def test_preupload_order_bytes_receipt_and_credentials(gateway, upload_provider):
    base, state, calls = upload_provider
    state["headers"].update(
        {
            "Authorization": "forbidden",
            "Cookie": "forbidden",
            "X-Api-Key": "forbidden",
            "X-Canvas-Secret": "forbidden",
            "Host": "forbidden",
            "Content-Length": "999",
            "content-type": "image/png",
        }
    )
    image = reference_image()
    result = submit_seedance(gateway, upload_provider, loader=lambda *_args: image)
    assert result.provider_task_id == "paid-job"
    assert [(call[0], call[1]) for call in calls] == [
        ("POST", "/v1/video-references/uploads"),
        ("PUT", "/upload/1"),
        ("POST", "/v1/video-references/uploads/complete"),
        ("POST", "/v1/videos"),
    ]
    assert calls[0][3] == {
        "kind": "image",
        "bytes": len(image),
        "mime": "image/png",
        "sha256": hashlib.sha256(image).hexdigest(),
    }
    put_headers = {key.lower(): value for key, value in calls[1][2].items()}
    assert calls[1][3] == image and put_headers["content-length"] == str(len(image))
    assert put_headers["x-upload-token"] == "upload-ticket"
    assert not any(
        key in put_headers
        for key in ("authorization", "cookie", "x-api-key", "x-canvas-secret", "x-private")
    )
    assert calls[0][2]["Authorization"] == calls[2][2]["Authorization"] == "Bearer private-key"
    assert all("Idempotency-Key" not in call[2] for call in calls[:-1])
    assert calls[-1][2]["Idempotency-Key"] == "persisted-attempt-key"
    assert calls[-1][3]["content"][0]["image_url"]["url"] == base + "/public/reference"
    assert "resource:" not in str(calls[-1][3])


@pytest.mark.parametrize("status", [404, 501])
def test_only_first_session_unavailable_falls_back_inline(gateway, upload_provider, status):
    _, state, calls = upload_provider
    state["create_status"] = {1: status}
    submit_seedance(gateway, upload_provider)
    assert [(call[0], call[1]) for call in calls] == [
        ("POST", "/v1/video-references/uploads"),
        ("POST", "/v1/videos"),
    ]
    assert calls[-1][3]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_second_session_unavailable_never_falls_back_or_creates(gateway, upload_provider):
    _, state, calls = upload_provider
    state["create_status"] = {2: 404}
    with pytest.raises(GenerationError, match="reference_upload_failed"):
        submit_seedance(
            gateway,
            upload_provider,
            images=[{"storageKey": "resource:12"}, {"storageKey": "resource:13"}],
        )
    assert not paid_calls(calls)
    assert len(state["uploads"]) == 2


@pytest.mark.parametrize(
    "state_change",
    [
        {"complete_status": 404},
        {"complete_status": 501},
        {"put_status": 302},
        {"put_status": 503},
        {"method": "POST"},
        {"method": None},
        {"upload_url": "http://public.example/upload"},
        {"upload_url": "https://user:password@public.example/upload"},
        {"complete": {"sha256": "incorrect"}},
        {"complete": {"bytes": 1}},
        {"complete": {"kind": "video"}},
        {"complete": {"mime": "text/html"}},
        {"create_disconnect": True},
    ],
)
def test_upload_failure_prevents_paid_creation(gateway, upload_provider, state_change):
    _, state, calls = upload_provider
    state.update(state_change)
    with pytest.raises(GenerationError) as error:
        submit_seedance(gateway, upload_provider)
    assert not error.value.accepted_unknown
    assert not paid_calls(calls)
    assert not any(call[1] == "/redirect-target" for call in calls)


def test_public_reference_and_provider_poll_never_load_or_upload(gateway, upload_provider):
    base, _, calls = upload_provider

    def forbidden(*_args):
        pytest.fail("Public references or existing provider tasks must not load local bytes")

    submit_seedance(
        gateway,
        upload_provider,
        images=[{"url": "https://public.example/frame.png"}],
        loader=forbidden,
    )
    assert len(calls) == 1 and len(paid_calls(calls)) == 1
    calls.clear()
    result = gateway.poll(
        frozen_video(base, "seedance-2.5", managed=True), "paid-job", canvas_credential(), SEEDANCE
    )
    assert result.status == "submitted"
    assert [(call[0], call[1]) for call in calls] == [("GET", "/v1/videos/paid-job")]


@pytest.mark.parametrize(
    "state_change",
    [{"paid_status": 503}, {"paid_body": {"status": "queued"}}, {"disconnect": True}],
)
def test_uncertain_paid_submission_never_repeats(gateway, upload_provider, state_change):
    _, state, calls = upload_provider
    state.update(state_change)
    with pytest.raises(GenerationError) as error:
        submit_seedance(gateway, upload_provider)
    assert error.value.accepted_unknown
    assert len(paid_calls(calls)) == 1


def test_declared_limit_and_expired_budget_stop_before_loader(gateway):
    def forbidden(*_args):
        pytest.fail("Rejected metadata or expired budget must not read media")

    deadline = time.monotonic() + 10
    with pytest.raises(GenerationError, match="reference_media_too_large"):
        read_canvas_reference(
            gateway.transport,
            "image",
            0,
            {"storageKey": "resource:12", "bytes": 30 * 1024**2 + 1},
            forbidden,
            deadline,
        )
    with pytest.raises(GenerationError, match="reference_upload_timeout"):
        read_canvas_reference(
            gateway.transport,
            "image",
            0,
            {"storageKey": "resource:12"},
            forbidden,
            time.monotonic() - 1,
        )


def test_inline_fallback_accounts_for_base64_and_overhead_before_read(gateway):
    payload = video_request(
        videos=[{"storageKey": "resource:12", "bytes": (JSON_LIMIT - JSON_OVERHEAD) * 3 // 4 + 1}]
    )

    def forbidden(*_args):
        pytest.fail("Oversized fallback must not load the reference")

    with pytest.raises(GenerationError, match="reference_inline_too_large"):
        inline_canvas_references(gateway.transport, payload, forbidden, time.monotonic() + 10)


def test_custom_channel_cannot_claim_managed_seedance(gateway, upload_provider):
    base, _, calls = upload_provider
    with pytest.raises(GenerationError, match="unsupported_protocol"):
        submit_seedance(gateway, upload_provider, snapshot=frozen_video(base, "seedance-2.5"))
    assert calls == []


def test_reference_kind_order_and_loader_original_index(gateway, upload_provider):
    base, state, calls = upload_provider
    gateway.settings.canvas_beefapi_test_origin = base
    observed = []
    image = reference_image()
    video, audio = b"\x00\x00\x00\x18ftypisom", b"ID3audio-bytes"
    payload = video_request(
        images=[
            {"url": "https://public.example/first.png"},
            {"storageKey": "resource:12", "type": "image/png"},
        ],
        videos=[{"storageKey": "resource:13", "type": "video/mp4"}],
        audios=[{"storageKey": "resource:14", "type": "audio/mp3"}],
        operation="reference_to_video",
    )

    def load(kind, index, _limit, _deadline):
        observed.append((kind, index))
        return {"image": image, "video": video, "audio": audio}[kind]

    gateway.submit(
        frozen_video(base, "seedance-2.5", managed=True),
        payload,
        canvas_credential(),
        SEEDANCE,
        canvas_reference_loader=load,
    )
    assert observed == [("image", 1), ("video", 0), ("audio", 0)]
    assert [item["kind"] for item in state["uploads"]] == ["image", "video", "audio"]
    assert [call[3] for call in calls if call[0] == "PUT"] == [image, video, audio]
    assert state["uploads"][2]["mime"] == "audio/mpeg"


def test_loader_failure_is_not_an_unknown_paid_submission(gateway, upload_provider):
    _, _, calls = upload_provider

    def fail(*_args):
        raise GenerationError("timeout", accepted_unknown=True)

    with pytest.raises(GenerationError, match="reference_load_failed") as error:
        submit_seedance(gateway, upload_provider, loader=fail)
    assert not error.value.accepted_unknown and calls == []


def test_actual_byte_limit_blocks_before_upload(gateway, upload_provider):
    _, _, calls = upload_provider
    with pytest.raises(GenerationError, match="reference_media_too_large"):
        submit_seedance(gateway, upload_provider, loader=lambda *_args: b"x" * (30 * 1024**2 + 1))
    assert calls == []


def test_wan_inline_is_only_available_for_trusted_managed_channel(gateway, upload_provider):
    base, _, calls = upload_provider
    gateway.settings.canvas_beefapi_test_origin = base
    payload = video_request(images=[{"storageKey": "resource:12"}], operation="image_to_video")
    # This fixture returns an empty body for channel 2; that is an unknown receipt,
    # but the single wire request still proves no Seedance upload was introduced.
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            frozen_video(base, "wan3.0-video", managed=True),
            payload,
            canvas_credential(),
            NEWAPI,
            canvas_reference_loader=lambda *_args: reference_image(),
        )
    assert error.value.accepted_unknown
    assert [(call[0], call[1]) for call in calls] == [("POST", "/v1/video/generations")]
    assert calls[0][3]["image_urls"][0].startswith("data:image/png;base64,")
    calls.clear()
    with pytest.raises(GenerationError, match="reference_media_requires_url"):
        gateway.submit(frozen_video(base, "wan3.0-video"), payload, canvas_credential(), NEWAPI)
    assert calls == []
