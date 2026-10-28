"""检查或执行本人已处置画布的生成媒体回收，不删除独立素材或其他引用。"""

import argparse
import json
import logging

from short_drama.core.config import Settings
from short_drama.core.logging import configure_logging
from short_drama.db.session import build_engine, session_factory
from short_drama.service.canvas.generation.generation_media_cleanup import (
    cleanup_canvas_generation_media,
    retire_canvas_generation_media,
)
from short_drama.storage.minio import MinioStorage

logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=32)
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit必须在1–1000之间")
    configure_logging()
    settings = Settings()
    engine, storage = build_engine(settings), MinioStorage(settings)
    try:
        factory = session_factory(engine)
        retired = retire_canvas_generation_media(factory, settings, apply=args.apply)
        removed = cleanup_canvas_generation_media(
            factory, storage, settings, limit=args.limit, apply=args.apply
        )
        logger.info("画布生成媒体退役：%s", json.dumps(retired, ensure_ascii=False))
        logger.info("画布生成字节回收：%s", json.dumps(removed, ensure_ascii=False))
        return 1 if retired["failed"] or removed["failed"] else 0
    finally:
        storage.close()
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
