"""Read-only infrastructure checks. Never print connection strings or private content."""

from kombu import Connection
from sqlalchemy import text

from short_drama.core.config import Settings
from short_drama.db.readiness import inspect_agent_schema
from short_drama.db.session import build_engine
from short_drama.tasks.celery_app import agent_queue, render_queue, topology


def check_agent(connection, settings):
    print(f"Agent feature: {'enabled' if settings.agent_enabled else 'disabled'}")
    print(f"Agent authenticated accounts: {'enabled' if settings.auth_enabled else 'disabled'}")
    state = inspect_agent_schema(connection)
    print(f"Agent schema: {state['status']} ({len(state['gaps'])} structural gaps)")
    if state["status"] != "ready":
        print("Agent aggregate checks skipped; use agent_migration.py --precheck for schema gaps")
        return
    for label, table in (("Run", "agent_runs"), ("Turn", "agent_turns")):
        rows = connection.execute(
            text(f"SELECT status, COUNT(*) FROM {table} GROUP BY status")
        ).all()
        print(f"Agent {label} count: {sum(count for _, count in rows)}")
        for status, count in rows:
            print(f"Agent {label} status {status}: {count}")
    unknown = connection.scalar(text("SELECT COUNT(*) FROM agent_turns WHERE status='unknown'"))
    expired = connection.scalar(
        text(
            "SELECT COUNT(*) FROM agent_runs WHERE lease_until < UTC_TIMESTAMP(6) "
            "AND status NOT IN ('succeeded','failed','cancelled')"
        )
    )
    native_waits = connection.scalar(
        text("SELECT COUNT(*) FROM agent_tool_calls WHERE status='waiting_generation'")
    )
    print(f"Agent unknown turns: {unknown}; never reset or resubmit automatically")
    print(f"Agent expired active leases: {expired}")
    print(f"Agent native generation waits: {native_waits}")


def check_mysql(settings):
    engine = None
    try:
        engine = build_engine(settings)
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT TABLE_NAME, COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME IN "
                    "('async_tasks','ai_generation_records','media_assets') GROUP BY TABLE_NAME"
                )
            ).all()
            for name, count in rows:
                print(f"MySQL {name}: {count} columns")
            cache = connection.scalar(
                text(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
                    "AND TABLE_NAME='ai_model_configs' AND COLUMN_NAME='capability_cache'"
                )
            )
            print(f"MySQL capability_cache: {'present' if cache else 'missing'}")
            configs = connection.execute(
                text(
                    "SELECT service_type, COUNT(*) FROM ai_model_configs "
                    "WHERE enabled=1 AND is_deleted=0 GROUP BY service_type"
                )
            ).all()
            for kind, count in configs:
                print(f"Available {kind} configurations: {count}")
            if not configs:
                print("Available enabled model configurations: 0")
            tasks = connection.execute(
                text("SELECT status, COUNT(*) FROM async_tasks GROUP BY status")
            ).all()
            print("Existing generation task count:", sum(count for _, count in tasks))
            for status, count in tasks:
                print(f"Task status {status}: {count}")
            try:
                check_agent(connection, settings)
            except Exception as error:
                print(f"Agent check failed ({type(error).__name__}); details suppressed")
    except Exception as error:
        print(f"MySQL check failed ({type(error).__name__}); details suppressed")
    finally:
        if engine is not None:
            engine.dispose()


def check_broker(settings):
    queues = (*topology(settings)[2].values(), render_queue(settings), agent_queue(settings))
    try:
        with Connection(
            settings.rabbitmq_url.get_secret_value(),
            connect_timeout=5,
            ssl=True if settings.rabbitmq_tls else False,
        ) as broker:
            broker.connect()
            print("RabbitMQ AMQP authentication and vhost: connected")
            for queue in queues:
                channel = broker.channel()
                try:
                    declaration = channel.queue_declare(queue=queue.name, passive=True)
                    print(
                        f"RabbitMQ {queue.name}: messages={declaration.message_count}, "
                        f"consumers={declaration.consumer_count}"
                    )
                except Exception as error:
                    if getattr(error, "reply_code", None) == 404:
                        print(f"RabbitMQ {queue.name}: does not exist")
                    else:
                        print(
                            f"RabbitMQ {queue.name}: passive check failed ({type(error).__name__})"
                        )
                finally:
                    try:
                        channel.close()
                    except Exception:
                        pass
    except Exception as error:
        print(f"RabbitMQ check failed ({type(error).__name__}); details suppressed")


def main():
    try:
        settings = Settings()
    except Exception as error:
        print(f"Settings check failed ({type(error).__name__}); details suppressed")
        return 1
    check_mysql(settings)
    check_broker(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
