"""Validate bundled methods, then explicitly publish immutable DB/MinIO versions."""

import argparse
import json
import sys

from short_drama.agent.builtin_skill_packages import builtin_skill_packages
from short_drama.core.config import Settings
from short_drama.db.session import build_engine, session_factory
from short_drama.service.agent_builtin_skill_service import publish_builtin_skills
from short_drama.storage.minio import MinioStorage


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Publish to Settings MySQL and MinIO")
    args = parser.parse_args()
    packages = builtin_skill_packages()
    if args.apply:
        configured = Settings()
        engine = build_engine(configured)
        try:
            result = publish_builtin_skills(
                session_factory(engine),
                configured,
                MinioStorage(configured),
                packages,
            )
            result["database"] = engine.url.database
        finally:
            engine.dispose()
    else:
        result = {
            "apply": False,
            "packages": [
                {
                    "id": item.key,
                    "content_version": str(item.content_version),
                    "capability": item.capability,
                    "name": item.content.name,
                    "resource_count": len(item.content.resources),
                }
                for item in packages
            ],
        }
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
