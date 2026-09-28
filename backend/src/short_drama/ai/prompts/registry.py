"""Compose scene-specific system prompts from reusable templates."""

from collections.abc import Iterable

from .loader import load_prompt

_ASSET_RULES = {
    "character": "script_assets/character_rules.md",
    "scene": "script_assets/scene_rules.md",
    "prop": "script_assets/prop_rules.md",
}

PROMPT_VERSIONS = {
    "novel_script": "novel-script-v2",
    "script_assets": "script-assets-v2",
    "script_shots": "script-shots-v2",
    "asset_image": "asset-image-v2",
    "shot_image": "shot-image-v2",
    "shot_video": "shot-video-v2",
}

_SHOT_LAYOUTS = {
    "four": "四宫格，严格 2×2，按顺序呈现本镜连续动作。",
    "five": "五宫格，严格上二下三排列，呈现本镜连续动作。",
    "nine": "九宫格，严格三行三列（3×3），呈现本镜连续动作。",
}


def _parts(*paths: str) -> str:
    return "\n\n".join(load_prompt(path) for path in paths)


def system_prompt(scene: str, *, kinds: Iterable[str] = ()) -> str:
    if scene == "novel_script":
        return _parts(
            "novel_script/system.md",
            "common/source_boundary.md",
        )
    if scene == "script_shots":
        return _parts(
            "script_shots/system.md",
            "script_shots/segmentation_rules.md",
            "script_shots/duration_rules.md",
            "common/source_boundary.md",
            "script_shots/output_contract.md",
            "common/structured_output.md",
        )
    if scene == "script_assets":
        selected = list(dict.fromkeys(kinds or _ASSET_RULES))
        unknown = [kind for kind in selected if kind not in _ASSET_RULES]
        if unknown:
            raise ValueError(f"Unsupported asset kind: {unknown[0]}")
        return _parts(
            "script_assets/system.md",
            *(_ASSET_RULES[kind] for kind in selected),
            *(f"asset_views/{kind}.md" for kind in selected),
            "common/source_boundary.md",
            "script_assets/output_contract.md",
            "common/structured_output.md",
        )
    raise ValueError("Unsupported text scene")


def asset_image_system_prompt(kind: str) -> str:
    if kind not in _ASSET_RULES:
        raise ValueError(f"Unsupported asset kind: {kind}")
    return _parts("asset_image/system.md", f"asset_views/{kind}.md")


def shot_image_system_prompt(layout: str) -> str:
    if layout == "single":
        return _parts("shot_image/system.md", "shot_image/single.md")
    if layout not in _SHOT_LAYOUTS:
        raise ValueError(f"Unsupported shot image layout: {layout}")
    return "\n\n".join(
        [
            load_prompt("shot_image/system.md"),
            f"## 本次布局：{layout}\n\n{_SHOT_LAYOUTS[layout]}",
            load_prompt("shot_image/multi.md"),
        ]
    )


def shot_video_system_prompt() -> str:
    return load_prompt("shot_video/system.md")
