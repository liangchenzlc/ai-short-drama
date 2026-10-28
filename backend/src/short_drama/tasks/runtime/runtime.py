"""Run with python -m short_drama.tasks.runtime.runtime alongside API and Workers."""

import logging
import time

from sqlalchemy import inspect

from short_drama.core.config import Settings
from short_drama.core.logging import configure_logging
from short_drama.db.session import build_engine, session_factory
from short_drama.service.generations.batches.batch import dispatch_batches
from short_drama.tasks.canvas.canvas_local import CanvasLocalPublisher
from short_drama.tasks.rendering.canvas_render import CanvasRenderPublisher
from short_drama.tasks.rendering.render import RenderPublisher, cleanup_render_scratch
from short_drama.tasks.runtime.publisher import Publisher
from short_drama.tasks.runtime.recovery import purge_credentials, recover


def main():
    configure_logging()
    settings = Settings()
    engine = build_engine(settings)
    log = logging.getLogger(__name__)
    from short_drama.db.readiness import (
        assert_agent_ready,
        assert_identity_ready,
        inspect_agent_schema,
    )

    assert_identity_ready(engine, settings)
    agent_degraded = None
    try:
        assert_agent_ready(engine, settings)
    except Exception as error:
        # A missing Agent schema must not stop native, canvas or cleanup dispatch.
        agent_degraded = type(error).__name__
        log.error(
            "Agent schema is not ready; scheduler continues without Agent work: reason=%s",
            agent_degraded,
        )
    with engine.connect() as connection:
        agent_schema_available = inspect_agent_schema(connection)["status"] == "ready"
        canvas_cleanup_available = inspect(connection).has_table("canvas_resource_deletions")
        canvas_generation_cleanup_available = inspect(connection).has_table(
            "canvas_generation_media_deletions"
        )
        canvas_text_available = inspect(connection).has_table("canvas_task_text_deltas")
        canvas_render_available = inspect(connection).has_table("canvas_render_jobs")
        canvas_local_available = inspect(connection).has_table("canvas_local_jobs")
    factory = session_factory(engine)
    publisher = Publisher(factory, settings)
    renders = RenderPublisher(factory, settings)
    canvas_renders = CanvasRenderPublisher(factory, settings) if canvas_render_available else None
    canvas_locals = CanvasLocalPublisher(factory, settings) if canvas_local_available else None
    agent_ready = settings.agent_enabled and agent_degraded is None
    if agent_ready:
        from short_drama.agent.coordinator.publisher import AgentPublisher
        from short_drama.agent.coordinator.recovery import recover as recover_agent
        from short_drama.agent.memory.runtime import AgentMemoryPublisher, AgentMemoryStore

        agents = AgentPublisher(factory, settings)
        memories = AgentMemoryPublisher(factory, settings)
        memory_store = AgentMemoryStore(factory, settings)
    from short_drama.storage.minio import MinioStorage

    storage = MinioStorage(settings)
    next_cleanup = 0
    next_agent_poll = 0
    next_canvas_cleanup = 0
    try:
        while True:
            if canvas_cleanup_available and time.monotonic() >= next_canvas_cleanup:
                from short_drama.service.canvas.resources.recycle.recycle_cleanup import (
                    cleanup_canvas_recycle,
                )
                from short_drama.service.canvas.resources.resource_cleanup import (
                    cleanup_canvas_resources,
                )
                from short_drama.service.canvas.timeline.playback.playback_cleanup import (
                    cleanup_canvas_playback,
                )

                try:
                    cleanup_canvas_recycle(factory, settings, apply=True)
                except Exception as error:
                    log.warning(
                        "Canvas recycle cleanup unavailable: error=%s", type(error).__name__
                    )
                try:
                    cleanup_canvas_resources(factory, storage, settings, apply=True)
                except Exception as error:
                    log.warning(
                        "Canvas resource cleanup unavailable: error=%s", type(error).__name__
                    )
                if canvas_local_available:
                    try:
                        cleanup_canvas_playback(factory, settings, storage)
                    except Exception as error:
                        log.warning(
                            "Canvas playback cleanup unavailable: error=%s", type(error).__name__
                        )
                if canvas_generation_cleanup_available:
                    from short_drama.service.canvas.generation.generation_media_cleanup import (
                        cleanup_canvas_generation_media,
                        retire_canvas_generation_media,
                    )

                    try:
                        retire_canvas_generation_media(factory, settings, apply=True)
                        cleanup_canvas_generation_media(factory, storage, settings, apply=True)
                    except Exception as error:
                        log.warning(
                            "Canvas generation media cleanup unavailable: error=%s",
                            type(error).__name__,
                        )
                next_canvas_cleanup = time.monotonic() + 10
            try:
                from short_drama.tasks.identity.email import deliver_one

                deliver_one(factory, settings)
                from short_drama.service.media.imports.resources import process_import

                process_import(factory, storage, settings)
                recover(factory, settings)
                dispatch_batches(factory, settings)
                if agent_schema_available:
                    from short_drama.agent.session.purge import purge_one
                    from short_drama.service.media.files.storage import StorageService

                    purge_one(factory, StorageService(storage, settings))
                if agent_ready:
                    recover_agent(factory, settings)
                    memory_store.recover()
                if agent_schema_available and time.monotonic() >= next_agent_poll:
                    from short_drama.agent.coordinator.media_runtime import collect_media_results
                    from short_drama.agent.native.tasks import collect_native_results

                    collect_native_results(factory, settings)
                    collect_media_results(factory, settings)
                    next_agent_poll = time.monotonic() + settings.agent_poll_seconds
                if time.monotonic() >= next_cleanup:
                    from short_drama.tasks.identity.email import prune_limits

                    prune_limits(factory)
                    purge_credentials(factory)
                    from short_drama.service.media.imports.resources import cleanup_imports

                    cleanup_imports(factory, storage, settings)
                    cleanup_render_scratch(settings)
                    if canvas_text_available:
                        from short_drama.service.canvas.generation.text.text_stream import (
                            cleanup_canvas_text,
                        )

                        cleanup_canvas_text(factory)
                    if canvas_cleanup_available:
                        from short_drama.service.canvas.resources.upload_cleanup import (
                            cleanup_canvas_uploads,
                        )
                        from short_drama.service.canvas.workspace.creation.creation_cleanup import (
                            cleanup_canvas_creations,
                        )

                        cleanup_canvas_uploads(factory, storage, settings, apply=True)
                        cleanup_canvas_creations(factory, storage, settings, apply=True)
                    next_cleanup = time.monotonic() + 3600
                for _ in range(100):
                    if not publisher.tick():
                        break
                for _ in range(10):
                    if not renders.tick():
                        break
                if canvas_renders is not None:
                    for _ in range(10):
                        if not canvas_renders.tick():
                            break
                if canvas_locals is not None:
                    for _ in range(10):
                        if not canvas_locals.tick():
                            break
                if agent_ready:
                    for _ in range(50):
                        if not agents.tick():
                            break
                    for _ in range(10):
                        if not memories.tick():
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
