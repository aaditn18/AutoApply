"""Tests for the submission pipeline phase modules.

The phases are Playwright-bound, so most of their real behavior is
exercised through the live submission path. These tests lock in the
**composition contract**: each phase module imports cleanly, exposes
the expected public callables, and — where possible without a real
browser — behaves correctly on edge cases (missing context, empty
inputs, exception paths).

If any of these tests fail, the driver's composition is broken and
`submit_form` won't work even though its own unit tests may pass.
"""

from __future__ import annotations

import pytest


# ─── Import-surface contract ────────────────────────────────────────────


def test_browser_phase_exports():
    from autoapply.execute.submitter.phases import browser

    assert callable(browser.launch_browser_context)
    # UA pool was loaded from state/rules/browser_pool.yml.
    assert len(browser.USER_AGENTS) >= 3
    assert all("Chrome/" in ua for ua in browser.USER_AGENTS)


def test_upload_phase_exports():
    from autoapply.execute.submitter.phases import upload

    assert callable(upload.upload_files)
    assert callable(upload.wait_for_resume_analysis)


def test_api_fill_phase_exports():
    from autoapply.execute.submitter.phases import api_fill

    assert callable(api_fill.fill_api_fields)


def test_stage2_phase_exports():
    from autoapply.execute.submitter.phases import stage2

    assert callable(stage2.run_stage2_batch)


def test_verification_phase_exports():
    from autoapply.execute.submitter.phases import verification

    assert callable(verification.handle_email_verification)


def test_submit_click_phase_exports():
    from autoapply.execute.submitter.phases import submit_click

    assert callable(submit_click.click_submit_and_handle_captcha)


def test_verify_phase_exports():
    from autoapply.execute.submitter.phases import verify

    assert callable(verify.check_submit_success)


def test_driver_re_exports_user_agents_for_backcompat():
    # driver.USER_AGENTS is deliberately re-exported so callers that
    # import directly from ``driver`` (legacy code paths) don't break.
    from autoapply.execute.submitter.driver import USER_AGENTS, submit_form

    assert callable(submit_form)
    assert len(USER_AGENTS) >= 3


# ─── Behavior tests (non-Playwright paths) ──────────────────────────────


def test_run_stage2_batch_returns_empty_when_no_llm_context():
    """llm_context is None → skip entirely, return empty dict."""
    from autoapply.execute.submitter.phases.stage2 import run_stage2_batch

    audit = run_stage2_batch(page=None, llm_context=None, already_filled_keys=set())
    assert audit == {}


def test_run_stage2_batch_swallows_dom_batch_exceptions(monkeypatch):
    """A dom_batch crash must NOT propagate — worst case we lose stage-2."""
    from autoapply.execute.submitter.phases import stage2

    def _raise(*_a, **_kw):
        raise RuntimeError("simulated DOM crash")

    monkeypatch.setattr(stage2, "batch_resolve_dom_fields", _raise)

    audit = stage2.run_stage2_batch(
        page=object(),
        llm_context={"profile": None, "track": "swe"},
        already_filled_keys=set(),
    )
    # Got back a well-formed empty audit; no exception.
    assert audit == {}


def test_handle_email_verification_raises_without_credentials():
    from autoapply.execute.submitter.phases.verification import (
        handle_email_verification,
    )
    from autoapply.execute.submitter import SubmitFailed

    with pytest.raises(SubmitFailed, match="IMAP credentials"):
        handle_email_verification(
            page=object(),
            imap_server="",
            imap_port=993,
            imap_email="",
            imap_password="",
            imap_code_timeout=10,
        )


def test_handle_email_verification_raises_when_code_not_found(monkeypatch):
    from autoapply.execute.submitter.phases import verification
    from autoapply.execute.submitter import SubmitFailed

    monkeypatch.setattr(
        verification, "fetch_imap_verification_code",
        lambda **_kw: None,  # code never arrives
    )

    with pytest.raises(SubmitFailed, match="code not found"):
        verification.handle_email_verification(
            page=object(),
            imap_server="imap.example.com",
            imap_port=993,
            imap_email="a@b.c",
            imap_password="pw",
            imap_code_timeout=5,
        )


def test_upload_files_records_missing_file_error(tmp_path):
    """A missing path (not cover_letter text) → field_errors entry,
    NOT a crash. Keeps other uploads proceeding."""
    from autoapply.execute.submitter.phases.upload import upload_files

    field_errors: list[str] = []
    upload_files(
        page=None,  # never consulted — missing-file check short-circuits
        files={"resume": str(tmp_path / "does_not_exist.pdf")},
        field_errors=field_errors,
    )
    assert field_errors == ["missing_file:resume"]


def test_upload_files_skips_empty_values():
    """files = {name: ''} → no attempt, no error entry."""
    from autoapply.execute.submitter.phases.upload import upload_files

    field_errors: list[str] = []
    upload_files(page=None, files={"resume": ""}, field_errors=field_errors)
    assert field_errors == []
