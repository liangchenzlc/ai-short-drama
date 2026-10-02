import logging
import re


class SecretPathFilter(logging.Filter):
    """Invitation bearer tokens must never appear in web access logs."""

    def filter(self, record):
        def redact(value):
            if isinstance(value, str):
                return re.sub(
                    r"(/(?:api/v1/invitations|invite)/)[A-Za-z0-9_-]{43}",
                    r"\1[redacted]",
                    value,
                )
            return value

        record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(redact(v) for v in record.args)
        return True


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # SQL and bind parameters can contain credentials and user content.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, SecretPathFilter) for f in access.filters):
        access.addFilter(SecretPathFilter())
