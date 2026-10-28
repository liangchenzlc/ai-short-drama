"""移植固定源workflow/mapping.go的比例、尺寸、枚举和数值规则。"""

import json
import math
import re

from short_drama.schemas.canvas.generation.runninghub.runninghub import RunningHubField


def field_key(value: str) -> str:
    return re.sub(r"[_\- ]", "", value.strip().lower())


def scalar(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if type(value) is float and value.is_integer():
        value = int(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def option(value) -> str:
    if isinstance(value, dict):
        for key in ("value", "id", "key", "label", "name"):
            candidate = scalar(value.get(key)).strip()
            if candidate:
                return candidate
    return scalar(value)


def numeric(value, *, resolution=False) -> float | None:
    try:
        text = scalar(value).strip()
        if resolution:
            text = text.removesuffix("p").removesuffix("P")
        result = float(text)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def round_step(value: float, step: int) -> int:
    # Go math.Round为中点远离0，不能用Python银行家舍入。
    ratio = value / step
    return (math.floor(ratio + 0.5) if ratio >= 0 else math.ceil(ratio - 0.5)) * step


def pixel_dimensions(value: str) -> tuple[int, int] | None:
    if "x" not in value.strip().lower():
        return None
    parts = [part for part in re.split(r"[x ,]+", value.strip().lower()) if part]
    if len(parts) == 2 and all(re.fullmatch(r"\+?\d+", part) for part in parts):
        dimensions = tuple(int(part) for part in parts)
        if min(dimensions) > 0:
            return dimensions
    return None


def ratio_parts(value: str) -> tuple[int, int] | None:
    parts = value.split(":")
    if len(parts) == 2 and all(re.fullmatch(r"\+?\d+", part.strip()) for part in parts):
        ratio = tuple(int(part.strip()) for part in parts)
        if min(ratio) > 0:
            return ratio
    return None


def aspect_ratio(value: str) -> str:
    text = re.split(r"[ (]", value.strip().lower(), maxsplit=1)[0]
    text = re.sub(r"-[124]k$", "", text)
    ratio = ratio_parts(text)
    if ratio is None:
        dimensions = pixel_dimensions(text)
        if dimensions is None:
            return ""
        actual = dimensions[0] / dimensions[1]
        presets = (
            (1, 1),
            (3, 2),
            (2, 3),
            (4, 3),
            (3, 4),
            (4, 5),
            (5, 4),
            (16, 9),
            (9, 16),
            (2, 1),
            (1, 2),
            (21, 9),
        )
        best = min(presets, key=lambda item: abs(actual - item[0] / item[1]) / (item[0] / item[1]))
        ratio = (
            best if abs(actual - best[0] / best[1]) / (best[0] / best[1]) <= 0.03 else dimensions
        )
    divisor = math.gcd(*ratio)
    return f"{ratio[0] // divisor}:{ratio[1] // divisor}"


def image_dimensions(value: str) -> tuple[int, int] | None:
    text = value.strip().lower()
    presets = {
        "1:1": (1024, 1024),
        "3:2": (1536, 1024),
        "2:3": (1024, 1536),
        "4:3": (1360, 1024),
        "3:4": (1024, 1360),
        "4:5": (1024, 1280),
        "5:4": (1280, 1024),
        "16:9": (1824, 1024),
        "9:16": (1024, 1824),
        "2:1": (2048, 1024),
        "1:2": (1024, 2048),
        "21:9": (2352, 1008),
        "1:1-2k": (2048, 2048),
        "16:9-2k": (2048, 1152),
        "9:16-2k": (1152, 2048),
        "16:9-4k": (3840, 2160),
        "9:16-4k": (2160, 3840),
    }
    if text in presets:
        return presets[text]
    ratio = ratio_parts(aspect_ratio(text))
    if ratio is None:
        return None
    width, height = ratio
    if text.endswith(("-2k", "-4k")):
        edge = 3840 if text.endswith("-4k") else 2048
        return (
            (edge, round_step(edge * height / width, 16))
            if width >= height
            else (round_step(edge * width / height, 16), edge)
        )
    return (
        (round_step(1024 * width / height, 16), 1024)
        if width >= height
        else (1024, round_step(1024 * height / width, 16))
    )


def resolution_pixels(value: str) -> int:
    text = value.strip().lower()
    presets = {
        "low": 480,
        "auto": 720,
        "default": 720,
        "medium": 720,
        "high": 720,
        "2k": 1440,
        "4k": 2160,
    }
    if text in presets:
        return presets[text]
    token = text.removesuffix("p")
    return int(token) if re.fullmatch(r"\+?\d+", token) and int(token) > 0 else 0


def dimensions(mode: str, size: str, quality: str) -> tuple[int, int] | None:
    pixels = pixel_dimensions(size)
    if pixels is not None:
        return pixels
    if mode != "video":
        return image_dimensions(size)
    ratio, edge = ratio_parts(aspect_ratio(size)), resolution_pixels(quality)
    if ratio is None or edge <= 0:
        return None
    width, height = ratio
    return (
        (round_step(edge * width / height, 2), edge)
        if width >= height
        else (edge, round_step(edge * height / width, 2))
    )


def field_options(field: RunningHubField) -> list:
    if (
        field_key(field.class_type) == "resolutionselector"
        and field_key(field.field_name) == "aspectratio"
    ):
        return [
            "1:1 (Square)",
            "2:3 (Portrait Photo)",
            "3:2 (Photo)",
            "3:4 (Portrait Standard)",
            "4:3 (Standard)",
            "9:16 (Portrait Widescreen)",
            "16:9 (Widescreen)",
            "21:9 (Ultrawide)",
        ]
    return field.options


def configured_default(field: RunningHubField) -> str:
    return option(field.field_value if field.field_value is not None else field.value).strip()


def ratio_value(field: RunningHubField, value: str) -> str:
    raw, fallback = value.strip(), configured_default(field)
    choices = field_options(field)
    if choices:
        return next(
            (
                option(item).strip()
                for item in choices
                if option(item).strip() and aspect_ratio(option(item)) == aspect_ratio(raw)
            ),
            fallback or raw,
        )
    if fallback.lower() in {"auto", "adaptive"}:
        return fallback
    return aspect_ratio(raw)


def numeric_value(field: RunningHubField, value: str):
    number = numeric(value, resolution=True)
    minimum, maximum, step = numeric(field.min), numeric(field.max), numeric(field.step)
    if number is None or (
        all(item is None for item in (minimum, maximum, step))
        and field.field_type.strip().upper() != "NUMBER"
    ):
        return None
    if minimum is not None:
        number = max(number, minimum)
    if maximum is not None:
        number = min(number, maximum)
    if step is not None and step > 0:
        anchor = minimum or 0
        quotient = (number - anchor) / step
        rounded = math.floor(quotient + 0.5) if quotient >= 0 else math.ceil(quotient - 0.5)
        number = anchor + rounded * step
    if field.field_type.strip().upper() == "NUMBER" and number.is_integer():
        return int(number)
    return number


def selected_value(field: RunningHubField, value: str, *, duration=False):
    raw = value.strip()
    if not raw:
        return ""

    def token(text):
        return (
            (
                text.removesuffix("s").removesuffix("秒")
                if duration
                else text.removesuffix("P").removesuffix("p")
            )
            .strip()
            .lower()
        )

    fallback = configured_default(field)
    if field.options:
        choices = [option(item).strip() for item in field.options]
        for candidate in choices:
            if candidate and token(candidate) == token(raw):
                return candidate
        if fallback:
            return next(
                (
                    candidate
                    for candidate in choices
                    if candidate and token(candidate) == token(fallback)
                ),
                fallback,
            )
        return raw
    result = numeric_value(field, raw)
    if result is not None:
        return result
    if fallback and not (
        (
            numeric(fallback, resolution=True) is not None
            and numeric(raw, resolution=True) is not None
        )
        or fallback.strip().lower() == raw.lower()
    ):
        return fallback
    return raw
