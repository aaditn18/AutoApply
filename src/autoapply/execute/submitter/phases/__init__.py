"""Submission pipeline — one module per pipeline phase.

A single Playwright-backed form submission is a linear sequence:

    browser setup → navigate → upload files (+ wait for resume analysis)
    → fill API-sourced fields → fill Lever qualifying cards → Stage-2
    DOM batch (SPA-injected fields via one LLM call) → label fallback
    → pre-submit diagnostics → click submit → post-submit captcha
    handling → email OTP verification → success detect → post-submit
    diagnostics → return outcome

:mod:`autoapply.execute.submitter.driver` is the composition layer: it
imports each phase as a named function and calls them in order. Each
phase in this package owns ONE concern — tests can exercise a phase in
isolation with a Playwright page fixture.

Phases:

- :mod:`browser`       — launch Chromium + context + stealth injection.
- :mod:`upload`        — file uploads and Lever's "resume analysis" wait.
- :mod:`api_fill`      — iterate pre-resolved ``data`` dict, fill fields.
- :mod:`stage2`        — DOM-batch LLM resolver for SPA-injected fields.
- :mod:`verification`  — email OTP fetch + entry.
- :mod:`submit_click`  — click submit + post-submit CAPTCHA handling.
- :mod:`verify`        — success detection + post-submit error capture.
"""
