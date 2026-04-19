"""Test-suite-wide fixtures + safety nets.

Hermetic-test policy
--------------------
Tests must NEVER hit the live Gemini API. Two layers of defense:

1. ``_block_live_gemini`` (autouse): monkeypatches
   :func:`autoapply.answers.llm_batch._call_with_cascade` and
   :func:`autoapply.answers.llm_fallback._call_gemini` to raise a loud
   error by default. Tests that want to exercise the LLM path must
   monkeypatch them back to a deterministic stub.

2. ``GEMINI_API_KEY`` is forced to the empty string in the test
   environment, which makes both ``resolve_batch`` and
   ``draft_field_answer`` take the "no API key" short-circuit path even
   if layer 1 is bypassed. Tests that explicitly want to exercise the
   batch path (with a mocked transport) set ``GEMINI_API_KEY`` via
   ``monkeypatch.setenv`` and also patch the transport.
"""

from __future__ import annotations

import os
import pytest


@pytest.fixture(autouse=True)
def _block_live_gemini(monkeypatch):
    """Prevent every test from making a real Gemini API call.

    Monkeypatches ``_call_with_cascade`` and ``_call_gemini`` to raise,
    AND clears ``GEMINI_API_KEY`` from ``os.environ`` + the cached
    :func:`autoapply.config.get_settings` result so modules that read
    the key can't get a live one.
    """
    # Wipe the env var (both sources: os.environ and the cached settings).
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    try:
        import autoapply.config as _cfg
        _cfg._settings = None
    except Exception:
        pass

    # Replace the cascade caller with a stub that returns the "no API
    # key" error path. Tests that want to simulate LLM responses patch
    # this back with their own stub inside the test body.
    def _stub_cascade(prompt, api_key, questions):
        from autoapply.answers.llm_batch import BatchResult
        return BatchResult(
            error="live-LLM call blocked by test fixture",
            cascade_trace=[{"model": "<blocked>"}],
        )

    def _stub_call_gemini(prompt, api_key):
        return None  # tests see "LLM declined"

    monkeypatch.setattr(
        "autoapply.answers.llm_batch._call_with_cascade", _stub_cascade,
        raising=False,
    )
    monkeypatch.setattr(
        "autoapply.answers.llm_fallback._call_gemini", _stub_call_gemini,
        raising=False,
    )
    yield
