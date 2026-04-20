"""Field-resolution pipeline — machine-key → classifier → bank → LLM batch.

This package contains the internal phases of the two-phase resolver
that used to live in ``standard_fields.py``. The public-facing module
``standard_fields`` still exposes the same names (``resolve_field``,
``resolve_all``, ``resolve_all_batched``, ``FieldSpec``, etc.) — it
now delegates to these submodules.

Phases
------

- :mod:`machine_key`   — machine-name → Profile attribute (first_name,
                         email, phone, ...). Fastest path; zero LLM.
- :mod:`options_snap`  — normalize a resolved value to a verbatim option
                         string for select fields. Handles ITAR/EAR
                         export-control dropdown fallback.
- :mod:`phase1`        — deterministic per-field resolution:
                         machine_key → classifier → bank/profile →
                         per-field LLM (select-only).
- :mod:`batch_builder` — decide which Phase-1 results need the batch
                         LLM (required + option-mismatch / needs_llm /
                         needs_review / empty).
- :mod:`backfill`      — apply LLM answers back onto the Phase-1
                         ResolvedField list, promoting unresolved
                         fields that the LLM answered.
- :mod:`orchestrator`  — :func:`resolve_all_batched` — the composition.

The package is intentionally close to a pipeline: each module's
outputs feed the next. Shared dataclasses (``ResolvedField``,
``FieldSpec``, ``UnresolvedField``, ``ClassifyFn``) live in the public
``standard_fields`` module and are imported here — that keeps the
widely-imported names stable for callers.
"""
