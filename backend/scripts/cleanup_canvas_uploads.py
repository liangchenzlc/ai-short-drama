"""检查或清理画布上传暂存对象；完成资源和作品引用不会删除。"""

import argparse
import json
import logging

from short_drama.core.config import Settings
from short_drama.core.logging import configure_logging
from short_drama.db.session import build_engine, session_factory
from short_drama.service.canvas_creation_cleanup import cleanup_canvas_creations
from short_drama.service.canvas_upload_cleanup import cleanup_canvas_uploads
from short_drama.storage.minio import MinioStorage

logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    configure_logging()
    settings = Settings()
    engine, storage = build_engine(settings), MinioStorage(settings)
    try:
        result = cleanup_canvas_uploads(
            session_factory(engine), storage, settings, apply=args.apply
        )
        logger.info("画布上传暂存检查：%s", json.dumps(result, ensure_ascii=False))
        creations = cleanup_canvas_creations(
            session_factory(engine), storage, settings, apply=args.apply
        )
        logger.info("画布创建暂存检查：%s", json.dumps(creations, ensure_ascii=False))
        return 1 if result["failed"] or creations["failed"] else 0
    finally:
        storage.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
