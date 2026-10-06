"""检查或重试永久删除素材的资源回收回执；不删除仍有规范记录的文件。"""

import argparse
import json
import logging

from short_drama.core.config import Settings
from short_drama.core.logging import configure_logging
from short_drama.db.session import build_engine, session_factory
from short_drama.service.canvas_resource_cleanup import cleanup_canvas_resources
from short_drama.storage.minio import MinioStorage

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=32)
    args = parser.parse_args()
    configure_logging()
    settings = Settings()
    engine, storage = build_engine(settings), MinioStorage(settings)
    try:
        result = cleanup_canvas_resources(
            session_factory(engine), storage, settings, limit=args.limit, apply=args.apply
        )
        logger.info("画布资源回收：%s", json.dumps(result, ensure_ascii=False))
        return 1 if result["failed"] else 0
    finally:
        storage.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
