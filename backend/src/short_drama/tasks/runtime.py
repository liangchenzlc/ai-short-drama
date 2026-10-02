"""Run with python -m short_drama.tasks.runtime alongside API and Workers."""

import logging
import time

from short_drama.core.config import Settings
from short_drama.core.logging import configure_logging
from short_drama.db.session import build_engine, session_factory
from short_drama.service.generation_batch_service import dispatch_batches
from short_drama.tasks.publisher import Publisher
from short_drama.tasks.recovery import purge_credentials, recover
from short_drama.tasks.render import RenderPublisher, cleanup_render_scratch


def main():
    configure_logging()
    settings = Settings()
    engine = build_engine(settings)
    from short_drama.db.readiness import assert_identity_ready

    assert_identity_ready(engine, settings)
    factory = session_factory(engine)
    publisher = Publisher(factory, settings)
    renders = RenderPublisher(factory, settings)
    from short_drama.storage.minio import MinioStorage

    storage = MinioStorage(settings)
    log = logging.getLogger(__name__)
    next_cleanup = 0
    try:
        while True:
            try:
                from short_drama.tasks.email import deliver_one

                deliver_one(factory, settings)
                from short_drama.service.resource_import_service import process_import

                process_import(factory, storage, settings)
                recover(factory, settings)
                dispatch_batches(factory, settings)
                if time.monotonic() >= next_cleanup:
                    from short_drama.tasks.email import prune_limits

                    prune_limits(factory)
                    purge_credentials(factory)
                    from short_drama.service.resource_import_service import cleanup_imports

                    cleanup_imports(factory, storage, settings)
                    cleanup_render_scratch(settings)
                    next_cleanup = time.monotonic() + 3600
                for _ in range(100):
                    if not publisher.tick():
                        break
                for _ in range(10):
                    if not renders.tick():
                        break
            except Exception:
                log.warning(
                    "Generation scheduler unavailable; retrying without exposing credentials"
                )
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        storage.close()
        engine.dispose()


if __name__ == "__main__":
    main()
