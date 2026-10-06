"""Run with python -m short_drama.tasks.runtime alongside API and Workers."""

import logging
import time

from sqlalchemy import inspect

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
    from short_drama.db.readiness import (
        assert_agent_ready,
        assert_identity_ready,
        inspect_agent_schema,
    )

    assert_identity_ready(engine, settings)
    assert_agent_ready(engine, settings)
    with engine.connect() as connection:
        agent_schema_available = inspect_agent_schema(connection)["status"] == "ready"
        canvas_cleanup_available = inspect(connection).has_table("canvas_resource_deletions")
        canvas_text_available = inspect(connection).has_table("canvas_task_text_deltas")
        canvas_beefapi_available = inspect(connection).has_table("canvas_beefapi_connections")
    factory = session_factory(engine)
    publisher = Publisher(factory, settings)
    renders = RenderPublisher(factory, settings)
    if settings.agent_enabled:
        from short_drama.agent.publisher import AgentPublisher
        from short_drama.agent.recovery import recover as recover_agent

        agents = AgentPublisher(factory, settings)
    from short_drama.storage.minio import MinioStorage

    storage = MinioStorage(settings)
    log = logging.getLogger(__name__)
    next_cleanup = 0
    next_agent_poll = 0
    next_canvas_cleanup = 0
    try:
        while True:
            if canvas_beefapi_available:
                from short_drama.service.canvas_beefapi_service import tick_beefapi_connections

                try:
                    tick_beefapi_connections(factory, settings)
                except Exception as error:
                    log.warning(
                        "Canvas BeefAPI recovery unavailable: error=%s", type(error).__name__
                    )
            if canvas_cleanup_available and time.monotonic() >= next_canvas_cleanup:
                from short_drama.service.canvas_resource_cleanup import cleanup_canvas_resources

                try:
                    cleanup_canvas_resources(factory, storage, settings, apply=True)
                except Exception as error:
                    log.warning(
                        "Canvas resource cleanup unavailable: error=%s", type(error).__name__
                    )
                next_canvas_cleanup = time.monotonic() + 10
            try:
                from short_drama.tasks.email import deliver_one

                deliver_one(factory, settings)
                from short_drama.service.resource_import_service import process_import

                process_import(factory, storage, settings)
                recover(factory, settings)
                dispatch_batches(factory, settings)
                if settings.agent_enabled:
                    recover_agent(factory, settings)
                if agent_schema_available and time.monotonic() >= next_agent_poll:
                    from short_drama.agent.native_tasks import collect_native_results

                    collect_native_results(factory, settings)
                    next_agent_poll = time.monotonic() + settings.agent_poll_seconds
                if time.monotonic() >= next_cleanup:
                    from short_drama.tasks.email import prune_limits

                    prune_limits(factory)
                    purge_credentials(factory)
                    from short_drama.service.resource_import_service import cleanup_imports

                    cleanup_imports(factory, storage, settings)
                    cleanup_render_scratch(settings)
                    if canvas_text_available:
                        from short_drama.service.canvas_text_stream import cleanup_canvas_text

                        cleanup_canvas_text(factory)
                    if canvas_cleanup_available:
                        from short_drama.service.canvas_creation_cleanup import (
                            cleanup_canvas_creations,
                        )
                        from short_drama.service.canvas_upload_cleanup import cleanup_canvas_uploads

                        cleanup_canvas_uploads(factory, storage, settings, apply=True)
                        cleanup_canvas_creations(factory, storage, settings, apply=True)
                    next_cleanup = time.monotonic() + 3600
                for _ in range(100):
                    if not publisher.tick():
                        break
                for _ in range(10):
                    if not renders.tick():
                        break
                if settings.agent_enabled:
                    for _ in range(50):
                        if not agents.tick():
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
