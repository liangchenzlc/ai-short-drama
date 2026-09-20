"""Read-only infrastructure checks. Never print connection strings or secrets."""

from kombu import Connection
from sqlalchemy import text

from short_drama.core.config import Settings
from short_drama.db.session import build_engine

settings = Settings()
engine = build_engine(settings)
try:
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
except Exception as error:
    print(f"MySQL check failed ({type(error).__name__}); details suppressed")
finally:
    engine.dispose()

try:
    with Connection(
        settings.rabbitmq_url.get_secret_value(),
        connect_timeout=5,
        ssl=True if settings.rabbitmq_tls else False,
    ) as broker:
        broker.connect()
        print("RabbitMQ AMQP authentication and vhost: connected")
        for kind in ("text", "image", "video"):
            channel = broker.channel()
            try:
                declaration = channel.queue_declare(queue=f"tasks.ai.{kind}", passive=True)
                print(
                    f"RabbitMQ tasks.ai.{kind}: messages={declaration.message_count}, "
                    f"consumers={declaration.consumer_count}"
                )
            except Exception as error:
                if getattr(error, "reply_code", None) == 404:
                    print(f"RabbitMQ tasks.ai.{kind}: does not exist")
                else:
                    print(
                        f"RabbitMQ tasks.ai.{kind}: passive check failed ({type(error).__name__})"
                    )
            finally:
                try:
                    channel.close()
                except Exception:
                    pass
except Exception as error:
    print(f"RabbitMQ check failed ({type(error).__name__}); details suppressed")
