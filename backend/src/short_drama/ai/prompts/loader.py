"""Load UTF-8 prompt resources from the installed backend package."""

from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=None)
def load_prompt(relative_path: str) -> str:
    parts = relative_path.replace("\\", "/").split("/")
    if not relative_path or any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"Invalid prompt template path: {relative_path}")
    try:
        text = (
            resources.files("short_drama.ai.prompts")
            .joinpath(*parts)
            .read_text(encoding="utf-8")
            .strip()
        )
    except (FileNotFoundError, IsADirectoryError, ModuleNotFoundError) as exc:
        raise RuntimeError(f"Prompt template not found: {relative_path}") from exc
    if not text:
        raise RuntimeError(f"Empty prompt template: {relative_path}")
    return text
