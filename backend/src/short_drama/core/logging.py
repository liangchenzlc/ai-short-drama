import logging


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # SQL and bind parameters can contain credentials and user content.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
