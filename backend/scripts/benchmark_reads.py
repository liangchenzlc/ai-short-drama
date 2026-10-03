"""Real authenticated MySQL read benchmarks in a new disposable database.

Run from backend/; only local presigning is replaced. No model, broker or object
requests occur. Use --source-root to load a frozen backend/src before imports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import os
import statistics
import sys
import time
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from benchmark_database import isolated_database
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from short_drama.core.config import Settings


class PresignBoundary:
    """Keep locator/expiry handling in StorageService; replace only network signing."""

    def __init__(self, settings: Settings) -> None:
        self.calls = 0

    def presigned_get(self, bucket: str, key: str, expiry: int) -> str:
        self.calls += 1
        return f"https://storage.example.test/{bucket}/{key}?expiry={expiry}"

    def close(self) -> None:
        pass


def seed(engine: Engine, settings: Settings) -> dict[str, int]:
    from short_drama.domain.base import Base
    from short_drama.service.auth_service import PASSWORDS
    from short_drama.service.base import utcnow

    now = utcnow()
    tables = Base.metadata.tables
    sizes = {}

    def put(name: str, rows: Iterable[dict[str, Any]]) -> None:
        rows = list(rows)
        if rows:
            with engine.begin() as connection:
                connection.execute(tables[name].insert(), rows)
        sizes[name] = len(rows)

    put(
        "users",
        [
            dict(
                id=1,
                username="perf_owner",
                display_name="Performance owner",
                email="perf@example.test",
                password_hash=PASSWORDS.hash("benchmark-only-password"),
                status="active",
                email_verified_at=now,
                created_at=now,
            )
        ],
    )
    projects = [
        dict(
            id=1000 + i,
            owner_user_id=1,
            name=f"Project {i:03}",
            aspect="16:9",
            created_at=now,
            updated_at=now,
        )
        for i in range(120)
    ]
    put("projects", projects)
    episodes = [
        dict(
            id=2000 + i,
            project_id=1000,
            position=i + 1,
            title=f"Episode {i:03}",
            aspect="16:9",
            created_at=now,
            updated_at=now,
        )
        for i in range(120)
    ]
    episodes += [
        dict(
            id=3000 + p * 4 + e,
            project_id=1000 + p,
            position=e + 1,
            title=f"Episode {p}/{e}",
            aspect="16:9",
            created_at=now,
            updated_at=now,
        )
        for p in range(1, 120)
        for e in range(4)
    ]
    put("episodes", episodes)
    configs = [
        dict(
            id=5000 + i,
            owner_user_id=1,
            service_type="image" if i else "text",
            name=f"Model {i:03}",
            model_key=f"fixture-{i}",
            provider="fixture",
            base_url="https://provider.example.test/v1",
            created_at=now,
            updated_at=now,
        )
        for i in range(120)
    ]
    put("ai_model_configs", configs)
    put(
        "media_files",
        [
            dict(
                id=6000 + i,
                project_id=1000,
                format_code="image/png",
                storage_locator=f"minio://{settings.minio_image_bucket}/perf/{i}.png",
                width=640,
                height=360,
                byte_size=1024,
                created_at=now,
                updated_at=now,
            )
            for i in range(420)
        ],
    )
    put(
        "assets",
        [
            dict(
                id=7000 + i,
                project_id=1000,
                kind=["character", "scene", "prop"][i % 3],
                name=f"Asset {i:03}",
                media_id=6000 + i,
                state="confirmed",
                description="Description " * 20,
                prompt="Image prompt " * 20,
                created_at=now,
                updated_at=now,
            )
            for i in range(120)
        ],
    )
    put(
        "project_assets",
        [dict(id=8000 + i, project_id=1000, asset_id=7000 + i, position=i + 1) for i in range(120)],
    )
    put(
        "episode_assets",
        [dict(id=9000 + i, episode_id=2000, asset_id=7000 + i, position=i + 1) for i in range(120)],
    )
    put(
        "shot_scripts",
        [
            dict(
                id=10000 + i,
                episode_id=2000,
                position=i + 1,
                script="Storyboard scene " * 20,
                image_settings={"aspect": "16:9", "resolution": "2K", "layout": "single"},
                video_settings={"duration_ms": 3000, "resolution": "720p"},
                created_at=now,
                updated_at=now,
            )
            for i in range(120)
        ],
    )
    put(
        "shot_assets",
        [
            dict(
                id=11000 + i * 3 + j,
                episode_id=2000,
                shot_id=10000 + i,
                asset_id=7000 + (i + j) % 120,
            )
            for i in range(120)
            for j in range(3)
        ],
    )
    put(
        "shot_images",
        [
            dict(
                id=12000 + i,
                episode_id=2000,
                shot_id=10000 + i,
                media_id=6000 + i,
                aspect="16:9",
                context_hash="a" * 64,
                created_at=now,
                updated_at=now,
            )
            for i in range(120)
        ],
    )
    put(
        "async_tasks",
        [
            dict(
                id=20000 + i,
                project_id=1000,
                initiated_by=1,
                service_type="image",
                status="succeeded",
                idempotency_key=f"perf-{i}",
                request_hash="a" * 64,
                finished_at=now,
                created_at=now,
                updated_at=now,
            )
            for i in range(300)
        ],
    )
    records = []
    for i in range(300):
        source = dict(
            scene="shot_image", project_id="1000", episode_id="2000", shot_id=str(10000 + i % 120)
        )
        request = dict(source=source, parameters={}, input={})
        if i % 3:
            request["display_context"] = dict(
                project_name="Project 000",
                episode_title="Episode 000",
                shot_position=i % 120 + 1,
                source_name=f"Shot {i % 120 + 1}",
            )
        records.append(
            dict(
                id=21000 + i,
                task_id=20000 + i,
                call_no=1,
                config_id=5001,
                config_snapshot=dict(name="Model 001", model_key="fixture-1", provider="fixture"),
                request_data=request,
                status="succeeded",
                finished_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    put("ai_generation_records", records)
    put(
        "media_assets",
        [
            dict(
                id=22000 + i,
                record_id=21000 + i,
                output_index=1,
                media_id=6120 + i,
                media_type="image",
                name=f"Generated image {i:03}",
                created_at=now,
                updated_at=now,
            )
            for i in range(300)
        ],
    )
    put(
        "agent_conversations",
        [
            dict(
                id=30000 + i,
                owner_user_id=1,
                project_id=1000,
                episode_id=2000,
                title=f"Conversation {i:03}",
                next_message_seq=121,
                created_at=now,
                updated_at=now,
            )
            for i in range(120)
        ],
    )
    messages = [
        dict(
            id=31000 + i,
            conversation_id=30000,
            seq=i + 1,
            role="user" if i % 2 == 0 else "assistant",
            content="Conversation message " * 30,
            created_at=now,
        )
        for i in range(120)
    ]
    messages += [
        dict(
            id=32000 + i,
            conversation_id=30000 + i,
            seq=1,
            role="user",
            content="Start message",
            created_at=now,
        )
        for i in range(1, 120)
    ]
    put("agent_messages", messages)
    put(
        "agent_runs",
        [
            dict(
                id=33000 + i,
                conversation_id=30000 + i,
                trigger_message_id=31000 if i == 0 else 32000 + i,
                initiated_by=1,
                model_config_id=5000,
                status="succeeded",
                finished_at=now,
                created_at=now,
                updated_at=now,
            )
            for i in range(120)
        ],
    )
    return sizes


def extra_seed(engine: Engine, settings: Settings) -> None:
    from short_drama.domain.base import Base
    from short_drama.service.base import utcnow

    now = utcnow()

    def put(name: str, rows: Iterable[dict[str, Any]]) -> None:
        with engine.begin() as connection:
            connection.execute(Base.metadata.tables[name].insert(), rows)

    put(
        "episode_novels",
        [
            dict(
                id=65000,
                episode_id=2000,
                content="Novel passage " * 5000,
                created_at=now,
                updated_at=now,
            )
        ],
    )
    put(
        "episode_scripts",
        [
            dict(
                id=64000,
                episode_id=2000,
                position=1,
                content="Script passage " * 4000,
                created_at=now,
                updated_at=now,
            )
        ],
    )
    with engine.begin() as connection:
        connection.execute(
            Base.metadata.tables["episodes"]
            .update()
            .where(Base.metadata.tables["episodes"].c.id == 2000)
            .values(editing_script_id=64000)
        )
    media = []
    for i in range(120):
        metadata = dict(
            duration_ms=3000,
            preview_locator=f"minio://{settings.minio_video_bucket}/preview/{i}.mp4",
            thumbnail_locator=f"minio://{settings.minio_image_bucket}/thumbnail/{i}.jpg",
            filmstrip_locator=f"minio://{settings.minio_image_bucket}/filmstrip/{i}.jpg",
            filmstrip_count=3,
            filmstrip_interval_ms=1000,
        )
        media.append(
            dict(
                id=61000 + i,
                project_id=1000,
                format_code="video/mp4",
                storage_locator=f"minio://{settings.minio_video_bucket}/video/{i}.mp4",
                duration_ms=3000,
                width=640,
                height=360,
                video_metadata=metadata,
                created_at=now,
                updated_at=now,
            )
        )
    put("media_files", media)
    put(
        "shot_videos",
        [
            dict(
                id=62000 + i,
                episode_id=2000,
                shot_id=10000 + i,
                media_id=61000 + i,
                duration=3000,
                resolution="720p",
                context_hash="b" * 64,
                created_at=now,
                updated_at=now,
            )
            for i in range(120)
        ],
    )
    put(
        "episode_assemblies",
        [dict(id=63000, episode_id=2000, aspect="16:9", created_at=now, updated_at=now)],
    )
    put(
        "episode_assembly_clips",
        [
            dict(
                id=66000 + i,
                assembly_id=63000,
                shot_id=10000 + i,
                media_id=61000 + i,
                position=i + 1,
                trim_out_ms=3000,
                source_context_hash="b" * 64,
                client_key=f"00000000-0000-4000-8000-{i:012d}",
            )
            for i in range(120)
        ],
    )


def native_seed(engine: Engine, settings: Settings) -> None:
    from short_drama.domain.base import Base

    with engine.begin() as connection:
        tables = Base.metadata.tables
        connection.execute(
            tables["project_sound_modes"].insert(),
            dict(project_id=1000, row_version=1, mode="native"),
        )
        connection.execute(
            tables["media_files"].insert(),
            dict(
                id=67000,
                project_id=1000,
                format_code="audio/wav",
                storage_locator=f"minio://{settings.minio_audio_bucket}/voice/sample.wav",
                duration_ms=3100,
                byte_size=1024,
                checksum_sha256="a" * 64,
            ),
        )
        connection.execute(
            tables["character_voices"].insert(),
            dict(project_id=1000, asset_id=7000, row_version=1, record_id=21000, media_id=67000),
        )
        connection.execute(
            tables["shot_assets"].insert(),
            [
                dict(id=68000 + i, episode_id=2000, shot_id=10000 + i, asset_id=7000)
                for i in range(1, 118)
            ],
        )
        connection.execute(
            tables["shot_dialogues"].insert(),
            [
                dict(
                    shot_id=10000 + i,
                    row_version=1,
                    document=dict(
                        reviewed=True,
                        lines=[
                            dict(
                                character_id="7000",
                                text="Hello.",
                                speech="onscreen",
                                delivery="natural",
                            )
                        ],
                    ),
                )
                for i in range(120)
            ],
        )


LIST_ENDPOINTS = [
    ("projects", "/projects"),
    ("episodes", "/projects/1000/episodes"),
    ("project_assets", "/projects/1000/assets"),
    ("episode_assets", "/projects/1000/episodes/2000/assets"),
    ("storyboard", "/projects/1000/episodes/2000/shots"),
    ("tasks", "/ai/generations"),
    ("media", "/media-library/items"),
    ("ai_configs", "/ai-model-configs"),
    ("agent_conversations", "/agent/conversations?project_id=1000&episode_id=2000"),
    ("agent_messages", "/agent/conversations/30000/messages"),
]
DETAIL_ENDPOINTS = [
    ("auth_me", "/auth/me"),
    ("project_detail", "/projects/1000"),
    ("episode_detail", "/projects/1000/episodes/2000"),
    ("writing", "/projects/1000/episodes/2000/writing"),
    ("asset_detail", "/assets/7000"),
    ("shot_detail", "/projects/1000/episodes/2000/shots/10000"),
    ("task_detail", "/ai/generations/20000"),
    ("media_detail", "/media-library/items/22000"),
    ("config_detail", "/ai-model-configs/5001"),
    ("agent_conversation_detail", "/agent/conversations/30000"),
    ("agent_run_detail", "/agent/runs/33000"),
    ("assembly", "/projects/1000/episodes/2000/assembly"),
    ("assembly_source", "/projects/1000/episodes/2001/assembly"),
]


def _normalize(value: Any) -> Any:
    """比较业务响应时忽略各隔离库的创建和登录时间，保留其他字段。"""
    volatile = {"created_at", "updated_at", "finished_at", "expires_at", "last_opened_at"}
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items() if key not in volatile}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _checkpoint(output: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")


def measure(
    client: TestClient,
    sql_counts: list[int],
    output: dict[str, Any],
    path: Path,
    name: str,
    url: str,
    samples: int,
    warmups: int,
    limit: int | None = None,
) -> None:
    durations, queries, sizes, hashes, normalized_hashes = [], [], [], [], []
    for index in range(samples + warmups):
        sql_counts.clear()
        started = time.perf_counter()
        response = client.get("/api/v1" + url)
        elapsed = (time.perf_counter() - started) * 1000
        if response.status_code != 200:
            raise RuntimeError(f"Read benchmark {name} returned HTTP {response.status_code}")
        document = response.json()
        if limit is not None:
            assert len(document["items"]) == limit, name
        if name == "assembly":
            assert len(document["clips"]) == 120
        if name == "writing":
            assert len(document["novel"]["content"]) == 70000
            assert len(document["editing_script"]["content"]) == 60000
        if name == "native_storyboard":
            assert all(
                row["native_speech"]["voices"][0]["media_id"] == "67000"
                for row in document["items"]
            )
        if index >= warmups:
            durations.append(elapsed)
            queries.append(len(sql_counts))
            sizes.append(len(response.content))
            hashes.append(hashlib.sha256(response.content).hexdigest())
            normalized = json.dumps(_normalize(document), sort_keys=True).encode()
            normalized_hashes.append(hashlib.sha256(normalized).hexdigest())
    result = {
        "endpoint": name,
        "limit": limit,
        "samples": samples,
        "warmups": warmups,
        "p50_ms": round(statistics.median(durations), 3),
        # 单样本不报告P95；少样本的分位数也不作为线上统计结论。
        "p95_ms": round(sorted(durations)[math.ceil(samples * 0.95) - 1], 3)
        if samples > 1
        else None,
        "sql_min": min(queries),
        "sql_max": max(queries),
        "sql_median": statistics.median(queries),
        "response_bytes": statistics.median(sizes),
        "stable_response": len(set(hashes)) == 1,
        "response_sha256": hashes[-1],
        "normalized_response_sha256": normalized_hashes[-1],
        "durations_ms": [round(value, 3) for value in durations],
    }
    output["results"].append(result)
    _checkpoint(output, path)
    # CLI输出正式测量结果，不输出SQL文本、参数或凭据。
    hidden = {"durations_ms", "response_sha256", "normalized_response_sha256"}
    sys.stdout.write(
        json.dumps({key: value for key, value in result.items() if key not in hidden}) + "\n"
    )
    sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/backend-reads.json"))
    parser.add_argument("--profile", choices=["list", "detail", "native", "all"], default="all")
    parser.add_argument("--samples", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=3)
    parser.add_argument("--native-samples", type=int, default=3)
    parser.add_argument("--native-large-samples", type=int, default=1)
    parser.add_argument("--native-warmups", type=int, default=0)
    parser.add_argument("--docker-container")
    parser.add_argument("--source-root", type=Path)
    args = parser.parse_args()
    if min(args.samples, args.native_samples, args.native_large_samples) < 1:
        parser.error("sample counts must be positive")
    if min(args.warmups, args.native_warmups) < 0:
        parser.error("warmup counts cannot be negative")
    if args.source_root:
        source = args.source_root.resolve()
        if not (source / "short_drama" / "__init__.py").is_file():
            parser.error("source-root must contain the short_drama package")
        sys.path.insert(0, str(source))

    # 应用导入在source-root之后，保证before使用冻结源码。
    from short_drama.core.config import Settings
    from short_drama.main import create_app

    configured = Settings()
    output: dict[str, Any] = {
        "boundary": "real isolated MySQL, real login/session/CSRF/access, in-process TestClient",
        "minio": "local presign boundary replacement; no object/model/broker I/O",
        "profile": args.profile,
        "samples": args.samples,
        "warmups": args.warmups,
        "native_samples": args.native_samples,
        "native_large_samples": args.native_large_samples,
        "native_warmups": args.native_warmups,
        "page_sizes": [20, 100],
        "isolated_database_dropped": False,
        "results": [],
    }
    _checkpoint(output, args.output)
    with isolated_database(configured, docker_container=args.docker_container) as engine:
        settings = configured.model_copy(
            update={
                "db_name": engine.url.database,
                "db_host": engine.url.host,
                "db_port": engine.url.port or 3306,
                "db_user": engine.url.username,
                "db_password": SecretStr(engine.url.password or ""),
                "auth_enabled": True,
                "agent_enabled": True,
                "auth_cookie_secure": False,
                "public_origin": "http://testserver",
                "native_video_enabled": False,
                "audio_production_enabled": False,
            }
        )
        output["database"] = engine.url.database
        output["seed_rows"] = seed(engine, settings)
        if args.profile in {"all", "detail", "native"}:
            extra_seed(engine, settings)
            output["extra_rows"] = {
                "novel": 1,
                "script": 1,
                "adopted_videos": 120,
                "assembly_clips": 120,
            }
        with (
            patch.dict(
                os.environ, {"NATIVE_VIDEO_ENABLED": "false", "AUDIO_PRODUCTION_ENABLED": "false"}
            ),
            patch("short_drama.main.MinioStorage", PresignBoundary),
            TestClient(create_app(settings)) as client,
        ):
            logging.getLogger("httpx").setLevel(logging.WARNING)
            logging.getLogger("httpx2").setLevel(logging.WARNING)
            client.headers["Origin"] = "http://testserver"
            assert client.get("/api/v1/projects").status_code == 401
            login = client.post(
                "/api/v1/auth/login",
                json={
                    "username": "perf_owner",
                    "password": "benchmark-only-password",
                },
            )
            assert login.status_code == 200
            client.headers["X-CSRF-Token"] = client.cookies["sd_csrf"]
            assert client.get("/api/v1/auth/me").json()["user"]["id"] == "1"
            assert client.app.state.agent_schema_ready
            bind = client.app.state.session_factory.kw["bind"]
            sql_counts: list[int] = []

            def count(*_args: Any) -> None:
                sql_counts.append(1)

            event.listen(bind, "before_cursor_execute", count)
            try:
                if args.profile in {"list", "all"}:
                    for name, url in LIST_ENDPOINTS:
                        for limit in [20, 100]:
                            target = url + ("&" if "?" in url else "?") + f"limit={limit}"
                            measure(
                                client,
                                sql_counts,
                                output,
                                args.output,
                                name,
                                target,
                                args.samples,
                                args.warmups,
                                limit,
                            )
                if args.profile in {"detail", "all"}:
                    for name, url in DETAIL_ENDPOINTS:
                        measure(
                            client,
                            sql_counts,
                            output,
                            args.output,
                            name,
                            url,
                            args.samples,
                            args.warmups,
                        )
                if args.profile in {"native", "all"}:
                    native_seed(engine, settings)
                    settings.native_video_enabled = settings.audio_production_enabled = True
                    os.environ["NATIVE_VIDEO_ENABLED"] = "true"
                    os.environ["AUDIO_PRODUCTION_ENABLED"] = "true"
                    for limit in [20, 100]:
                        samples = args.native_samples if limit == 20 else args.native_large_samples
                        measure(
                            client,
                            sql_counts,
                            output,
                            args.output,
                            "native_storyboard",
                            f"/projects/1000/episodes/2000/shots?limit={limit}",
                            samples,
                            args.native_warmups,
                            limit,
                        )
            finally:
                event.remove(bind, "before_cursor_execute", count)
    output["isolated_database_dropped"] = True
    _checkpoint(output, args.output)
    sys.stdout.write(f"completed: {args.output}; isolated database dropped\n")


if __name__ == "__main__":
    main()
