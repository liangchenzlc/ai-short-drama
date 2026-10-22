"""离线切换保留账号及个人画风闭包，不泄漏另一账号的项目媒体。"""

import json
from datetime import datetime
from decimal import Decimal

import pytest

from scripts.creation_mode_reset import decode, fingerprint, retained, serialized


def dataset(**tables):
    return {name: {"rows": values} for name, values in tables.items()}


def test_personal_style_project_media_is_copied_separately_for_each_user():
    profile = {"prompt": "41", "assets": [{"referenceResourceIds": ["41"]}]}
    data = dataset(
        users=[{"id": 1}, {"id": 2}],
        ai_model_configs=[{"id": 71, "owner_user_id": 1, "key_cipher": "unchanged"}],
        canvas_style_profiles=[
            {"id": 11, "scope_user_id": 1, "project_id": None, "profile_json": profile},
            {"id": 12, "scope_user_id": 2, "project_id": None, "profile_json": profile},
            {"id": 13, "scope_user_id": None, "project_id": 3, "profile_json": profile},
        ],
        canvas_style_media_references=[
            {"id": 21, "style_id": 11, "media_id": 41},
            {"id": 22, "style_id": 12, "media_id": 41},
        ],
        media_files=[
            {
                "id": 41,
                "project_id": 3,
                "scope_user_id": None,
                "storage_locator": "minio://image/original",
                "video_metadata": None,
            }
        ],
        projects=[{"id": 3}],
    )
    calls = []

    def copy(uid, locator):
        calls.append((uid, locator))
        return f"minio://image/user-{uid}"

    result = retained(data, copy)
    assert "projects" not in result
    assert result["users"] == data["users"]["rows"]
    assert result["ai_model_configs"] == data["ai_model_configs"]["rows"]
    assert calls == [(1, "minio://image/original"), (2, "minio://image/original")]
    media = result["media_files"]
    assert [(m["scope_user_id"], m["project_id"]) for m in media] == [(1, None), (2, None)]
    assert media[0]["id"] != media[1]["id"]
    for saved, image in zip(result["canvas_style_profiles"], media, strict=True):
        document = json.loads(saved["profile_json"])
        assert document["prompt"] == "41"
        assert document["assets"][0]["referenceResourceIds"] == [str(image["id"])]
    assert [r["media_id"] for r in result["canvas_style_media_references"]] == [
        m["id"] for m in media
    ]


def test_private_style_media_stays_private_and_invitation_mail_is_discarded():
    data = dataset(
        canvas_style_profiles=[
            {"id": 11, "scope_user_id": 1, "profile_json": {"coverUrl": "resource:41"}}
        ],
        canvas_style_media_references=[{"id": 21, "style_id": 11, "media_id": 41}],
        media_files=[{"id": 41, "scope_user_id": 1, "project_id": None}],
        email_challenges=[{"id": 1, "purpose": "registration"}, {"id": 2, "purpose": "invitation"}],
        email_outbox=[{"id": 8, "challenge_id": 1}, {"id": 9, "challenge_id": 2}],
    )

    def no_copy(*args):
        raise AssertionError("private media must retain its identity")

    result = retained(data, no_copy)
    assert result["media_files"] == data["media_files"]["rows"]
    assert [r["id"] for r in result["email_challenges"]] == [1]
    assert [r["id"] for r in result["email_outbox"]] == [8]
    data["media_files"]["rows"] = []
    with pytest.raises(RuntimeError, match="引用不存在"):
        retained(data, no_copy)


def test_backup_roundtrip_preserves_datetimes_binary_and_decimal():
    data = {
        "at": datetime(2026, 10, 21, 0, 1, 2, 123456),
        "binary": b"\x00\xff",
        "size": Decimal("1.25"),
        "json": '{"id":"123456789012345678"}',
    }
    restored = json.loads(serialized(data), object_hook=decode)
    assert restored == data
    assert fingerprint(restored) == fingerprint(data)
