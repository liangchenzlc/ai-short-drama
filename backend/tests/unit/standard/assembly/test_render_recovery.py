import json
from types import SimpleNamespace

import pytest

from short_drama.service.video_render import checksum
from short_drama.tasks.render import restore_completed_render


@pytest.mark.parametrize("damaged", ["truncated", "not-object", "mismatch", "invalid-media"])
def test_damaged_cache_does_not_hide_a_valid_completed_render(tmp_path, damaged):
    snapshot = {"version": 2, "clips": [{"media_id": "3"}]}
    broken = tmp_path / "1-attempt"
    valid = tmp_path / "2-attempt"
    directory = tmp_path / "3-attempt"
    for path in (broken, valid, directory):
        path.mkdir()
    cached = broken / "output.mp4"
    cached.write_bytes(b"broken")
    manifest = {"snapshot": snapshot, "checksum": checksum(cached)}
    if damaged == "truncated":
        text = '{"snapshot":'
    elif damaged == "not-object":
        text = "[]"
    elif damaged == "mismatch":
        text = json.dumps({**manifest, "checksum": "mismatched"})
    else:
        text = json.dumps(manifest)
    (broken / "completed.json").write_text(text, encoding="utf-8")
    (valid / "output.mp4").write_bytes(b"verified-output")
    (valid / "completed.json").write_text(
        json.dumps({"snapshot": snapshot, "checksum": checksum(valid / "output.mp4")}),
        encoding="utf-8",
    )
    info = {"duration_ms": 1000, "has_audio": True}

    def probe(path):
        if path.read_bytes() == b"broken":
            raise RuntimeError("cached video is invalid")
        return info

    output, metadata = restore_completed_render(
        SimpleNamespace(probe=probe), tmp_path, directory, [1, 2], snapshot
    )
    assert output == directory / "output.mp4"
    assert output.read_bytes() == b"verified-output"
    assert metadata == info


def test_only_invalid_cache_falls_back_to_fresh_encoding(tmp_path):
    directory = tmp_path / "1-attempt"
    directory.mkdir()
    (directory / "completed.json").write_text('{"snapshot":', encoding="utf-8")
    assert restore_completed_render(None, tmp_path, directory, [1], {}) == (None, None)
