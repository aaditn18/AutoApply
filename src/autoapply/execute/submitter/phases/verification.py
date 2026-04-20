"""Email OTP verification phase.

Some Greenhouse tenants send a one-time code to the applicant's email
between submit and confirmation. This phase polls IMAP for the code and
types it into the verification form.

The path only runs when:
  (a) we're not already on a success page, AND
  (b) the post-submit page has an actual code input element (not just
      confirmation-email body copy that happens to mention "email").

Both checks are the caller's responsibility — this module assumes we
should proceed.
"""

from __future__ import annotations

import logging
from typing import Any

from .. import SubmitFailed
from ..imap_otp import enter_verification_code, fetch_imap_verification_code
from ..util import jitter


log = logging.getLogger(__name__)


def handle_email_verification(
    page: Any,
    *,
    imap_server: str,
    imap_port: int,
    imap_email: str,
    imap_password: str,
    imap_code_timeout: int,
) -> None:
    """Fetch the OTP via IMAP and type it into the page.

    Raises :class:`SubmitFailed` if credentials are unset or the code
    doesn't arrive within ``imap_code_timeout`` seconds. Raising is
    correct here — we've already clicked submit; without the OTP the
    application is stuck in "awaiting verification" state and will be
    rejected upstream by the success detector.
    """
    log.info("email verification required; fetching code via IMAP …")
    if not (imap_email and imap_password):
        raise SubmitFailed(
            "email verification required but IMAP credentials "
            "not configured (set IMAP_EMAIL + IMAP_PASSWORD)"
        )

    code = fetch_imap_verification_code(
        imap_server=imap_server,
        imap_port=imap_port,
        imap_email=imap_email,
        imap_password=imap_password,
        timeout=imap_code_timeout,
    )
    if not code:
        raise SubmitFailed(
            "email verification required but code not found "
            f"in inbox within {imap_code_timeout}s"
        )

    log.info("verification code fetched: %s", code)
    enter_verification_code(page, code)

    # Wait for result after code entry. SPA may not trigger
    # ``networkidle`` reliably; swallow timeout and rely on the
    # downstream success detector.
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        pass
    jitter(1.5, 3.0)
