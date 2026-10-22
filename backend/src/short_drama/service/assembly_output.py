"""成片输出比例独立于项目；受理时冻结服务端探测的显示尺寸。"""

from fractions import Fraction


def display_dimensions(width, height, sample_aspect_ratio="1:1", rotation=0):
    """FFmpeg 自动旋转后的显示尺寸，包含非方形像素的修正。"""
    try:
        sar = Fraction(str(sample_aspect_ratio).replace(":", "/"))
    except (ValueError, ZeroDivisionError):
        sar = Fraction(1)
    if sar <= 0:
        sar = Fraction(1)
    width = max(1, round(width * sar))
    height = int(height)
    if round(float(rotation)) % 180:
        width, height = height, width
    return width, height


def scaled_dimensions(width, height, short_side):
    scale = short_side / min(width, height)
    return max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)


def resolve_output(aspect, resolution, clips):
    """首个参与导出的分镜视频优先，其次导入视频；未就绪不猜测比例。"""
    short_side = {"1080p": 1080, "preview": 360}.get(resolution, 720)
    source = None
    if aspect == "auto":
        videos = [c for c in clips if c.get("source_type", "shot") != "image"]
        source = next((c for c in videos if c.get("source_type", "shot") == "shot"), None)
        source = source or next(iter(videos), None)
        if source and (
            source.get("probe_error") or not source.get("width") or not source.get("height")
        ):
            return {
                "output_width": None,
                "output_height": None,
                "output_source": {"media_id": source["media_id"], "clip_id": source["clip_id"]},
                "output_status": "invalid" if source.get("probe_error") else "preparing",
            }
        width, height = (source["width"], source["height"]) if source else (16, 9)
    else:
        width, height = (9, 16) if aspect == "9:16" else (16, 9)
    width, height = scaled_dimensions(width, height, short_side)
    return {
        "output_width": width,
        "output_height": height,
        "output_source": {
            "media_id": source["media_id"],
            "clip_id": source["clip_id"],
            "width": source["width"],
            "height": source["height"],
        }
        if source
        else None,
        "output_status": "ready",
    }
