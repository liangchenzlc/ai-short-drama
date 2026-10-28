"""采用事务内发布业务作品，不公开对应的私有任务和候选。"""

from short_drama.service.base import utcnow


def publish(entity):
    """保留首次明确采用时间，替换当前指针不撤回曾采用作品。"""
    if entity.published_at is None:
        entity.published_at = utcnow()
    return entity


def public_voice_context(context):
    """执行快照保留内部关联，共享页面只读采用后的声音作品。"""
    if context is None:
        return None
    return {
        **context,
        "voices": [
            {key: value for key, value in voice.items() if key != "record_id"}
            for voice in context.get("voices", [])
        ],
    }
