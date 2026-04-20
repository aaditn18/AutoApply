"""External-system adapters — thin wrappers over third-party SDKs.

This package is the **IO boundary**: every module here imports from an
external SDK (Google Generative AI, Playwright, IMAP, ...) and exposes
a narrow Pythonic interface to the rest of AutoApply. Nothing outside
``adapters/`` should import the underlying SDK directly.

Why keep this layer thin:
  * Swapping an SDK (or its API version) is a one-file change.
  * Unit tests mock the adapter, not the SDK — no more wrestling with
    ``google.genai`` internals in test fixtures.
  * The rest of the codebase stays pure Python / Pydantic — no
    SDK-specific types leak into business logic.

Today this houses:
  * :mod:`gemini` — Gemini Flash cascade caller.

Future adapters (Playwright, IMAP, Greenhouse REST, etc.) will move
here as they're split from their current homes.
"""
