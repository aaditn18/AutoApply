"""API-sourced field fill phase.

``submit_greenhouse`` / ``submit_lever`` pre-resolve every field listed
in the board's API ``/questions`` response using the two-phase resolver
(:func:`autoapply.execute.standard_fields.resolve_all_batched`). The
result is a flat ``name → value`` dict handed to this phase.

For each ``(name, value)`` pair, we call
:func:`autoapply.execute.submitter.field_fill.fill_field` which
dispatches to the right Playwright filler (text / select / combobox /
checkbox / radio). Two failure modes are distinguished:

- ``ValueError("no element found for ...")`` — the field is absent on
  THIS tenant's DOM (common for pre-augmented location atoms on
  tenants that don't render a City field). DEBUG-logged only; not a
  real error.
- Anything else — appended to ``field_errors`` as ``fill:<name>:<cls>``
  so the caller can surface what went wrong.
"""

from __future__ import annotations

import logging
from typing import Any

from ..field_fill import fill_field
from ..util import jitter


log = logging.getLogger(__name__)


def fill_api_fields(
    page: Any,
    data: dict[str, Any],
    field_errors: list[str],
) -> None:
    """Iterate ``data`` and fill each non-empty entry.

    ``data`` is expected to be a flat ``{machine_name: value}`` dict;
    the resolver has already done classifier + profile + bank + batch
    LLM resolution upstream. Any value we receive here is a final
    answer ready to type.
    """
    for name, value in data.items():
        if not value:
            continue
        try:
            fill_field(page, name, str(value))
            jitter(0.1, 0.4)
        except ValueError as exc:
            msg = str(exc)
            if "no element found" in msg:
                # Field absent on this tenant's DOM — expected for the
                # pre-augmented SPA-injected atoms (city/state/zip/...).
                log.debug("fill skipped (field absent): %r", name)
            else:
                log.debug("fill error field=%r: %s", name, exc)
                field_errors.append(f"fill:{name}:ValueError")
        except Exception as exc:
            log.debug("fill error field=%r: %s", name, exc)
            field_errors.append(f"fill:{name}:{type(exc).__name__}")
