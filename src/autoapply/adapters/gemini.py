"""Gemini Flash adapter — model cascade + SDK wrapper.

This module is the only place in the codebase that imports ``google.genai``
directly. Everything else uses the narrow interface here. When a Gemini
model is deprecated (happens every ~6 months), this is the single file
to edit.

Public surface:

  * :data:`MODEL_CASCADE` — ordered tuple of model IDs to try.
  * :data:`_CASCADE_ERROR_MARKERS` — strings in an error message that
    indicate "try the next model" rather than "bail out". (Kept
    underscore-prefixed for backcompat with existing callers / tests;
    the name is historical.)
  * :func:`call_with_cascade` — try each model until one succeeds, or
    all are exhausted. Returns a parsed answer dict; callers supply
    the parse function so this module stays free of answer-schema
    knowledge.
  * :func:`is_cascade_error` — classifier for the error-type boundary.

Design note: this module doesn't know about ``BatchQuestion`` /
``BatchAnswer`` / prompts. It accepts a prompt string and a parse
callback, and returns whatever the parser returns. That keeps the
Gemini SDK knowledge isolated from answer-schema evolution.
"""

from __future__ import annotations

import logging
from typing import Any, Callable


log = logging.getLogger(__name__)


# ─── Model cascade ──────────────────────────────────────────────────────
#
# User-specified order: try preview / lightweight models first so the
# 2.5 Flash-Lite 1000-RPD workhorse is preserved for when everything else
# has been quota-exhausted. The 2.5 Flash (non-Lite) entry is the last
# resort — it has the strongest reasoning but only 250 RPD.
MODEL_CASCADE: tuple[str, ...] = (
    "gemini-3.1-flash-lite",   # preview, restrictive
    "gemini-2.5-flash-lite",   # 1000 RPD free tier
    "gemini-3-flash",          # preview, restrictive
    "gemini-2.5-flash",        # 250 RPD free tier, strongest reasoning
)


# Quota / rate-limit / availability errors that should trigger a cascade
# fallback to the next model. Anything else (auth, malformed request,
# server 5xx retry-worthy) surfaces to the caller unchanged.
_CASCADE_ERROR_MARKERS: tuple[str, ...] = (
    "RESOURCE_EXHAUSTED",
    "429",
    "quota",
    "Quota",
    "rate limit",
    "rate_limit",
    "rateLimit",
    "exceeded",
    "not found",   # fallback for preview-model 404s
    "NOT_FOUND",
    "permission",  # region / billing gating
    "PERMISSION_DENIED",
)


def is_cascade_error(msg: str) -> bool:
    """True when an error message indicates "try the next model"."""
    return any(marker in msg for marker in _CASCADE_ERROR_MARKERS)


# Public alias kept for historical callers (llm_fallback.py imports this
# name directly). Private-prefix because the rest of the codebase only
# uses it internally; external consumers use ``is_cascade_error``.
_is_cascade_error = is_cascade_error


CallTrace = list[dict[str, Any]]
ParseFn = Callable[[str], Any]


def call_with_cascade(
    *,
    prompt: str,
    api_key: str,
    parse: ParseFn,
    models: tuple[str, ...] = MODEL_CASCADE,
    temperature: float = 0.2,
) -> tuple[Any, str, str, CallTrace]:
    """Try each model in ``models`` until one produces a usable response.

    Args:
      prompt: the full prompt text (already sanitized / wrapped by caller).
      api_key: the Gemini API key.
      parse: callable ``(text) -> Any``. Called with the model's raw
        response text; should return a non-falsy value on success and a
        falsy value (empty dict / list / None) on "response parsed but
        nothing usable" — in which case we try the next model.
      models: ordered tuple of model IDs. Defaults to :data:`MODEL_CASCADE`.
      temperature: low by default (deterministic picks, not creative
        variation).

    Returns ``(result, model_used, error, trace)``:
      * ``result`` — the parse callback's return value, or an empty value
        (`{}` / `[]` / None) on failure.
      * ``model_used`` — the model that succeeded, or "" on failure.
      * ``error`` — top-level failure message if ALL models exhausted
        (or immediate bail on a non-cascade error); "" on success.
      * ``trace`` — per-attempt log ``[{"model": ..., "ok"|"error": ...}]``.

    Uses the stable ``v1`` API. The 3.x preview models still 404 on v1
    as of April 2026 (only available via ``v1alpha``), but the cascade
    rolls forward to 2.5 Flash-Lite; when 3.x graduates to v1 stable,
    no code change is needed here.
    """
    try:
        from google import genai  # type: ignore[import]
    except ImportError:
        return (
            None,
            "",
            "google-genai SDK not installed (pip install google-genai)",
            [],
        )

    client = genai.Client(
        api_key=api_key,
        http_options={"api_version": "v1"},
    )
    trace: CallTrace = []

    for model_id in models:
        attempt: dict[str, Any] = {"model": model_id}
        try:
            # v1 doesn't support response_mime_type in generate_content
            # config (only v1beta does). Caller is expected to include
            # explicit JSON-output instructions in the prompt and tolerate
            # markdown-fence wrappers in the parse callback.
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config={"temperature": temperature},
            )
            text = getattr(response, "text", "") or ""
            if not text.strip():
                attempt["error"] = "empty response"
                trace.append(attempt)
                continue

            result = parse(text)
            if not result:
                attempt["error"] = "response parsed to empty result"
                trace.append(attempt)
                continue

            attempt["ok"] = True
            # Best-effort result-size tag (only dicts / lists have __len__).
            try:
                attempt["result_count"] = len(result)  # type: ignore[arg-type]
            except TypeError:
                pass
            trace.append(attempt)
            return result, model_id, "", trace

        except Exception as exc:
            msg = str(exc)
            attempt["error"] = msg[:300]
            trace.append(attempt)
            if is_cascade_error(msg):
                log.warning(
                    "gemini: %s returned cascade-triggering error; trying next: %s",
                    model_id, msg[:160],
                )
                continue
            # Non-cascade error (auth, malformed request, unrecoverable
            # server error) — surface immediately.
            log.error("gemini: %s terminal error: %s", model_id, msg[:200])
            return None, "", f"{model_id}: {msg[:200]}", trace

    return (
        None,
        "",
        "all models in cascade exhausted (rate-limited or unavailable)",
        trace,
    )
