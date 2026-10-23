"""Conversation-scoped display order shared by assistant messages and task steps.

Messages and task operations are written by different processes, so their wall-clock
timestamps are not a safe ordering key. Both take a number from the conversation's
monotonic ``next_item_seq`` instead: the client only has to sort by that number to
interleave narration and steps.

Callers must hold the conversation row lock. ``AgentRunService.send_message``,
``lock_run`` and the task coordinator all lock it before writing.
"""

KIND_REPLY = "reply"
KIND_NARRATION = "narration"
KIND_NOTICE = "notice"
MESSAGE_KINDS = frozenset({KIND_REPLY, KIND_NARRATION, KIND_NOTICE})


def allocate_item_seq(conversation) -> int:
    """Reserve the next display position. Never call without the row locked."""
    value = int(conversation.next_item_seq or 1)
    conversation.next_item_seq = value + 1
    return value
