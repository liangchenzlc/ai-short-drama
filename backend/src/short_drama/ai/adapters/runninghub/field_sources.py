"""RunningHub 工作流字段来源识别与输入槽位分类。"""

import math
import re
from functools import cmp_to_key

from short_drama.ai.adapters.runninghub.values import (
    field_key,
    scalar,
)
from short_drama.ai.types import GenerationError
from short_drama.schemas.canvas.generation.runninghub.runninghub import RunningHubField

PROMPT_SOURCES = {"prompt", "text", "positiveprompt", "positive"}


MEDIA_SOURCES = {
    "referenceimage": "referenceImages",
    "image": "referenceImages",
    "referenceimages": "referenceImages",
    "referencevideo": "referenceVideos",
    "video": "referenceVideos",
    "referencevideos": "referenceVideos",
    "referenceaudio": "referenceAudios",
    "audio": "referenceAudios",
    "referenceaudios": "referenceAudios",
    "mask": "mask",
}


BUSINESS_FIELDS = set(
    (
        "aspectratio ratio duration durationseconds seconds videoseconds quality resolution seed "
        "noiseseed steps step sigmapoints cfg cfgscale guidance guidancescale sampler samplername "
        "scheduler fps count batch batchsize generateaudio watermark negativeprompt systemprompt"
    ).split()
)


def _invalid():
    raise GenerationError("unsupported_workflow_parameters")


def dimension_source(key: str) -> str:
    for prefix in (
        "",
        "image",
        "video",
        "size",
        "output",
        "target",
        "latent",
        "frame",
        "canvas",
        "source",
        "resolution",
        "final",
    ):
        for suffix in ("width", "height"):
            if key == prefix + suffix:
                return suffix
    return {
        "pixelwidth": "width",
        "widthpixels": "width",
        "pixelheight": "height",
        "heightpixels": "height",
    }.get(key, "")


def named_source(name: str, mode="") -> str:
    key = field_key(name)
    if "mask" in key:
        return "mask"
    if source := dimension_source(key):
        return source
    groups = {
        "aspectRatio": {
            "ratio",
            "aspectratio",
            "imageaspectratio",
            "imageratio",
            "videoaspectratio",
            "videoratio",
        },
        "vquality": {"videoresolution", "videoquality", "vquality"},
        "size": {"size", "imagesize", "imageresolution"},
        "count": {
            "batch",
            "batchsize",
            "count",
            "numimages",
            "numberofimages",
            "imagecount",
            "imagescount",
        },
        "quality": {"quality"},
        "videoSeconds": {
            "duration",
            "seconds",
            "durationseconds",
            "videoseconds",
            "videoduration",
            "videodurationseconds",
            "videolength",
            "clipduration",
        },
        "videoGenerateAudio": {"generateaudio", "videogenerateaudio"},
        "videoWatermark": {"watermark", "videowatermark"},
        "audioFormat": {"audioformat"},
        "audioVoice": {"voice", "audiovoice"},
        "audioSpeed": {"audiospeed"},
        "audioInstructions": {"audioinstructions"},
        "transparentBackground": {"transparentbackground", "transparent"},
    }
    if key == "resolution" and mode in {"image", "video"}:
        return "vquality" if mode == "video" else "size"
    return next((source for source, names in groups.items() if key in names), "")


def dynamic_source(name: str, field_type: str, mode="") -> str:
    return named_source(name, mode) or {
        "IMAGE": "referenceImage",
        "VIDEO": "referenceVideo",
        "AUDIO": "referenceAudio",
    }.get(field_type.strip().upper(), "")


def normalized_source(field: RunningHubField) -> str:
    source = (field.source or "").strip().lower()
    if source:
        return field_key(source)
    if field.bind_prompt:
        return "prompt"
    if not field.source_from_upstream:
        return ""
    if field.field_name.strip().lower() in {"prompt", "text", "positive_prompt", "positiveprompt"}:
        return "prompt"
    return {
        "image": "referenceimage",
        "img": "referenceimage",
        "photo": "referenceimage",
        "picture": "referenceimage",
        "video": "referencevideo",
        "movie": "referencevideo",
        "audio": "referenceaudio",
        "sound": "referenceaudio",
        "music": "referenceaudio",
        "voice": "referenceaudio",
    }.get(field.field_type.strip().lower(), "")


def fields_for_mode(fields: list[RunningHubField], mode: str) -> list[RunningHubField]:
    result = []
    for original in fields:
        field = original.model_copy(deep=True)
        configured = field.source is not None or field.source_from_upstream is not None
        if not configured:
            field.source = (
                "prompt"
                if field.bind_prompt
                else dynamic_source(field.field_name, field.field_type)
            )
            if field.field_type.strip().lower() in {"image", "video", "audio"}:
                field.source_from_upstream = True
        canonical = named_source(field.source or "", mode)
        if canonical:
            field.source = canonical
        inferred = dynamic_source(field.field_name, field.field_type, mode)
        if not (field.source or "").strip() and not configured and not field.bind_prompt:
            field.source = inferred
            if inferred:
                field.source_automatic = True
        source_key, inferred_key, name_key = (
            field_key(field.source or ""),
            field_key(inferred),
            field_key(field.field_name),
        )
        if source_key and inferred_key and source_key != inferred_key:
            was_media = source_key in {"referenceimage", "referencevideo", "referenceaudio"}
            repair_media = was_media and (
                dimension_source(name_key) or inferred_key in {"aspectratio", "vquality"}
            )
            repair_resolution = name_key == "resolution" and (
                (mode == "video" and source_key == "size")
                or (mode == "image" and source_key == "vquality")
            )
            if (
                repair_media
                or repair_resolution
                or name_key == "quality"
                and source_key == "vquality"
            ):
                field.source = inferred
        result.append(field)
    return result


def is_link(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and scalar(value[0]) != ""
        and type(value[1]) in {int, float}
        and math.isfinite(value[1])
        and int(value[1]) == value[1]
    )


def unsafe_field(field: RunningHubField, workflow: dict) -> bool:
    node = workflow.get(field.node_id.strip())
    class_type = node.get("class_type", "") if isinstance(node, dict) else field.class_type
    class_type, name = str(class_type).strip().lower(), field.field_name.strip().lower()
    return (
        field.safe_to_override is False
        or class_type == "int"
        and name == "value"
        or class_type == "imageresize+"
        and name in {"width", "height", "multiple_of"}
    )


def prompt_candidate(name: str, class_type: str, title: str) -> bool:
    if field_key(name) in {
        "text",
        "prompt",
        "positiveprompt",
        "positive",
        "caption",
        "description",
    }:
        return True
    return name.strip().lower() == "value" and any(
        marker in class_type or marker in title
        for marker in ("text", "string", "prompt", "提示词", "文本")
    )


def _node_compare(left: str, right: str) -> int:
    def parsed(value):
        if re.fullmatch(r"[+-]?\d+", value.strip()):
            number = int(value)
            if -(2**63) <= number < 2**63:
                return number
        return None

    a, b = parsed(left), parsed(right)
    a, b = (a, b) if a is not None and b is not None else (left, right)
    return (a > b) - (a < b)


def prompt_fallback(workflow: dict, prompt: str) -> list[dict]:
    best, score = None, -1000
    for node_id in sorted(workflow, key=cmp_to_key(_node_compare)):
        node = workflow[node_id]
        if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
            continue
        class_type = str(node.get("class_type", "")).strip().lower()
        meta = node.get("_meta") or {}
        title = str(meta.get("title", "")).strip().lower() if isinstance(meta, dict) else ""
        for name, value in sorted(node["inputs"].items()):
            if is_link(value) or not prompt_candidate(name, class_type, title):
                continue
            descriptor = (name + " " + title).lower()
            candidate_score = (
                -100
                if any(
                    marker in descriptor
                    for marker in ("negative", "neg prompt", "负面", "反向", "负向")
                )
                else 0
            )
            candidate_score += (
                20 if any(marker in descriptor for marker in ("positive", "正面", "正向")) else 0
            )
            candidate_score += 5 if "cliptextencode" in class_type else 0
            candidate_score += (
                2
                if field_key(name)
                in {"text", "prompt", "positiveprompt", "positive", "caption", "description"}
                else 0
            )
            if best is None or candidate_score > score:
                best, score = (
                    {"nodeId": node_id, "fieldName": name, "fieldValue": prompt},
                    candidate_score,
                )
    return [best] if best is not None and prompt.strip() else []


def _media_field_type(name: str, value, class_type: str) -> str:
    key, class_key = field_key(name), field_key(class_type)
    if "mask" in key:
        return "IMAGE"
    for kind in ("image", "video", "audio"):
        if "load" + kind in class_key and key in {"file", "path"}:
            return kind.upper()
    scalar_type = (
        "BOOLEAN" if type(value) is bool else "NUMBER" if type(value) in {int, float} else "TEXT"
    )
    if named_source(name) or type(value) in {bool, int, float}:
        return scalar_type
    for kind in ("image", "video", "audio"):
        names = {
            prefix + kind
            for prefix in (
                "",
                "input",
                "reference",
                "ref",
                "source",
                "init",
                "start",
                "end",
                "first",
                "last",
                "control",
                "controlnet",
                "style",
                "subject",
            )
        }
        names |= {kind + suffix for suffix in ("file", "path", "filename", "upload")}
        if kind == "image":
            names |= {"firstframe", "lastframe", "startframe", "endframe"}
        if key in names:
            return kind.upper()
    media_value = value[0] if isinstance(value, list) and value else value
    text = re.split(r"[?#]", scalar(media_value).strip().lower(), maxsplit=1)[0]
    for kind, extensions in (
        ("IMAGE", (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif")),
        ("VIDEO", (".mp4", ".webm", ".mov", ".m4v", ".mkv")),
        ("AUDIO", (".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")),
    ):
        if text.endswith(extensions):
            return kind
    return "TEXT"


def seed_field(name: str) -> bool:
    key = field_key(name)
    return key.endswith("seed") or "noiseseed" in key
