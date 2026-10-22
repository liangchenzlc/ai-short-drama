"""Check the immutable message-version and lease-token execution fence."""

from short_drama.core.time import utcnow


def owns_execution(run, fence):
    return fence is None or (
        run.message_version == fence[0]
        and run.lease_token == fence[1]
        and (fence[1] is None or (run.lease_until is not None and run.lease_until > utcnow()))
    )
