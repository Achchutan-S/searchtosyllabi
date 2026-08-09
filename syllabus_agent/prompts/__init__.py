"""Loads prompt text bundled as plain files next to this module, keeping prompt
copy out of pipeline stage code so it can be edited/reviewed independently of
the code that calls it.

Every stage prompt is composed on top of `base.txt`, which carries the shared
output discipline (raw JSON, no fences, no markdown inside values). Stage
prompts therefore only need to describe their own task and schema.
"""

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent

BASE_PROMPT_NAME = "base"


@lru_cache
def _read_prompt_file(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.txt").read_text().strip()


@lru_cache
def load_prompt(name: str, *, with_base: bool = True) -> str:
    """Return a stage prompt, prefixed by the shared base prompt by default."""
    prompt = _read_prompt_file(name)
    if not with_base or name == BASE_PROMPT_NAME:
        return prompt
    return f"{_read_prompt_file(BASE_PROMPT_NAME)}\n\n{prompt}"
