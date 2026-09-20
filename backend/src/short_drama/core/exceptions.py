class BusinessError(Exception):
    status_code = 400
    code = "invalid_operation"

    def __init__(self, message: str = "Invalid operation"):
        self.message = message
        super().__init__(message)


class NotFound(BusinessError):
    status_code = 404
    code = "not_found"


class GenerationRequestError(BusinessError):
    """Closed admission errors; never reflect provider messages or request content."""

    messages = {
        "default_config_missing": "Choose a model or configure an enabled default model.",
        "unsupported_image_size": (
            "Clear aspect and resolution to use model defaults, "
            "or use an explicit supported pixel size."
        ),
        "unsupported_protocol": "The model service protocol is not supported.",
        "unsupported_parameters": "The selected parameters or reference images are not supported.",
    }

    def __init__(self, reason):
        reason = reason if reason in self.messages else "unsupported_parameters"
        self.code = f"generation_{reason}"
        super().__init__(self.messages[reason])


class Conflict(BusinessError):
    status_code = 409
    code = "conflict"


class DatabaseUnavailable(BusinessError):
    status_code = 503
    code = "database_unavailable"


class ConfigurationError(BusinessError):
    status_code = 503
    code = "configuration_error"


class StorageUnavailable(BusinessError):
    status_code = 503
    code = "storage_unavailable"
