"""Compose scene-specific system prompts from reusable templates."""

from collections.abc import Iterable

from .loader import load_prompt

_ASSET_RULES = {
    "character": "script_assets/character_rules.md",
    "scene": "script_assets/scene_rules.md",
    "prop": "script_assets/prop_rules.md",
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
            "common/source_boundary.md",
            "script_assets/output_contract.md",
            "common/structured_output.md",
        )
    raise ValueError("Unsupported text scene")
