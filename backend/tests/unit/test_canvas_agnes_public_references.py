"""冻结引用、内存签名与一次 POST 的离线合同；供应商及签名器均为替身。"""

import base64
import json
import socket
import time
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import quote

import pytest

from short_drama.ai import GenerationError
from short_drama.ai.adapters import build_submission, validate_request
from short_drama.ai.canvas_credentials import CanvasCredentials
from short_drama.ai.gateway import GenerationGateway
from short_drama.core.config import Settings
from short_drama.core.exceptions import NotFound, StorageUnavailable, WorkflowError
from short_drama.service.canvas_video_admission import freeze_video_references

ADAPTER = "canvas_openai_videos.v1"
SIGNED_QUERY = "X-Amz-Signature=offline-signature"


def snapshot():
    return {
        "base_url": "https://supplier.test/v1",
        "service_type": "video",
        "model_key": "agnes-video-2.5-flash",
        "budget_seconds": 30,
        "canvas_auth_version": 1,
        "canvas_auth_scene": "canvas_node",
    }


def payload(*, mixed=False):
    images = [{"id": "first", "storageKey": "resource:17", "type": "image/png", "bytes": 12}]
    if mixed:
        images.insert(0, {"id": "last", "url": "https://media.test/last.png"})
    return {
        "source": {"scene": "canvas_node", "project_id": "9"},
        "input": {"prompt": "离线首帧", "reference_media_ids": ["17"]},
        "canvas_parameters": {"mode": "video"},
        "canvas_request": {
            "operation": "image_to_video",
            "input": {
                "mode": "video",
                "prompt": "离线首帧",
                "config": {"videoSeconds": "6", "size": "16:9", "vquality": "720p"},
                "referenceImages": images,
                "referenceVideos": [],
                "referenceAudios": [],
                "metadata": {
                    "videoStartFrameNodeId": "first",
                    **({"videoEndFrameNodeId": "last"} if mixed else {}),
                },
            },
        },
    }


def public_settings():
    return Settings(
        _env_file=None,
        minio_endpoint="localhost:9000",
        minio_access_key="offline-access",
        minio_secret_key="offline-secret",
        minio_public_endpoint="media.test",
        minio_provider_presign_expiry=1800,
    )


def test_pure_validation_accepts_frozen_identity_without_any_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("pure validation performed I/O")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr("short_drama.storage.minio.MinioStorage.stat", forbidden)
    monkeypatch.setattr("short_drama.storage.minio.MinioStorage.provider_presigned_get", forbidden)
    monkeypatch.setattr("short_drama.ai.transport.SafeTransport.request", forbidden)
    request = payload(mixed=True)
    before = deepcopy(request)
    result = validate_request(snapshot(), request, ADAPTER)
    assert result["resolved_parameters"] == {"seconds": "6", "size": "720P", "aspect_ratio": "16:9"}
    assert request == before and SIGNED_QUERY not in json.dumps(result)
    with pytest.raises(GenerationError, match="reference_media_requires_url"):
        build_submission(snapshot(), request, ADAPTER)


@pytest.mark.parametrize("change", ["ids", "missing-id", "role", "duration", "video", "data"])
def test_bad_frozen_structure_fails_before_resolver_or_post(change):
    request = payload()
    inputs = request["canvas_request"]["input"]
    if change == "ids":
        request["input"]["reference_media_ids"] = ["19"]
    elif change == "missing-id":
        request["input"]["reference_media_ids"] = []
    elif change == "role":
        inputs["metadata"]["videoStartFrameNodeId"] = "absent"
    elif change == "duration":
        inputs["config"]["videoSeconds"] = "13"
    elif change == "video":
        inputs["referenceVideos"] = [{"url": "https://media.test/v.mp4"}]
    else:
        inputs["referenceImages"][0]["dataUrl"] = "data:image/png;base64,eA=="
    resolver, transport = Mock(), Mock()
    gateway = GenerationGateway(Settings(_env_file=None))
    gateway.transport = transport
    with pytest.raises(GenerationError):
        gateway.submit(
            snapshot(),
            request,
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            canvas_reference_url_resolver=resolver,
        )
    resolver.assert_not_called()
    transport.request.assert_not_called()


def captured_gateway(reply=None):
    gateway = GenerationGateway(Settings(_env_file=None))
    posts = []

    def request(method, url, **options):
        posts.append((method, deepcopy(options["body"])))
        body = reply if reply is not None else {"video_id": "offline-video", "status": "pending"}
        return 200, {"Content-Type": "application/json"}, json.dumps(body).encode()

    gateway.transport.request = request
    return gateway, posts


@pytest.mark.parametrize("mixed", [False, True])
def test_resolved_urls_only_enter_submit_copy_and_follow_original_indices(mixed):
    gateway, posts = captured_gateway()
    resolver = Mock(return_value="https://media.test/first.png?" + SIGNED_QUERY)
    request = payload(mixed=mixed)
    before = deepcopy(request)
    result = gateway.submit(
        snapshot(),
        request,
        CanvasCredentials(apiKey="offline-key"),
        ADAPTER,
        canvas_reference_url_resolver=resolver,
        agnes_reference_guard_writer=Mock(),
    )
    assert len(posts) == 1 and posts[0][0] == "POST"
    wire = posts[0][1]
    assert wire["mode"] == "keyframe"
    assert wire["first_frame"].endswith(SIGNED_QUERY)
    if mixed:
        assert wire["last_frame"] == "https://media.test/last.png"
    assert resolver.call_args.args[:2] == ("image", 1 if mixed else 0)
    assert request == before
    assert SIGNED_QUERY not in json.dumps(result.resolved_parameters)
    assert "storageKey" not in json.dumps(wire) and "reference.invalid" not in json.dumps(wire)


@pytest.mark.parametrize("failure", ["missing", "invalid", "http", "unresolved"])
def test_signing_or_strict_wire_failure_leaves_zero_post_and_sanitized_error(failure):
    gateway, posts = captured_gateway()
    if failure == "missing":
        resolver = Mock(side_effect=GenerationError("reference_missing"))
    else:
        resolver = Mock(
            return_value={
                "invalid": "https://reference.invalid/a?" + SIGNED_QUERY,
                "http": "http://media.test/a?" + SIGNED_QUERY,
                "unresolved": "resource:17",
            }[failure]
        )
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(),
            payload(),
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            canvas_reference_url_resolver=resolver,
        )
    assert posts == [] and SIGNED_QUERY not in str(error.value)


def test_unknown_submission_signs_once_posts_once_and_poll_never_resigns():
    gateway, posts = captured_gateway({"status": "pending"})
    resolver = Mock(return_value="https://media.test/first.png?" + SIGNED_QUERY)
    guards = []
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(),
            payload(),
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            canvas_reference_url_resolver=resolver,
            agnes_reference_guard_writer=guards.append,
        )
    assert error.value.accepted_unknown and len(posts) == resolver.call_count == 1
    result = gateway.poll(
        snapshot(),
        "offline-existing",
        CanvasCredentials(apiKey="offline-key"),
        ADAPTER,
        agnes_reference_guard=guards[0],
        require_agnes_reference_guard=True,
    )
    assert result.status == "submitted"
    assert [method for method, _ in posts] == ["POST", "GET"]
    assert resolver.call_count == 1


def admitted_generation(monkeypatch, *, enabled=True, scope=(None, 9)):
    monkeypatch.setattr("short_drama.service.canvas_video_admission.scope_of", lambda *args: scope)
    media = SimpleNamespace(
        storage_locator="minio://image/reference.png",
        format_code="image/png",
        byte_size=12,
        width=37,
        height=19,
        duration_ms=None,
    )
    return SimpleNamespace(
        settings=public_settings() if enabled else Settings(_env_file=None),
        session=object(),
        _validate_media=Mock(return_value=media),
    )


def test_no_public_configuration_retains_422_before_media_or_task_admission(monkeypatch):
    generation = admitted_generation(monkeypatch, enabled=False)
    with pytest.raises(WorkflowError) as error:
        freeze_video_references(generation, payload(), 9, ADAPTER, model=snapshot()["model_key"])
    assert error.value.code == "reference_media_requires_url" and error.value.status_code == 422
    generation._validate_media.assert_not_called()


@pytest.mark.parametrize("scope", [(None, 10), (3, None)])
def test_enabled_public_admission_keeps_actual_project_scope(monkeypatch, scope):
    with pytest.raises(NotFound):
        freeze_video_references(
            admitted_generation(monkeypatch, scope=scope),
            payload(),
            9,
            ADAPTER,
            model=snapshot()["model_key"],
        )


def test_enabled_admission_freezes_real_metadata_and_removes_client_url(monkeypatch):
    request = payload()
    request["canvas_request"]["input"]["referenceImages"][0]["url"] = "https://fake.test/?secret=1"
    freeze_video_references(
        admitted_generation(monkeypatch), request, 9, ADAPTER, model=snapshot()["model_key"]
    )
    reference = request["canvas_request"]["input"]["referenceImages"][0]
    assert "url" not in reference and reference["width"] == 37
    assert request["input"]["reference_media_ids"] == ["17"]


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "scope",
        "mime",
        "metadata",
        "missing",
        "size",
        "locator",
        "storage",
        "database",
        "expired",
        "index",
    ],
)
def test_real_media_is_rechecked_before_signer_with_original_index(failure):
    from short_drama.service.canvas_video_references import StoredCanvasVideoReferenceURLs

    media = SimpleNamespace(
        scope_user_id=None,
        project_id=9,
        storage_locator="minio://image/reference.png",
        format_code="image/png",
        byte_size=12,
        width=None,
        height=None,
        duration_ms=None,
    )
    if failure == "scope":
        media.scope_user_id, media.project_id = 7, None
    elif failure == "mime":
        media.format_code = "audio/mpeg"
    elif failure == "metadata":
        media.byte_size = 13
    elif failure == "locator":
        media.storage_locator = "https://fake.test/reference"

    @contextmanager
    def factory():
        if failure == "database":
            from sqlalchemy.exc import SQLAlchemyError

            raise SQLAlchemyError("private storage context")
        yield SimpleNamespace(get=lambda *args: None if failure == "missing" else media)

    storage = Mock()
    storage.stat.return_value = SimpleNamespace(
        size=100 if failure == "size" else 12, content_type="image/png"
    )
    if failure == "storage":
        storage.stat.side_effect = StorageUnavailable(SIGNED_QUERY)
    storage.provider_presigned_get.return_value = "https://media.test/reference.png?" + SIGNED_QUERY
    resolver = StoredCanvasVideoReferenceURLs(
        factory, storage, public_settings(), payload(mixed=True), 9
    )
    if failure:
        with pytest.raises(GenerationError) as error:
            resolver(
                "image",
                0 if failure == "index" else 1,
                64,
                time.monotonic() + (-1 if failure == "expired" else 30),
            )
        assert SIGNED_QUERY not in str(error.value)
        storage.provider_presigned_get.assert_not_called()
    else:
        assert resolver("image", 1, 64, time.monotonic() + 30).endswith(SIGNED_QUERY)
        storage.provider_presigned_get.assert_called_once_with("image", "reference.png")


@pytest.mark.parametrize(
    "permission",
    ["owner", "member", "inactive", "archived", "revoked", "cancelled", "disabled", "unknown"],
)
def test_worker_uses_real_submit_policy_before_resolver_and_only_freezes_scalars(
    monkeypatch, permission
):
    from short_drama.domain import AIGenerationRecord, AIModelConfig, MediaFile, Project
    from short_drama.domain.collaboration import ProjectMember, User
    from short_drama.service.base import utcnow
    from short_drama.service.generation_execution_service import GenerationExecutionService

    task = SimpleNamespace(
        id=1,
        next_action="submit",
        service_type="video",
        project_id=9,
        scope_user_id=None,
        initiated_by=7,
        started_at=utcnow(),
        cancel_requested=permission == "cancelled",
        message_version=1,
    )
    record = SimpleNamespace(
        id=2,
        task_id=1,
        config_id=3,
        adapter=ADAPTER,
        config_snapshot=snapshot(),
        request_data=payload(),
        status="prepared",
        response_data=None,
        provider_task_id=None,
        text_content=None,
    )
    user = SimpleNamespace(
        id=7,
        status="inactive" if permission == "inactive" else "active",
        email_verified_at=utcnow(),
    )
    project = SimpleNamespace(
        id=9,
        owner_user_id=8 if permission in {"member", "revoked"} else 7,
        archived_at=utcnow() if permission == "archived" else None,
    )
    config = SimpleNamespace(enabled=permission != "disabled", is_deleted=False)
    media = SimpleNamespace(
        scope_user_id=None,
        project_id=9,
        storage_locator="minio://image/reference.png",
        format_code="image/png",
        byte_size=12,
        width=None,
        height=None,
        duration_ms=None,
    )

    class Session:
        info = {}

        def scalar(self, statement):
            entity = statement.column_descriptions[0]["entity"]
            return {
                User: user,
                Project: project,
                AIModelConfig: config,
                ProjectMember: 17 if permission == "member" else None,
            }[entity]

        def get(self, model, identifier):
            return {AIGenerationRecord: record, MediaFile: media}[model]

    class Factory:
        @contextmanager
        def __call__(self):
            yield Session()

        begin = __call__

    monkeypatch.setattr(
        "short_drama.service.generation_execution_service.owned_task", lambda *args: task
    )
    monkeypatch.setattr("short_drama.service.task_access.lock_resource_project", lambda *args: None)
    storage = Mock()
    storage.stat.return_value = SimpleNamespace(size=12, content_type="image/png")
    storage.provider_presigned_get.return_value = "https://media.test/reference.png?" + SIGNED_QUERY
    gateway, posts = captured_gateway({"status": "pending"} if permission == "unknown" else None)
    executor = GenerationExecutionService(Factory(), public_settings(), gateway, storage)
    executor._credential = lambda current: CanvasCredentials(apiKey="offline-key")
    results = []
    executor._store_result = lambda *args: results.append(args[-1])
    if permission == "unknown":
        with pytest.raises(GenerationError) as error:
            executor._generate(task, record, 1, "offline-lease")
        assert error.value.accepted_unknown and SIGNED_QUERY not in str(error.value)
        executor._generate(task, record, 1, "offline-recovery-lease")
        assert len(posts) == storage.provider_presigned_get.call_count == 1
        assert results == [] and task.error["code"] == "provider_acceptance_unknown"
    elif permission in {"owner", "member"}:
        executor._generate(task, record, 1, "offline-lease")
        assert len(posts) == len(results) == storage.provider_presigned_get.call_count == 1
        assert results[0].status == "submitted"
        assert posts[0][1]["first_frame"].endswith(SIGNED_QUERY)
    else:
        executor._generate(task, record, 1, "offline-lease")
        assert posts == results == []
        storage.stat.assert_not_called()
        storage.provider_presigned_get.assert_not_called()
        assert task.status == ("failed" if permission == "disabled" else "cancelled")
    assert SIGNED_QUERY not in json.dumps(record.request_data)


SIGNED_URL = (
    "https://media.test/private/first.png?X-Amz-Signature=0123456789abcdef"
    "&X-Amz-Credential=offline-access%2F20261011%2Fregion%2Fs3%2Faws4_request"
    "&X-Amz-Security-Token=offline-temporary-token"
)


@pytest.mark.parametrize(
    "output",
    [
        SIGNED_URL,
        SIGNED_URL.replace("/private/first.png", "/output/video.mp4"),
        "https://video.test/v.mp4?renamed=0123456789abcdef",
        "https://video.test/offline-temporary-token/v.mp4",
        "https://video.test/v.mp4?access=offline-access%2F20261011%2Fregion%2Fs3%2Faws4_request",
        "https://video.test/v.mp4?nested=" + quote(quote(SIGNED_URL, safe=""), safe=""),
    ],
)
def test_post_completed_echo_is_rejected_after_one_post_and_retains_safe_id(output):
    gateway, posts = captured_gateway(
        {"video_id": "safe-video", "status": "completed", "url": output}
    )
    guards = []
    with pytest.raises(GenerationError, match="reference_output_rejected") as error:
        gateway.submit(
            snapshot(),
            payload(),
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            canvas_reference_url_resolver=Mock(return_value=SIGNED_URL),
            agnes_reference_guard_writer=guards.append,
        )
    assert len(posts) == len(guards) == 1
    assert error.value.accepted_unknown and error.value.provider_task_id == "safe-video"
    assert SIGNED_URL not in json.dumps(guards) and "0123456789abcdef" not in str(error.value)


@pytest.mark.parametrize(
    "reply",
    [
        {"video_id": "0123456789abcdef", "status": "pending"},
        {"video_id": "safe-video", "status": "completed", "url": SIGNED_URL},
    ],
)
def test_poll_echo_never_resigns_and_stops_without_retry(reply):
    from short_drama.ai.canvas_agnes_adapters import agnes_reference_guard

    gateway, calls = captured_gateway(reply)
    guard = agnes_reference_guard([SIGNED_URL])
    with pytest.raises(GenerationError, match="reference_output_rejected") as error:
        gateway.poll(
            snapshot(),
            reply["video_id"],
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            agnes_reference_guard=guard,
            require_agnes_reference_guard=True,
        )
    assert not error.value.accepted_unknown and not error.value.retryable
    assert len(calls) <= 1 and all(method == "GET" for method, _ in calls)


@pytest.mark.parametrize(
    "guard", [None, {}, {"version": 1}, {"version": 1, "urls": ["x" * 64], "tokens": []}]
)
def test_missing_or_corrupt_poll_guard_leaves_zero_get(guard):
    gateway, calls = captured_gateway()
    with pytest.raises(GenerationError, match="reference_guard_unavailable"):
        gateway.poll(
            snapshot(),
            "safe-video",
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            agnes_reference_guard=guard,
            require_agnes_reference_guard=True,
        )
    assert calls == []


@pytest.mark.parametrize(
    "writer", [None, Mock(side_effect=GenerationError("reference_guard_unavailable"))]
)
def test_missing_or_failed_guard_writer_leaves_zero_post(writer):
    gateway, calls = captured_gateway()
    with pytest.raises(GenerationError, match="reference_guard_unavailable"):
        gateway.submit(
            snapshot(),
            payload(),
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            canvas_reference_url_resolver=Mock(return_value=SIGNED_URL),
            agnes_reference_guard_writer=writer,
        )
    assert calls == []


def test_independent_signed_video_output_is_accepted_and_guard_precedes_post():
    output = (
        "https://video.test/video.mp4?X-Amz-Signature=independent-signature&X-Amz-Date=20261011"
    )
    gateway, calls = captured_gateway(
        {"video_id": "safe-video", "status": "completed", "url": output}
    )
    guards = []

    def persist(guard):
        assert calls == []
        guards.append(deepcopy(guard))

    result = gateway.submit(
        snapshot(),
        payload(),
        CanvasCredentials(apiKey="offline-key"),
        ADAPTER,
        canvas_reference_url_resolver=Mock(return_value=SIGNED_URL),
        agnes_reference_guard_writer=persist,
    )
    assert result.status == "succeeded" and result.outputs[0]["url"] == output
    assert len(calls) == len(guards) == 1
    assert all(
        value not in json.dumps(guards)
        for value in (SIGNED_URL, "0123456789abcdef", "offline-temporary-token", "offline-access")
    )


@pytest.mark.parametrize("record_adapter", [None, ADAPTER])
def test_store_result_rejects_echo_before_archive_prepares_source_cipher(record_adapter):
    from short_drama.ai.canvas_agnes_adapters import agnes_reference_guard
    from short_drama.ai.types import GenerationResult
    from short_drama.service.generation_execution_service import GenerationExecutionService

    factory = Mock()
    executor = GenerationExecutionService(factory, public_settings(), Mock(), Mock())
    executor.archive = Mock()
    task = SimpleNamespace(next_action="submit", service_type="video")
    record = SimpleNamespace(
        config_snapshot=snapshot(),
        request_data=payload(),
        adapter=record_adapter,
        response_data={"agnes_reference_guard": agnes_reference_guard([SIGNED_URL])},
    )
    result = GenerationResult(
        "succeeded",
        ADAPTER,
        provider_task_id="safe-video",
        outputs=[{"url": SIGNED_URL, "media_type": "video"}],
    )
    with pytest.raises(GenerationError, match="reference_output_rejected"):
        executor._store_result(task, record, 1, "lease", result)
    executor.archive.prepare.assert_not_called()
    factory.begin.assert_not_called()


def test_reference_guard_bounds_and_does_not_scan_base64_media():
    from short_drama.ai.canvas_agnes_adapters import (
        agnes_reference_guard,
        check_agnes_reference_echo,
    )
    from short_drama.ai.types import GenerationResult

    guard = agnes_reference_guard([SIGNED_URL])
    for value in (["https://media.test/" + "a" * 8192], [SIGNED_URL] * 9):
        with pytest.raises(GenerationError, match="reference_guard_unavailable"):
            agnes_reference_guard(value)
    result = GenerationResult("succeeded", ADAPTER, outputs=[{"b64_json": "a" * 1000000}])
    with pytest.raises(GenerationError, match="reference_output_rejected"):
        check_agnes_reference_echo(result, guard, submitted=False)


def execution_fixture(monkeypatch):
    from short_drama.domain import AIGenerationRecord
    from short_drama.service.base import utcnow
    from short_drama.service.generation_execution_service import GenerationExecutionService

    task = SimpleNamespace(
        id=1,
        next_action="submit",
        message_version=1,
        service_type="video",
        started_at=utcnow(),
        cancel_requested=False,
    )
    record = SimpleNamespace(
        id=2,
        task_id=1,
        config_id=3,
        adapter=ADAPTER,
        status="sent",
        config_snapshot=snapshot(),
        request_data=payload(),
        response_data={"marker": "preserved"},
        provider_task_id=None,
    )
    call = deepcopy(record)
    state = SimpleNamespace(in_transaction=False, commits=0, commit_failure=False)

    class Session:
        def get(self, model, identifier):
            return call if model is AIGenerationRecord else None

    class Factory:
        @contextmanager
        def begin(self):
            from sqlalchemy.exc import SQLAlchemyError

            assert not state.in_transaction
            state.in_transaction = True
            try:
                yield Session()
                if state.commit_failure:
                    raise SQLAlchemyError("offline commit failure")
                state.commits += 1
            finally:
                state.in_transaction = False

    monkeypatch.setattr(
        "short_drama.service.generation_execution_service.owned_task", lambda *args: task
    )
    monkeypatch.setattr(
        "short_drama.service.canvas_text_stream.finalize_canvas_text", lambda *args: None
    )
    gateway, calls = captured_gateway()
    executor = GenerationExecutionService(Factory(), public_settings(), gateway, Mock())
    return executor, gateway, task, record, call, state, calls


def test_guard_transaction_commits_before_post_and_store_result_preserves_it(monkeypatch):
    executor, gateway, task, record, call, state, calls = execution_fixture(monkeypatch)
    transport = gateway.transport.request

    def request(*args, **kwargs):
        assert not state.in_transaction
        assert state.commits == (1 if args[0] == "POST" else 2)
        assert (
            call.response_data["agnes_reference_guard"]
            == record.response_data["agnes_reference_guard"]
        )
        return transport(*args, **kwargs)

    gateway.transport.request = request
    result = gateway.submit(
        snapshot(),
        payload(),
        CanvasCredentials(apiKey="offline-key"),
        ADAPTER,
        canvas_reference_url_resolver=Mock(return_value=SIGNED_URL),
        agnes_reference_guard_writer=lambda guard: executor._persist_agnes_reference_guard(
            task, record, 1, "lease", guard
        ),
    )
    guard = deepcopy(call.response_data["agnes_reference_guard"])
    executor._store_result(task, record, 1, "lease", result)
    assert call.status == "sent" and call.provider_task_id == "offline-video"
    assert task.next_action == "poll" and task.message_version == 2
    assert call.response_data["agnes_reference_guard"] == guard
    assert call.response_data["marker"] == "preserved" and len(calls) == 1
    task.next_action = "poll"
    record.provider_task_id = call.provider_task_id
    result = gateway.poll(
        snapshot(),
        call.provider_task_id,
        CanvasCredentials(apiKey="offline-key"),
        ADAPTER,
        agnes_reference_guard=guard,
        require_agnes_reference_guard=True,
    )
    executor._store_result(task, record, 2, "lease", result)
    assert call.response_data["agnes_reference_guard"] == guard
    assert [method for method, _ in calls] == ["POST", "GET"]


@pytest.mark.parametrize(
    "failure", ["commit", "lease", "wrong-record", "wrong-action", "duplicate"]
)
def test_guard_persistence_failure_or_lost_lease_leaves_zero_post(monkeypatch, failure):
    from short_drama.ai.canvas_agnes_adapters import agnes_reference_guard
    from short_drama.dao.task_runtime_dao import LeaseLost

    executor, gateway, task, record, call, state, calls = execution_fixture(monkeypatch)
    if failure == "commit":
        state.commit_failure = True
    elif failure == "lease":
        monkeypatch.setattr(
            "short_drama.service.generation_execution_service.owned_task",
            Mock(side_effect=LeaseLost()),
        )
    elif failure == "wrong-record":
        call.task_id = 8
    elif failure == "wrong-action":
        task.next_action = "poll"
    else:
        call.response_data["agnes_reference_guard"] = agnes_reference_guard([SIGNED_URL])
    with pytest.raises((LeaseLost, GenerationError)):
        gateway.submit(
            snapshot(),
            payload(),
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            canvas_reference_url_resolver=Mock(return_value=SIGNED_URL),
            agnes_reference_guard_writer=lambda guard: executor._persist_agnes_reference_guard(
                task, record, 1, "lease", guard
            ),
        )
    assert calls == [] and not state.in_transaction
    assert "agnes_reference_guard" not in record.response_data


def test_echo_error_preserves_safe_provider_id_and_guard_without_retry(monkeypatch):
    from short_drama.ai.canvas_agnes_adapters import agnes_reference_guard
    from short_drama.tasks.state import recovery_action

    executor, gateway, task, record, call, _, calls = execution_fixture(monkeypatch)
    guard = agnes_reference_guard([SIGNED_URL])
    call.response_data["agnes_reference_guard"] = guard
    record.response_data = deepcopy(call.response_data)
    result = {"video_id": "safe-video", "status": "completed", "url": SIGNED_URL}
    gateway.transport.request = Mock(return_value=(200, {}, json.dumps(result).encode()))
    with pytest.raises(GenerationError) as error:
        gateway.submit(
            snapshot(),
            payload(),
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            canvas_reference_url_resolver=Mock(return_value=SIGNED_URL),
            agnes_reference_guard_writer=Mock(),
        )
    executor._call_error(task, record, 1, "lease", error.value)
    assert call.status == "unknown" and call.provider_task_id == "safe-video"
    assert call.response_data["agnes_reference_guard"] == guard
    assert "media_manifest" not in call.response_data and task.status == "failed"
    assert recovery_action(call) == "poll" and calls == []
    assert gateway.transport.request.call_count == 1
    assert gateway.transport.request.call_args.args[0] == "POST"
    task.next_action = "poll"
    with pytest.raises(GenerationError) as error:
        gateway.poll(
            snapshot(),
            call.provider_task_id,
            CanvasCredentials(apiKey="offline-key"),
            ADAPTER,
            agnes_reference_guard=guard,
            require_agnes_reference_guard=True,
        )
    executor._call_error(task, record, 2, "lease", error.value)
    assert call.provider_task_id == "safe-video" and task.next_action is None
    assert call.response_data["agnes_reference_guard"] == guard
    assert SIGNED_URL not in json.dumps(call.response_data) and calls == []
    assert gateway.transport.request.call_count == 2
    assert gateway.transport.request.call_args.args[0] == "GET"


@pytest.mark.parametrize("guard", [None, {}, {"version": 1}])
def test_worker_poll_missing_or_corrupt_guard_never_calls_gateway_or_storage(monkeypatch, guard):
    executor, gateway, task, record, call, _, calls = execution_fixture(monkeypatch)
    task.next_action = "poll"
    call.response_data = record.response_data = {"agnes_reference_guard": guard}
    executor._credential = lambda current: CanvasCredentials(apiKey="offline-key")
    gateway.poll = Mock()
    with pytest.raises(GenerationError, match="reference_guard_unavailable"):
        executor._generate(task, record, 1, "lease")
    gateway.poll.assert_not_called()
    executor.storage.provider_presigned_get.assert_not_called()
    assert calls == []


def test_independent_video_archives_its_own_url_and_save_does_not_submit_or_sign(monkeypatch):
    from short_drama.ai.canvas_agnes_adapters import agnes_reference_guard
    from short_drama.ai.types import GenerationResult

    executor, gateway, task, record, call, _, _ = execution_fixture(monkeypatch)
    executor.archive.settings = Settings(
        _env_file=None,
        encryption_key=base64.b64encode(b"offline-archive-key".ljust(32, b"0")).decode(),
    )
    monkeypatch.setattr("short_drama.service.generation_archive.next_id", lambda: 99)
    guard = agnes_reference_guard([SIGNED_URL])
    call.response_data["agnes_reference_guard"] = guard
    record.response_data = deepcopy(call.response_data)
    output = "https://video.test/independent.mp4?X-Amz-Signature=independent-signature"
    result = GenerationResult(
        "succeeded",
        ADAPTER,
        provider_task_id="safe-video",
        outputs=[{"url": output, "media_type": "video"}],
    )
    executor._store_result(task, record, 1, "lease", result)
    manifest = call.response_data["media_manifest"]
    assert executor.archive._cipher().decrypt(manifest[0]["source_cipher"]) == output
    assert task.next_action == "save" and call.response_data["agnes_reference_guard"] == guard
    record.response_data = deepcopy(call.response_data)
    executor.store.heartbeat = Mock(return_value=True)
    monkeypatch.setattr(
        "short_drama.service.generation_execution_service.latest_record", lambda *args: call
    )

    def save_one(*args):
        manifest[0]["saved"] = True

    executor.archive.save_one = Mock(side_effect=save_one)
    gateway.submit, gateway.poll = Mock(), Mock()
    executor._save(task, record, 2, "lease")
    executor.archive.save_one.assert_called_once()
    gateway.submit.assert_not_called()
    gateway.poll.assert_not_called()
    executor.storage.provider_presigned_get.assert_not_called()
    assert task.status == "succeeded" and call.response_data["agnes_reference_guard"] == guard


def test_excessive_percent_encoding_is_rejected_with_bounded_decoding():
    from short_drama.ai.canvas_agnes_adapters import (
        agnes_reference_guard,
        check_agnes_reference_echo,
    )

    guard = agnes_reference_guard([SIGNED_URL])
    nested = SIGNED_URL
    for _ in range(5):
        nested = quote(nested, safe="")
    with pytest.raises(GenerationError, match="reference_output_rejected"):
        check_agnes_reference_echo(
            {"url": "https://video.test/v.mp4?nested=" + nested}, guard, submitted=False
        )
