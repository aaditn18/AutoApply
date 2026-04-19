"""Post-submit success / failure signal detector.

Decision ladder in :func:`detect_submit_success`:

    1. HARD FAIL — visible error nodes OR explicit failure phrases.
    2. URL signal — path segment or query marker.
    3. Strong text signal — specific phrase + submit button gone.
    4. Medium — submit gone + generic affirmative wording.
    5. Legacy caller-provided fragments (lowest confidence).
"""

from __future__ import annotations

from typing import Any

from .util import inner_text_safe


# Strong application-submission phrases — high confidence that the submit
# actually happened. More specific than the generic "thank you" that can
# appear in pre-submit hero copy.
STRONG_SUCCESS_PHRASES: tuple[str, ...] = (
    "your application has been submitted",
    "your application has been received",
    "your application has been sent",
    "application submitted successfully",
    "successfully submitted your application",
    "we've received your application",
    "we have received your application",
    "thanks for applying",
    "thank you for applying",
    "thank you for your application",
    "thank you for your interest in",   # Loop-style confirmation
    "application complete",
    "your application is complete",
    "application received",
    "we'll be in touch",
    "we will be in touch",
    "we'll review your application",
    "we will review your application",
)

# URL path segments that unambiguously identify a post-submit page.
# Checked against `urlparse(page.url).path`, not the raw URL — prevents
# false positives when the company name happens to contain "thank" or
# "success" in a subdomain / query string.
STRONG_SUCCESS_URL_PATHS: tuple[str, ...] = (
    "/confirmation",
    "/confirm",
    "/thank-you",
    "/thank_you",
    "/thankyou",
    "/thanks",
    "/success",
    "/submitted",
    "/applied",
    "/complete",
    "/received",
    "/post-apply",
    "/post_apply",
)

# Query-string markers some ATSs append on success (e.g. ?confirmation=true).
SUCCESS_QUERY_MARKERS: tuple[str, ...] = (
    "confirmation=true",
    "submitted=true",
    "applied=true",
    "status=success",
    "success=true",
)

# Failure phrases — if any appear in post-submit page text, success is
# disqualified regardless of other signals. Kept deliberately specific to
# avoid false positives from generic "required" / "error" labels that can
# appear anywhere on a live form page.
FAILURE_PHRASES: tuple[str, ...] = (
    "please correct the",
    "please fix the",
    "fix the errors",
    "there were errors",
    "there was an error submitting",
    "there was a problem submitting",
    "could not be submitted",
    "unable to submit",
    "form submission failed",
    "submission failed",
    "please review the form",
    "please complete all required",
    "please complete the required",
    "this field is required",
)


def detect_submit_success(
    page: Any,
    success_url_fragments: tuple[str, ...],
    *,
    submit_selector: str = "",
) -> tuple[bool, str]:
    """Decide whether a form submit succeeded using multiple signals.

    Returns ``(ok, reason)``. ``reason`` is a short diagnostic string
    suitable for logs. The function never raises — on internal error it
    returns ``(False, "detector_error: …")``.
    """
    try:
        from urllib.parse import urlparse

        final_url_full = page.url or ""
        final_url = final_url_full.lower()
        parsed = urlparse(final_url_full)
        url_path = (parsed.path or "").lower()
        url_query = (parsed.query or "").lower()

        try:
            page_text = inner_text_safe(page).lower()
        except Exception:
            page_text = ""

        # ── 1. Hard failure gates ─────────────────────────────────────────
        visible_errors = collect_page_errors(page)
        if visible_errors:
            return False, f"visible_errors: {visible_errors[:200]}"

        for phrase in FAILURE_PHRASES:
            if phrase in page_text:
                return False, f"failure_phrase: {phrase!r}"

        # Helper: is the submit button still visible?
        def _submit_still_visible() -> bool:
            if not submit_selector:
                return False
            try:
                loc = page.locator(submit_selector).first
                return loc.is_visible(timeout=500)
            except Exception:
                return False

        # ── 2. Strong URL signal (dedicated confirmation path) ────────────
        for path_frag in STRONG_SUCCESS_URL_PATHS:
            if path_frag in url_path:
                return True, f"url_path: {path_frag!r}"

        for marker in SUCCESS_QUERY_MARKERS:
            if marker in url_query or marker in final_url:
                return True, f"url_query: {marker!r}"

        # ── 3. Strong page-text signal ────────────────────────────────────
        matched_phrase: str | None = None
        for phrase in STRONG_SUCCESS_PHRASES:
            if phrase in page_text:
                matched_phrase = phrase
                break

        if matched_phrase:
            if _submit_still_visible():
                # Phrase matched but submit button is still there → the
                # text is almost certainly pre-submit hero copy, not a
                # confirmation. Reject to be safe.
                return False, (
                    f"phrase {matched_phrase!r} present but submit "
                    f"button still visible (not a confirmation page)"
                )
            return True, f"phrase: {matched_phrase!r}"

        # ── 4. Medium: submit button gone + affirmative wording ───────────
        if submit_selector and not _submit_still_visible():
            affirmative = (
                "thank you",
                "received",
                "submitted",
                "complete",
                "we'll be in touch",
                "we will be in touch",
            )
            if any(w in page_text for w in affirmative):
                return True, "form_gone_plus_affirmative"

        # ── 5. Legacy caller-provided fragments (lowest confidence) ───────
        # Accept only if the fragment appears in the URL PATH (not raw URL),
        # to avoid matching against ATS-branded query strings / company
        # names that happen to contain "success" or "thank".
        for frag in success_url_fragments:
            if frag and frag in url_path:
                return True, f"legacy_path_fragment: {frag!r}"

        return False, f"no_success_signal; final_url={page.url}"

    except Exception as exc:
        return False, f"detector_error: {type(exc).__name__}: {exc}"


def collect_page_errors(page: Any) -> str:
    """Scrape VISIBLE error messages from the current page.

    Only looks at elements that are actually visible to the user — prevents
    hidden error nodes (e.g., Lever's file-size tooltip that is always in the
    DOM but shown only on hover / trigger) from polluting the error field.
    """
    selectors = [
        ".error:visible",
        ".alert-error:visible",
        ".alert-danger:visible",
        '[class*="error"]:visible',
        ".invalid-feedback:visible",
        '[data-error]:visible',
        ".field-error:visible",
        ".form-error:visible",
    ]
    msgs: list[str] = []
    for sel in selectors:
        try:
            for loc in page.locator(sel).all()[:3]:
                # Double-check visibility via is_visible() in case the CSS
                # :visible pseudo-class isn't supported by all Playwright builds.
                if not loc.is_visible():
                    continue
                txt = loc.inner_text().strip()
                if txt and txt not in msgs:
                    msgs.append(txt[:120])
        except Exception:
            pass
    return "; ".join(msgs[:5])
