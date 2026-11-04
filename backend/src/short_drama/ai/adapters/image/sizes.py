"""Existing application size presets shared by image families."""

import re

from short_drama.ai.adapters.image.common import unsupported


def image_size(parameters, *, family, model):
    aspect, resolution = parameters.get("aspect"), parameters.get("resolution")
    if aspect is None and resolution is None:
        return None
    if resolution and re.fullmatch(r"\d{2,5}[x*]\d{2,5}", resolution):
        width, height = map(int, re.split("[x*]", resolution))
        if aspect:
            try:
                a, b = map(int, aspect.split(":"))
                # Allow provider-recommended dimensions rounded to multiples of 16.
                if not a or not b or abs(width / height - a / b) > 0.015:
                    unsupported()
            except (ValueError, ZeroDivisionError):
                unsupported()
        return f"{width}{'*' if family == 'dashscope' else 'x'}{height}"
    if family == "openai":
        if re.match(r"^gpt-image-2(?:[.-]|$)", model):
            # App presets, expressed as pixels rather than provider-specific
            # resolution labels. Keep legacy GPT Image / DALL-E rules below.
            presets = {
                "1K": {
                    "1:1": "1024x1024",
                    "16:9": "1536x864",
                    "9:16": "864x1536",
                    "4:3": "1152x864",
                    "3:4": "864x1152",
                    "3:2": "1536x1024",
                    "2:3": "1024x1536",
                },
                "2K": {
                    "1:1": "2048x2048",
                    "16:9": "2560x1440",
                    "9:16": "1440x2560",
                    "4:3": "2304x1728",
                    "3:4": "1728x2304",
                    "3:2": "2496x1664",
                    "2:3": "1664x2496",
                },
            }
            tier = "1K" if resolution in (None, "1024") else resolution
            size = presets.get(tier, {}).get(aspect or "1:1")
            if not size:
                unsupported()
            return size
        if resolution not in (None, "1K", "1024"):
            unsupported()
        sizes = {"1:1": "1024x1024", "3:2": "1536x1024", "2:3": "1024x1536"}
        if model.startswith("dall-e-3"):
            sizes = {"1:1": "1024x1024", "16:9": "1792x1024", "9:16": "1024x1792"}
        if aspect not in (None, *sizes):
            unsupported()
        return sizes[aspect or "1:1"]
    if family == "ark":
        if aspect is None and resolution in ("1K", "2K", "4K"):
            return resolution
        sizes = {
            "2K": {
                "1:1": "2048x2048",
                "16:9": "2560x1440",
                "9:16": "1440x2560",
                "4:3": "2304x1728",
                "3:4": "1728x2304",
                "3:2": "2496x1664",
                "2:3": "1664x2496",
            },
            "4K": {
                "1:1": "4096x4096",
                "16:9": "4096x2304",
                "9:16": "2304x4096",
                "4:3": "4096x3072",
                "3:4": "3072x4096",
            },
        }
        value = sizes.get(resolution or "2K", {}).get(aspect or "1:1")
    else:
        sizes = {
            "1:1": "1024*1024",
            "16:9": "1280*720",
            "9:16": "720*1280",
            "4:3": "1152*864",
            "3:4": "864*1152",
        }
        if model.startswith(("wan2.5", "wan2.6")):
            sizes = {
                "1:1": "1280*1280",
                "16:9": "1696*960",
                "9:16": "960*1696",
                "4:3": "1472*1104",
                "3:4": "1104*1472",
            }
        if resolution not in (None, "1K", "1024"):
            unsupported()
        value = sizes.get(aspect or "1:1")
    if not value:
        unsupported()
    return value
