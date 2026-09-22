"""Package-backed business prompt templates."""

from .loader import load_prompt
from .registry import system_prompt

__all__ = ["load_prompt", "system_prompt"]
