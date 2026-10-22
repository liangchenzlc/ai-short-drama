"""Capture urllib3's real wire framing in memory, with no sockets or provider calls."""

from email.parser import BytesHeaderParser
from types import SimpleNamespace

import pytest
from urllib3.connection import HTTPConnection

from short_drama.ai import transport


@pytest.fixture
def wire_capture(monkeypatch):
    calls = []
    monkeypatch.setattr(transport.time, "monotonic", lambda: 100)
    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("8.8.8.8", 443))],
    )

    class Response:
        status = 403
        headers = {"Content-Type": "application/json"}

        def read1(self, *_args, **_kwargs):
            raise AssertionError("Rejected provider response bodies must remain unread")

        def close(self):
            pass

        release_conn = close

    class Pool:
        def __init__(self, **options):
            assert options["host"] == "8.8.8.8"
            assert options["server_hostname"] == options["assert_hostname"] == "provider.example"
            assert options["cert_reqs"] == "CERT_REQUIRED"

        def request(self, method, path, **options):
            assert options["retries"] is False and options["redirect"] is False
            chunks = []
            connection = HTTPConnection("offline.invalid")
            # The real urllib3 request serializes headers/body; replacing send
            # prevents connection creation, DNS resolution and network traffic.
            connection.send = lambda data: chunks.append(bytes(data))
            connection.request(method, path, headers=options["headers"], body=options["body"])
            header_block, separator, payload = b"".join(chunks).partition(b"\r\n\r\n")
            assert separator
            headers = BytesHeaderParser().parsebytes(header_block.split(b"\r\n", 1)[1])
            calls.append((headers, payload))
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(transport.urllib3, "HTTPSConnectionPool", Pool)
    return calls


@pytest.mark.parametrize("multipart", [False, True])
@pytest.mark.parametrize("mixed_case", [False, True])
def test_sdk_header_overrides_are_unique_on_wire_and_keep_protected_fields(
    wire_capture, multipart, mixed_case
):
    incoming = {
        "aCcEpT" if mixed_case else "accept": "application/json",
        "aCcEpT-EnCoDiNg" if mixed_case else "accept-encoding": "identity",
        "uSeR-AgEnT" if mixed_case else "user-agent": "pydantic-ai/offline-test",
        "hOsT" if mixed_case else "host": "untrusted.example",
        "cOnTeNt-TyPe" if mixed_case else "content-type": "untrusted/type",
        "Authorization": "Bearer superseded-fixture",
        "authorization": "Bearer active-fixture",
    }
    original = dict(incoming)
    body = (
        transport.MultipartBody([("image[]", ("ref.png", b"image-fixture", "image/png"))])
        if multipart
        else {"prompt": "offline-fixture"}
    )
    result = transport.SafeTransport(SimpleNamespace()).request(
        "POST",
        "https://provider.example/v1/images/edits",
        headers=incoming,
        body=body,
        max_bytes=1024,
        deadline=160,
    )

    assert result == (403, {"Content-Type": "application/json"}, b"")
    assert incoming == original and len(wire_capture) == 1
    headers, payload = wire_capture[0]
    for name in (
        "Accept",
        "Accept-Encoding",
        "User-Agent",
        "Host",
        "Content-Type",
        "Authorization",
        "Content-Length",
    ):
        assert len(headers.get_all(name, [])) == 1
    assert headers["User-Agent"] == "pydantic-ai/offline-test"
    assert headers["Authorization"] == "Bearer active-fixture"
    assert headers["Host"] == "provider.example"
    assert headers["Accept"] == "application/json" and headers["Accept-Encoding"] == "identity"
    assert int(headers["Content-Length"]) == len(payload)
    if multipart:
        assert headers.get_content_type() == "multipart/form-data"
        boundary = headers.get_boundary().encode()
        assert payload.startswith(b"--" + boundary + b"\r\n")
        assert payload.endswith(b"--" + boundary + b"--\r\n")
        assert b"image-fixture" in payload
    else:
        assert headers["Content-Type"] == "application/json"
        assert payload == b'{"prompt": "offline-fixture"}'


@pytest.mark.parametrize("body", [None, {"prompt": "offline-fixture"}])
def test_native_defaults_keep_one_identity_header_and_no_credential(wire_capture, body):
    transport.SafeTransport(SimpleNamespace()).request(
        "GET" if body is None else "POST",
        "https://provider.example/v1/resource",
        body=body,
        max_bytes=1024,
        deadline=160,
    )

    assert len(wire_capture) == 1
    headers, _payload = wire_capture[0]
    assert headers.get_all("User-Agent") == ["ShortDrama-Generation/1.0"]
    assert headers.get_all("Accept-Encoding") == ["identity"]
    assert headers.get_all("Accept") == ["*/*" if body is None else "application/json"]
    assert headers.get_all("Host") == ["provider.example"]
    assert headers.get_all("Authorization") is None
    assert headers.get_all("Content-Type") == (None if body is None else ["application/json"])
