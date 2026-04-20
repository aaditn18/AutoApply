"""Field resolution facade — public API over the :mod:`.resolution` pipeline.

This module was a 575-LOC implementation file; after the Phase-5
refactor the heavy lifting lives under :mod:`autoapply.execute.resolution`.
This file now exposes:

- **Dataclasses / exceptions** — :class:`UnresolvedField`,
  :class:`ResolvedField`, :class:`FieldSpec`, and the
  :data:`ClassifyFn` type alias. Kept here because dozens of callers
  (applicators, tests, the review queue) import them from
  ``autoapply.execute.standard_fields``.
- **Public functions** — :func:`resolve_field`, :func:`resolve_all`,
  :func:`resolve_all_batched`. These are thin re-exports from the
  :mod:`.resolution` submodules.

See :mod:`.resolution.__init__` for the pipeline layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from autoapply.answers import classifier as classifier_mod
from autoapply.answers.types import QuestionType


ClassifyFn = Callable[[str], classifier_mod.ClassifiedQuestion]


class UnresolvedField(Exception):
    """Raised when a required field has no deterministic answer."""

    def __init__(self, label: str, name: str = "", reason: str = ""):
        self.label = label
        self.name = name
        self.reason = reason
        super().__init__(
            f"UnresolvedField label={label!r} name={name!r} reason={reason}"
        )


@dataclass
class ResolvedField:
    """One form field with a resolved answer.

    ``source`` is one of:
      ``"machine_key"`` | ``"profile"`` | ``"bank"`` |
      ``"classifier+bank"`` | ``"llm_required"`` | ``"review_required"`` |
      ``"none"`` | ``"llm_answer"`` | ``"llm_batch:<sub-source>"``.
    """

    name: str
    label: str
    value: str
    source: str
    question_type: QuestionType | None = None
    requires_llm: bool = False
    requires_review: bool = False


@dataclass
class FieldSpec:
    """A single ATS form field as reported by the board API."""

    name: str
    label: str
    required: bool = False
    kind: str = "text"  # text | textarea | select | file | multi_select | checkbox
    options: list[str] = field(default_factory=list)


# ── Public pipeline entry points ───────────────────────────────────────
# Deferred imports so the dataclasses above are fully defined before the
# resolution submodules import them back.

from autoapply.execute.resolution.phase1 import resolve_all, resolve_field  # noqa: E402
from autoapply.execute.resolution.orchestrator import resolve_all_batched  # noqa: E402


# ── Back-compat: names some tests / external modules still reach for ──
# Private helpers that used to live in this file. New code should import
# from the resolution submodules directly.

from autoapply.execute.resolution.machine_key import (  # noqa: E402
    _MACHINE_KEY_RULES,
    _match_machine_key,
    _profile_value,
)
from autoapply.execute.resolution.options_snap import (  # noqa: E402
    _OTHER_MARKERS,
    _US_PERSON_MARKERS,
    _snap_to_option,
)
from autoapply.execute.resolution.batch_builder import (  # noqa: E402
    _value_matches_option,
)
from autoapply.execute.resolution.orchestrator import (  # noqa: E402
    _load_bank_yaml_text,
)


# ``re`` import retained for downstream modules that might pattern-match
# against ``standard_fields`` globals. Not strictly needed for this facade
# but removing it has historically broken one-off consumers.
_ = re  # noqa: F841


__all__ = [
    # Public types
    "UnresolvedField",
    "ResolvedField",
    "FieldSpec",
    "ClassifyFn",
    # Public functions
    "resolve_field",
    "resolve_all",
    "resolve_all_batched",
    # Back-compat internals
    "_MACHINE_KEY_RULES",
    "_match_machine_key",
    "_profile_value",
    "_snap_to_option",
    "_US_PERSON_MARKERS",
    "_OTHER_MARKERS",
    "_value_matches_option",
    "_load_bank_yaml_text",
]
