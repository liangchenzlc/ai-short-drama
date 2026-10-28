"""Preview or explicitly collect unreferenced Skill resources older than the grace period."""

import argparse
import json
import sys

from short_drama.core.config import Settings
from short_drama.db.session import build_engine, session_factory
from short_drama.service.skills.resources.skill_resource import collect_unreferenced_skill_resources
from short_drama.storage.minio import MinioStorage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Remove only verified orphan versions")
    parser.add_argument("--grace-hours", type=int, default=24, help="Minimum 24, maximum 8760")
    parser.add_argument("--limit", type=int, default=1000, help="Maximum candidate object versions")
    args = parser.parse_args()
    if not 24 <= args.grace_hours <= 8760 or not 1 <= args.limit <= 10000:
        parser.error("--grace-hours 须为 24–8760，--limit 须为 1–10000")
    configured = Settings()
    engine = build_engine(configured)
    storage = MinioStorage(configured)
    try:
        result = collect_unreferenced_skill_resources(
            session_factory(engine),
            configured,
            storage,
            apply=args.apply,
            grace_hours=args.grace_hours,
            limit=args.limit,
        )
        result["database"] = engine.url.database
    finally:
        storage.close()
        engine.dispose()
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
