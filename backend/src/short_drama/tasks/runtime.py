"""Run with python -m short_drama.tasks.runtime alongside API and Workers."""

import logging
import time

from short_drama.core.config import Settings
from short_drama.core.logging import configure_logging
from short_drama.db.session import build_engine, session_factory
from short_drama.tasks.publisher import Publisher
from short_drama.tasks.recovery import purge_credentials, recover


def main():
    configure_logging()
    settings = Settings()
    engine = build_engine(settings)
    factory = session_factory(engine)
    publisher = Publisher(factory, settings)
    log = logging.getLogger(__name__)
    next_cleanup = 0
    try:
        while True:
            try:
                recover(factory, settings)
                if time.monotonic() >= next_cleanup:
                    purge_credentials(factory)
                    next_cleanup = time.monotonic() + 3600
                for _ in range(100):
                    if not publisher.tick():
                        break
            except Exception:
                log.warning(
                    "Generation scheduler unavailable; retrying without exposing credentials"
                )
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
