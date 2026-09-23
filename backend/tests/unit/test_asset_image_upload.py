from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image

from short_drama.core.exceptions import BusinessError, WorkflowError
from short_drama.service.asset_image_service import inspect_image_upload


def image_bytes(format_name="PNG", size=(2, 3)):
    output = BytesIO()
    Image.new("RGB", size, "red").save(output, format=format_name)
    return output.getvalue()


def test_upload_validation_derives_mime_dimensions_size_and_checksum():
    data = image_bytes("PNG", (4, 5))
    inspected = inspect_image_upload(BytesIO(data), "wrong/type")
    assert (inspected.content_type, inspected.width, inspected.height) == ("image/png", 4, 5)
    assert inspected.byte_size == len(data)
    assert len(inspected.checksum_sha256) == 64
    assert inspected.stream.read() == data


def test_upload_validation_rejects_invalid_or_oversized_input_before_storage():
    with pytest.raises(BusinessError, match="PNG, JPEG, or WebP"):
        inspect_image_upload(BytesIO(b"not an image"), "image/png")

    with pytest.raises(BusinessError, match="20 MiB"):
        inspect_image_upload(BytesIO(b"x" * (20 * 1024**2 + 1)), "image/png")


def test_upload_validation_rejects_more_than_forty_million_pixels(monkeypatch):
    class FakeImage:
        format = "PNG"
        size = (8000, 5001)

        def verify(self):
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

    monkeypatch.setattr("short_drama.service.asset_image_service.Image.open", lambda _: FakeImage())
    with pytest.raises(BusinessError, match="40 million"):
        inspect_image_upload(BytesIO(b"valid-enough-for-the-fake"), "image/png")


def test_compensation_deletes_only_the_new_uploaded_version_on_database_failure(monkeypatch):
    from short_drama.service.asset_image_service import AssetImageService

    data = image_bytes()
    stored = SimpleNamespace(
        storage_locator="minio://images/new.png",
        size=len(data),
        content_type="image/png",
        version_id="v1",
    )
    storage = SimpleNamespace(upload=lambda *_args, **_kwargs: stored)
    deleted = []
    storage.delete = lambda locator, *, version_id=None: deleted.append((locator, version_id))
    service = AssetImageService(SimpleNamespace(), SimpleNamespace(), storage)
    monkeypatch.setattr(service, "_ensure_asset_exists", lambda _asset_id: None)
    monkeypatch.setattr(service, "_storage_locator_exists", lambda _locator: False)
    monkeypatch.setattr(
        service, "_persist_upload", lambda *_args: (_ for _ in ()).throw(RuntimeError())
    )

    with pytest.raises(RuntimeError):
        service.upload(1, BytesIO(data), len(data), "image.png", "image/png")
    assert deleted == [("minio://images/new.png", "v1")]


@pytest.mark.parametrize(
    "lookup",
    [lambda _locator: True, lambda _locator: (_ for _ in ()).throw(RuntimeError())],
)
def test_uncertain_database_outcome_retains_uploaded_object(monkeypatch, lookup):
    from short_drama.service.asset_image_service import AssetImageService

    data = image_bytes()
    stored = SimpleNamespace(
        storage_locator="minio://images/uncertain.png",
        size=len(data),
        content_type="image/png",
        version_id="v2",
    )
    deleted = []
    storage = SimpleNamespace(
        upload=lambda *_args, **_kwargs: stored,
        delete=lambda locator, *, version_id=None: deleted.append((locator, version_id)),
    )
    service = AssetImageService(SimpleNamespace(), SimpleNamespace(), storage)
    monkeypatch.setattr(service, "_ensure_asset_exists", lambda _asset_id: None)
    monkeypatch.setattr(
        service, "_persist_upload", lambda *_args: (_ for _ in ()).throw(RuntimeError())
    )
    monkeypatch.setattr(service, "_storage_locator_exists", lookup)

    with pytest.raises(RuntimeError):
        service.upload(1, BytesIO(data), len(data), "image.png", "image/png")
    assert deleted == []


def test_upload_deduplicates_then_explicit_confirm_advances_asset_version():
    from generation_fixtures import generation_session

    from short_drama.service.asset_image_service import AssetImageService
    from short_drama.service.asset_library_service import AssetLibraryService
    from short_drama.service.project_service import ProjectService

    class Storage:
        def __init__(self):
            self.count = 0
            self.deleted = []

        def upload(self, _stream, *, length, content_type):
            self.count += 1
            return SimpleNamespace(
                storage_locator=f"minio://image/{self.count}.png",
                size=length,
                content_type=content_type,
                version_id=f"v{self.count}",
            )

        def delete(self, locator, *, version_id=None):
            self.deleted.append((locator, version_id))

        def download_url(self, locator):
            return f"https://signed.test/{locator.rsplit('/', 1)[-1]}"

    with generation_session() as session:
        project = ProjectService(session).create({"name": "P", "aspect": "16:9"})
        item, _ = AssetLibraryService(session).create(
            "project",
            project.id,
            project.id,
            {"kind": "prop", "name": "umbrella", "prompt": "red umbrella"},
            "upload-1",
        )
        storage = Storage()
        service = AssetImageService(session, SimpleNamespace(), storage)
        data = image_bytes()
        first, created = service.upload(item.id, BytesIO(data), len(data), "one.png", "image/png")
        assert created is True
        assert AssetLibraryService(session).get(item.id).row_version == 1
        sibling, _ = AssetLibraryService(session).create(
            "project",
            project.id,
            project.id,
            {"kind": "scene", "name": "street", "prompt": "rain"},
            "upload-2",
        )
        shared, created = service.add_candidate(sibling.id, first.media_id)
        assert created is True
        assert shared.media_id == first.media_id
        other_project = ProjectService(session).create({"name": "Q", "aspect": "16:9"})
        foreign, _ = AssetLibraryService(session).create(
            "project",
            other_project.id,
            other_project.id,
            {"kind": "scene", "name": "other", "prompt": "dry"},
            "upload-3",
        )
        with pytest.raises(WorkflowError) as error:
            service.add_candidate(foreign.id, first.media_id)
        assert error.value.code == "media_not_shareable"
        replay, created = service.upload(item.id, BytesIO(data), len(data), "two.png", "image/png")
        assert created is False
        assert replay.id == first.id
        assert storage.deleted == [("minio://image/2.png", "v2")]

        confirmed = service.confirm(
            item.id,
            {
                "row_version": "1",
                "media_id": first.media_id,
                "expected_media_id": None,
            },
        )
        assert (confirmed.media_id, confirmed.state, confirmed.row_version) == (
            first.media_id,
            "confirmed",
            2,
        )
        again = service.confirm(
            item.id,
            {
                "row_version": "2",
                "media_id": first.media_id,
                "expected_media_id": first.media_id,
            },
        )
        assert again.row_version == 2
