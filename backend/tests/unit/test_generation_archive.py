from io import BytesIO

import pytest
from PIL import Image


def test_image_metadata_comes_from_bytes_and_mismatched_media_is_rejected():
    from short_drama.service.generation_archive import inspect_media

    output = BytesIO()
    Image.new("RGB", (13, 7), "red").save(output, format="PNG")
    result = inspect_media(output.getvalue(), "image")
    assert result["mime"] == "image/png"
    assert (result["width"], result["height"]) == (13, 7)
    assert result["ext"] == "png"
    with pytest.raises(ValueError):
        inspect_media(b"<html>upstream error</html>", "image")
    with pytest.raises(ValueError):
        inspect_media(output.getvalue(), "video")


def test_different_download_bytes_cannot_overwrite_same_asset_object():
    from short_drama.core.config import Settings
    from short_drama.core.exceptions import NotFound
    from short_drama.service.generation_archive import GenerationArchive

    class Storage:
        objects = {}

        def stat(self, bucket, key):
            if key in self.objects:
                raise AssertionError("A competing archive write reused the same object key")
            raise NotFound()

        def put(self, bucket, key, data, length, content_type):
            self.objects[key] = data.read()

    storage = Storage()
    archive = GenerationArchive(None, Settings(_env_file=None), None, storage)
    entry = {"asset_id": "123", "media_type": "image"}
    for color in ("red", "blue"):
        output = BytesIO()
        Image.new("RGB", (2, 2), color).save(output, "PNG")
        archive._store(1, 2, entry, output.getvalue())
    assert len(storage.objects) == 2
