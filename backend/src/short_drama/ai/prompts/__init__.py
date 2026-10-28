"""Package-backed business prompt templates."""

from short_drama.ai.prompts.loader import load_prompt
from short_drama.ai.prompts.registry import system_prompt

__all__ = ["load_prompt", "system_prompt"]
