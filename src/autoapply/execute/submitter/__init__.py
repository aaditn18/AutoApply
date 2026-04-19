"""Internal submitter package — Playwright browser automation for form submit.

Public entry points remain in :mod:`autoapply.execute.playwright_submit`
for backward-compat. This subpackage holds the split implementation:

    util            — jitter, inner_text_safe, strip_html, react_set_value
    field_fill      — fill_field + fill_select/radio/combobox helpers
    file_upload     — upload_file + file_input_selector
    lever_cards     — Lever qualifying-question dynamic resolver
    imap_otp        — email verification code fetching + entry
    success_detect  — post-submit success/failure signal detector
    captcha_detect  — is-there-a-blocking-captcha probe + site-key extractor
    captcha_retry   — solver dispatcher + token-injection retry
    diagnostics     — pre/post-submit logging dumps (Lever-scoped)
    driver          — the `_submit_form` orchestrator + browser setup

Exceptions live here so every module that needs to raise/catch them shares
one class definition.
"""

from __future__ import annotations


class CaptchaDetected(Exception):
    """A CAPTCHA wall was encountered; route application to the review queue."""


class SubmitFailed(Exception):
    """Unrecoverable error during browser navigation, form fill, or submit."""


__all__ = ["CaptchaDetected", "SubmitFailed"]
