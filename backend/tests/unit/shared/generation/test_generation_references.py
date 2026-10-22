import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from short_drama.ai import GenerationError
from short_drama.core.exceptions import NotFound, StorageUnavailable
from short_drama.service.ai_generation_service import safe_error
from short_drama.service.generation_references import StoredAudioReferences, StoredImageReferences


@pytest.fixture
def references():
    state = SimpleNamespace(size=4, chunks=[b"ab", b"cd"], closed=False, error=None)

    class Storage:
        def stat(self, bucket, key):
            assert (bucket, key) == ("images", "saved.png")
            if state.error:
                raise state.error
            return SimpleNamespace(size=state.size)

        @contextmanager
        def open(self, bucket, key):
            try:
                yield SimpleNamespace(stream=lambda _: iter(state.chunks))
            finally:
                state.closed = True

    @contextmanager
    def factory():
        yield SimpleNamespace(get=lambda _, identifier: state.media if identifier == 1 else None)

    state.media = SimpleNamespace(storage_locator="minio://images/saved.png")
    settings = SimpleNamespace(
        minio_image_bucket="images", minio_video_bucket="videos", minio_audio_bucket="audio"
    )
    return factory, Storage(), settings, state


def test_saved_reference_is_read_and_stream_closed(references):
    factory, storage, settings, state = references
    loader = StoredImageReferences(factory, storage, settings, ["1"])
    assert loader(0, 4, time.monotonic() + 10) == b"abcd"
    assert state.closed


def test_audio_reference_uses_frozen_checksum_and_rejects_replaced_bytes(references):
    import hashlib

    factory, storage, settings, state = references
    loader = StoredAudioReferences(
        factory,
        storage,
        settings,
        [{"media_id": "1", "checksum": hashlib.sha256(b"abcd").hexdigest()}],
    )
    assert loader(0, 4, time.monotonic() + 10) == b"abcd"
    state.chunks = [b"abce"]
    with pytest.raises(GenerationError, match="audio_reference_changed"):
        loader(0, 4, time.monotonic() + 10)


@pytest.mark.parametrize("reported_size", [4, 1])
def test_reference_limit_checks_metadata_and_actual_stream(references, reported_size):
    factory, storage, settings, state = references
    state.size = reported_size
    loader = StoredImageReferences(factory, storage, settings, ["1"])
    with pytest.raises(GenerationError, match="reference_images_too_large") as error:
        loader(0, 3, time.monotonic() + 10)
    assert not error.value.accepted_unknown
    if reported_size == 1:
        assert state.closed


@pytest.mark.parametrize(
    "error,code",
    [(NotFound(), "reference_missing"), (StorageUnavailable(), "reference_storage_unavailable")],
)
def test_reference_storage_failure_has_safe_known_outcome(references, error, code):
    factory, storage, settings, state = references
    state.error = error
    loader = StoredImageReferences(factory, storage, settings, ["1"])
    with pytest.raises(GenerationError, match=code) as caught:
        loader(0, 4, time.monotonic() + 10)
    assert not caught.value.accepted_unknown


def test_missing_media_and_untrusted_locator_cannot_be_read(references):
    factory, storage, settings, state = references
    with pytest.raises(GenerationError, match="reference_missing"):
        StoredImageReferences(factory, storage, settings, ["2"])
    state.media.storage_locator = "http://127.0.0.1/private"
    loader = StoredImageReferences(factory, storage, settings, ["1"])
    with pytest.raises(GenerationError, match="reference_storage_unavailable"):
        loader(0, 4, time.monotonic() + 10)


def test_reference_budget_expires_before_and_during_read(references, monkeypatch):
    factory, storage, settings, state = references
    loader = StoredImageReferences(factory, storage, settings, ["1"])
    with pytest.raises(GenerationError, match="timeout"):
        loader(0, 4, time.monotonic() - 1)
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(
        "short_drama.service.generation_references.time.monotonic", lambda: next(ticks)
    )
    with pytest.raises(GenerationError, match="timeout"):
        loader(0, 4, 1)
    assert state.closed


def test_reference_error_message_does_not_expose_internal_address():
    result = safe_error({"code": "unsafe_address", "message": "http://private/secret"})
    assert "安全检查" in result["message"]
    assert "private" not in result["message"]
