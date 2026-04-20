"""Shared dataclass for the Stage-2 DOM pipeline.

``_DomField`` is the in-flight record for one empty required field we
intend to fill via the batched LLM. It's produced by :mod:`.scrape`,
enriched with options by :mod:`.options`, potentially pre-resolved by
:mod:`.resolve`, and finally filled by :mod:`.fill`.

The leading underscore is kept for backwards compatibility — earlier
tests and callers reach into ``dom_batch._DomField``. A public rename
can happen in a later phase once the test-import audit is done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Field kinds we're willing to have the LLM fill. Checkboxes and radios
# are left out of the input-type whitelist (they have dedicated branches
# in the scrape / fill code); everything else is a text-like input.
_SUPPORTED_INPUT_TYPES = {"text", "email", "tel", "search", "url", "number", ""}


@dataclass
class _DomField:
    """Cached info about one DOM element that needs filling."""

    element_id: str            # dedup key — the [id] or [name] attr
    label: str                 # human-readable label for the LLM prompt
    kind: str                  # "select" | "multi_select" | "text" | "textarea"
    options: list[str]         # non-empty only for kind=="select"/"multi_select"
    locator: Any               # Playwright Locator for the actual input

    # For ``kind == "multi_select"``: a list of Playwright Locators for
    # the individual checkbox inputs. The scraper fills these in when
    # detecting a required checkbox group (Greenhouse's ``race``,
    # US-states lists, "locations you're committed to" etc.).
    # Left None for non-checkbox kinds.
    checkbox_locators: list[Any] | None = None
