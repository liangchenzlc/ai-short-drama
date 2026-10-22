"""Task recovery decisions and safe public errors, independent of admission services."""

from short_drama.core.exceptions import BusinessError
from short_drama.core.time import utcnow
from short_drama.schemas.storyboard_result import parse_storyboard_result

ERROR_MESSAGES = {
    "provider_queue_full": "模型服务的视频队列已满，本次未创建供应商任务。请稍后显式重试。",
    "canvas_work_disposed": "原画布作品已处置，执行载荷已回收，不能恢复或重新生成。",
    "transport_error": (
        "与模型服务通信时连接中断，未收到完整结果，无法确认服务商是否已受理。"
        "系统未自动重发，请先核对服务商调用记录。"
    ),
    "upload_timeout": (
        "向模型服务上传参考图片超时，无法确认服务商是否已受理。"
        "系统未自动重发，请先核对服务商调用记录；可压缩参考图或检查网络后重新生成。"
    ),
    "unsafe_address": "图片或模型服务地址被安全检查拦截，请检查存储与模型服务的地址配置。",
    "reference_missing": "参考图片已不存在，请重新选择素材图片后生成。",
    "reference_storage_unavailable": "暂时无法读取参考图片，请检查对象存储服务后重试。",
    "reference_images_too_large": "参考图片超过上传大小限制，请压缩图片或减少参考图片数量。",
    "invalid_reference_image": "参考图片损坏或格式不受支持，请使用 PNG、JPEG 或 WebP 图片。",
    "invalid_mask_image": "蒙版必须为带透明编辑区域的有效 PNG 图片，请重新绘制蒙版。",
    "mask_dimensions_mismatch": "蒙版与源图片的像素尺寸不同，请重新打开源图片绘制蒙版。",
    "timeout": "等待模型响应超时，任务已失败；生成请求未自动重发，请核对服务商调用记录。",
    "generation_timeout": "生成任务超过等待时限，任务已失败；已停止自动查询和生成。",
    "upstream_unavailable": (
        "模型服务或中转站返回了服务器错误（HTTP 5xx）。"
        "当前无法确认生成结果，生成请求未自动重发；请核对服务商调用记录。"
    ),
    "partial_result": "部分生成结果未完成，已保存的结果仍可使用。",
    "text_truncated": "The model stopped at its output limit.",
    "message_delivery_unknown": "Message delivery needs verification.",
    "acceptance_unknown": "Model acceptance needs verification.",
    "archive_failed": "Generated results could not be saved.",
    "download_failed": "生成结果下载失败，请取回原任务结果，不要重新提交。",
    "archive_timeout": "The result saving window expired.",
    "business_save_failed": "结果已保存，业务入库失败，可恢复本地保存。",
    "invalid_structured_output": "模型返回的结构不正确，原文已保留。请调整要求后重新生成。",
    "unknown_asset_reference": "模型引用了不存在于输入清单的素材，原文已保留。",
    "source_missing": "生成来源已不存在，原始结果已保留。",
    "shot_video_auto_apply_skipped": "视频已生成并保存，未替换当前确定视频。",
    "empty_result": "模型未返回有效正文。",
}


def safe_error(error):
    if not error:
        return None
    code = error.get("code", "generation_failed")
    if not isinstance(code, str) or not code.replace("_", "").isalnum() or len(code) > 80:
        code = "generation_failed"
    result = {
        "code": code,
        "message": ERROR_MESSAGES.get(code, "Generation could not be completed."),
    }
    if code == "shot_video_auto_apply_skipped":
        reason = {
            "shot_video_superseded": "已有更新生成的视频，本次结果仅保留在生成记录中。",
            "shot_version_conflict": "分镜、提示词、参考图或声音已变化，请核对生成记录。",
            "generation_access_revoked": "生成者已无权更新此分镜，结果仅保留在生成记录中。",
            "generation_cancelled": "任务已取消，已保存的结果仅保留在生成记录中。",
            "native_audio_invalid": "视频没有通过原生音轨检查，请核对生成记录。",
            "shot_archived": "分镜已归档，结果仅保留在生成记录中。",
            "not_found": "生成来源已不存在，结果仅保留在生成记录中。",
            "source_mismatch": "生成结果的作品归属不一致，请核对生成记录。",
        }.get(error.get("reason"), "请核对当前分镜和生成记录。")
        result["message"] += reason
    http_status = error.get("http_status")
    if type(http_status) is int and 100 <= http_status <= 599:
        result["http_status"] = http_status
        result["message"] = result["message"].replace("HTTP 5xx", f"HTTP {http_status}")
    if code == "partial_result":
        expected, returned, saved = (
            error.get("expected_count"),
            error.get("returned_count"),
            error.get("saved_count"),
        )
        if (
            all(type(count) is int for count in (expected, returned, saved))
            and expected > 0
            and 0 <= saved <= min(expected, returned)
        ):
            result["message"] = (
                f"请求 {expected} 个结果，模型服务仅返回 {returned} 个，已保存 {saved} 个。"
                "其余结果未生成成功，已保存的结果仍可使用。"
                if returned < expected
                else f"已保存 {saved}/{expected} 个结果，其余结果保存失败，可尝试恢复保存。"
            )
    return result


def can_retry(task, record):
    from short_drama.ai.canvas_audio_adapters import ASYNC_AUDIO
    from short_drama.ai.canvas_runninghub_adapters import RUNNINGHUB_ADAPTERS
    from short_drama.ai.canvas_video_adapters import VIDEO_ADAPTERS

    if (task.error or {}).get("code") == "canvas_work_disposed":
        return False
    if (
        ((getattr(record, "request_data", None) or {}).get("source") or {}).get("scene")
        == "canvas_node"
        and record.status == "succeeded"
        and (
            getattr(record, "adapter", None) in RUNNINGHUB_ADAPTERS
            or (
                getattr(task, "service_type", None) == "video"
                and getattr(record, "adapter", None) in VIDEO_ADAPTERS
                and record.provider_task_id
            )
            or (
                getattr(task, "service_type", None) == "audio"
                and getattr(record, "adapter", None) in {"openai_speech.v1", ASYNC_AUDIO}
            )
        )
    ):
        return False
    return (
        task.status in {"failed", "cancelled"}
        and record.status not in {"sent", "unknown"}
        and (task.error or {}).get("code")
        not in {"message_delivery_unknown", "business_save_failed"}
    )


def can_reparse_storyboard(record):
    request = getattr(record, "request_data", None) or {}
    if (request.get("source") or {}).get("scene") != "script_shots":
        return False
    snapshot = request.get("source_snapshot") or {}
    try:
        parse_storyboard_result(
            record.text_content,
            {int(asset["id"]) for asset in snapshot["assets"]},
            snapshot["content"],
        )
    except (KeyError, TypeError, ValueError):
        return False
    return True


def resume_action(task, record):
    from short_drama.ai.canvas_runninghub_adapters import RUNNINGHUB_ADAPTERS

    if (task.error or {}).get("code") == "canvas_work_disposed":
        return None
    response = record.response_data or {}
    entries = response.get("media_manifest", [])
    expected_count = response.get("expected_count", 1)
    if getattr(record, "adapter", None) in RUNNINGHUB_ADAPTERS:
        expected_count -= sum(bool(item.get("ignored")) for item in entries)
        entries = [item for item in entries if not item.get("ignored")]
    request = getattr(record, "request_data", None) or {}
    image_group = (
        bool(request.get("image_group_id"))
        or (request.get("source") or {}).get("scene") == "image_operation"
    )
    recoverable_media = bool(entries) and (
        any(
            not item.get("saved")
            and (item.get("source_cipher") or item.get("locator") or item.get("inline_cipher"))
            for item in entries
        )
        or any(
            image_group and item.get("saved") and not item.get("image_group_saved")
            for item in entries
        )
        or (all(item.get("saved") for item in entries) and len(entries) >= expected_count)
    )
    if task.locked_until and task.locked_until > utcnow():
        return None
    if (
        task.status == "failed"
        and record.status == "succeeded"
        and getattr(record, "text_content", None)
        and response.get("finish_reason") not in {"length", "max_output_tokens"}
        and (
            (task.error or {}).get("code")
            in {"business_save_failed", "archive_timeout", "message_delivery_unknown"}
            or (
                (task.error or {}).get("code") == "invalid_structured_output"
                and can_reparse_storyboard(record)
            )
        )
    ):
        return "save"
    if task.status == "failed" and record.status == "succeeded" and recoverable_media:
        return "save"
    if (
        task.status == "failed"
        and record.status == "sent"
        and getattr(record, "adapter", None) in RUNNINGHUB_ADAPTERS
        and record.provider_task_id
        and not task.cancel_requested
        and (
            (task.error or {}).get("code")
            in {
                "upstream_unavailable",
                "invalid_response",
                "connection_error",
                "transport_error",
                "dns_error",
                "timeout",
                "rate_limited",
                "provider_rate_limit",
                "provider_task_not_ready",
            }
            or (task.error or {}).get("http_status") in {404, 408, 409, 425, 429}
        )
        and task.started_at is not None
        and (utcnow() - task.started_at).total_seconds()
        < record.config_snapshot.get("budget_seconds", 180)
    ):
        return "poll"
    if task.status != "failed" or (task.error or {}).get("code") != "message_delivery_unknown":
        return None
    if task.next_action == "submit" and record.status == "prepared" and not task.cancel_requested:
        return "submit"
    if task.next_action == "poll" and record.provider_task_id:
        return "poll"
    if task.next_action == "save" and record.status == "succeeded" and recoverable_media:
        return "save"
    return None


def validate_idempotency_key(key):
    if (
        not isinstance(key, str)
        or not key.strip()
        or len(key) > 128
        or any(ord(c) < 32 for c in key)
    ):
        raise BusinessError("A nonempty Idempotency-Key of at most 128 characters is required")
    return key
