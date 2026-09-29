from io import BytesIO
from types import SimpleNamespace

import pytest
from generation_fixtures import config, generation_session, settings
from PIL import Image
from sqlalchemy import func, select

from short_drama.core.exceptions import WorkflowError
from short_drama.domain import AIGenerationRecord, Asset, AsyncTask, MediaAsset, MediaFile
from short_drama.service.ai_generation_service import AIGenerationService
from short_drama.service.task_reference_service import TaskReferenceService


class Storage:
    def __init__(self):
        self.uploads = []
        self.deleted = []

    def upload(self, stream, *, length, content_type):
        data = stream.read()
        assert len(data) == length
        self.uploads.append((data, content_type))
        return SimpleNamespace(storage_locator="minio://image/input.png", version_id="v1")

    def download_url(self, _locator):
        return "https://signed.example/input.png"

    def delete(self, locator, *, version_id=None):
        self.deleted.append((locator, version_id))


def png():
    stream = BytesIO()
    Image.new("RGB", (4, 5), "blue").save(stream, format="PNG")
    return stream.getvalue()


def test_local_upload_can_be_used_in_standalone_task_without_creating_asset_or_task():
    with generation_session() as session:
        model = config(session)
        model.base_url = "https://relay.example"
        model.model_key = "gpt-image-1"
        session.commit()
        storage = Storage()
        data = png()
        result = TaskReferenceService(session, settings, storage).upload(
            BytesIO(data), len(data), "本地参考.png", "wrong/type"
        )
        assert isinstance(result["media_id"], str)
        assert (result["name"], result["width"], result["height"]) == ("本地参考.png", 4, 5)
        assert result["url"] == "https://signed.example/input.png"
        with session.begin():
            media = session.get(MediaFile, int(result["media_id"]))
            assert media.format_code == "image/png"
            assert media.byte_size == len(data)
            assert len(media.checksum_sha256) == 64
            for entity in (Asset, MediaAsset, AsyncTask):
                assert session.scalar(select(func.count()).select_from(entity)) == 0
        receipt, created = AIGenerationService(session, settings).create(
            "image",
            {"input": {"prompt": "blue room", "reference_media_ids": [result["media_id"]]}},
            "local-reference-task",
        )
        assert created and receipt["status"] == "queued"
        record = session.scalar(select(AIGenerationRecord))
        assert record.request_data["input"]["reference_media_ids"] == [result["media_id"]]
        assert storage.deleted == []


@pytest.mark.parametrize(
    "data,length,code",
    [
        (b"invalid", 7, "invalid_image"),
        (b"", 20 * 1024**2 + 1, "upload_too_large"),
        (png(), 1, "invalid_image"),
    ],
)
def test_invalid_task_upload_never_reaches_storage(data, length, code):
    with generation_session() as session:
        storage = Storage()
        with pytest.raises(WorkflowError) as caught:
            TaskReferenceService(session, settings, storage).upload(
                BytesIO(data), length, "ref.png", "image/png"
            )
        assert caught.value.code == code
        assert storage.uploads == []
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 0


def test_failed_task_reference_persistence_cleans_up_uploaded_object():
    with generation_session() as session:
        storage = Storage()

        def unavailable(_locator):
            raise RuntimeError("preview unavailable")

        storage.download_url = unavailable
        data = png()
        with pytest.raises(RuntimeError):
            TaskReferenceService(session, settings, storage).upload(
                BytesIO(data), len(data), "ref.png", "image/png"
            )
        assert session.scalar(select(func.count()).select_from(MediaFile)) == 0
        assert storage.deleted == [("minio://image/input.png", "v1")]
