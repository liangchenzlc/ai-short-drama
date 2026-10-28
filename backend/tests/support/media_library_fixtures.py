"""已归档的三类生成产物；同时供 SQLite 行为测试和 MySQL 权限测试使用。"""

from datetime import timedelta

from sqlalchemy.orm import Session

from short_drama.core.identity import ActorContext
from short_drama.domain import AIGenerationRecord, AsyncTask, MediaAsset, MediaFile, Project
from short_drama.domain.collaboration import User
from short_drama.service.base import utcnow
from tests.support.generation_fixtures import config


def seed_media_library(session: Session) -> ActorContext:
    config(session, identifier=100)
    now = utcnow() - timedelta(minutes=1)
    session.add(
        User(
            id=2,
            username="other_creator",
            display_name="Other",
            email="other@example.test",
            password_hash="test-only",
            status="active",
            email_verified_at=now,
            created_at=now,
        )
    )
    session.flush()
    for identifier, owner, mode in (
        (101, 1, "standard"),
        (102, 1, "infinite_canvas"),
        (103, 2, "standard"),
    ):
        session.add(
            Project(
                id=identifier,
                owner_user_id=owner,
                workspace_mode=mode,
                name=f"Work {identifier}",
                created_at=now,
                updated_at=now,
            )
        )
    session.flush()
    entries = (
        (101, "image", "雨夜人物", 0),
        (102, "video", "雨夜运动", 1),
        (101, "audio", "旁白配音", 2),
        (102, "image", "雨夜场景", 3),
        (101, "video", "列车片段", 3),
        (102, "audio", "雨夜环境声", 4),
        (None, "image", "模型测试图片", 5),
        (103, "image", "雨夜私密", 6),
    )
    for index, (project_id, kind, name, seconds) in enumerate(entries, 1):
        owner = 2 if project_id == 103 else 1
        scope = {"project_id": project_id, "scope_user_id": None if project_id else owner}
        created = now + timedelta(seconds=seconds)
        session.add_all(
            [
                AsyncTask(
                    id=1000 + index,
                    **scope,
                    initiated_by=owner,
                    service_type=kind,
                    status="succeeded",
                    idempotency_key=f"library-{index}",
                    request_hash="a" * 64,
                    created_at=created,
                    updated_at=created,
                    finished_at=created,
                ),
                MediaFile(
                    id=3000 + index,
                    **scope,
                    created_by=owner,
                    updated_by=owner,
                    format_code={"image": "image/png", "video": "video/mp4", "audio": "audio/wav"}[
                        kind
                    ],
                    storage_locator=f"minio://fixtures/library-{index}",
                    original_name="",
                    created_at=created,
                    updated_at=created,
                ),
            ]
        )
        session.flush()
        session.add(
            AIGenerationRecord(
                id=2000 + index,
                task_id=1000 + index,
                call_no=1,
                config_id=100,
                config_snapshot={},
                request_data={"input": {"prompt": "测试生成产物"}},
                status="succeeded",
                created_at=created,
                updated_at=created,
            )
        )
        session.flush()
        session.add(
            MediaAsset(
                id=4000 + index,
                record_id=2000 + index,
                media_id=3000 + index,
                output_index=1,
                media_type=kind,
                name=name,
                created_at=created,
                updated_at=created,
            )
        )
    session.commit()
    return ActorContext(1, "legacy_fixture", "legacy@example.test", True, 1, "fixture", "library")
