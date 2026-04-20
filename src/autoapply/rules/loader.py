"""Rule-file + prompt-file loaders.

Both loaders cache per-process (LRU) so hot paths that reach for a rule
list don't re-parse YAML on every call. Tests can call
:func:`clear_cache` to reload after mutating a fixture file.

Locations:
  * Rule YAMLs: ``<repo_root>/state/rules/<name>.yml``
  * Prompts:    ``<repo_root>/prompts/<name>.md``

Rule files are free-form YAML; consumers know their own schema. The
loader only asserts "file exists and parses as YAML". A consumer that
expects a specific shape should validate on its side (and preferably at
module-import time so a malformed rule file fails loud and early rather
than at apply time).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


def _rules_dir() -> Path:
    from autoapply.config import get_settings

    return get_settings().state_dir / "rules"


def _prompts_dir() -> Path:
    from autoapply.config import get_settings

    return get_settings().prompts_dir


@lru_cache(maxsize=64)
def load_rules(name: str) -> dict[str, Any]:
    """Load ``state/rules/<name>.yml`` and return the parsed top-level dict.

    Raises :class:`FileNotFoundError` if the file is missing — that's a
    programming error (a consumer referencing a rule file that was
    renamed / deleted), not a runtime condition to tolerate.
    """
    path = _rules_dir() / f"{name}.yml"
    if not path.is_file():
        raise FileNotFoundError(f"rule file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"rule file {path} must parse to a dict at top level, got {type(data).__name__}"
        )
    return data


@lru_cache(maxsize=32)
def load_prompt(name: str) -> str:
    """Load ``prompts/<name>.md`` and return its raw text.

    Prompts are stored as plain markdown (with optional ``{placeholder}``
    fields the caller will ``str.format`` against). We do not interpret
    any markdown here; the LLM gets the string verbatim.
    """
    path = _prompts_dir() / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf-8")


def clear_cache() -> None:
    """Drop cached rules/prompts (for tests that mutate fixture files)."""
    load_rules.cache_clear()
    load_prompt.cache_clear()
