"""Tests for the iframe-wrapper redirect helper."""

from __future__ import annotations

from autoapply.execute.submitter.phases.iframe_resolve import (
    _is_known_wrapper_host,
    _looks_like_form_frame,
    resolve_form_iframe,
)


def test_pattern_matches_greenhouse_embed():
    assert _looks_like_form_frame(
        "https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8477773002"
    )


def test_pattern_matches_old_greenhouse_embed():
    assert _looks_like_form_frame(
        "https://boards.greenhouse.io/embed/?token=123"
    )


def test_pattern_rejects_main_greenhouse_jobs_page():
    """The non-embed Greenhouse jobs page is NOT an iframe wrapper."""
    assert not _looks_like_form_frame(
        "https://job-boards.greenhouse.io/smartsheet/jobs/7599444"
    )


def test_pattern_rejects_blank_and_unrelated():
    assert not _looks_like_form_frame("about:blank")
    assert not _looks_like_form_frame("")
    assert not _looks_like_form_frame(
        "https://content.googleapis.com/static/proxy.html"
    )
    assert not _looks_like_form_frame(
        "https://www.recaptcha.net/recaptcha/enterprise/anchor"
    )


# ── resolve_form_iframe with stub Page ──────────────────────────────


class _StubFrame:
    def __init__(self, url: str):
        self.url = url


class _StubPage:
    def __init__(self, frames: list[_StubFrame], url: str = ""):
        self.frames = frames
        self.url = url

    def wait_for_selector(self, *_a, **_k):
        # No-op — tests don't have an actual iframe element to wait for.
        return None


# ── _is_known_wrapper_host ──────────────────────────────────────────


def test_wrapper_host_detection():
    # Direct ATS hosts → not wrappers
    assert not _is_known_wrapper_host("https://boards.greenhouse.io/lyft/jobs/x")
    assert not _is_known_wrapper_host("https://job-boards.greenhouse.io/x/jobs/y")
    assert not _is_known_wrapper_host("https://jobs.lever.co/example/abc/apply")
    assert not _is_known_wrapper_host("https://jobs.ashbyhq.com/x/y/application")
    # Custom domains → potential wrappers
    assert _is_known_wrapper_host("https://app.careerpuck.com/job-board/lyft/job/x")
    assert _is_known_wrapper_host("https://careers.example.com/jobs/123")
    # Empty / blank → false (don't waste time polling)
    assert not _is_known_wrapper_host("")


# ── resolve_form_iframe ─────────────────────────────────────────────


def test_resolve_returns_embed_url_when_frame_present():
    page = _StubPage(
        frames=[
            _StubFrame("https://app.careerpuck.com/job-board/lyft/job/8477773002"),
            _StubFrame("https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8477773002"),
            _StubFrame("about:blank"),
        ],
        url="https://app.careerpuck.com/job-board/lyft/job/8477773002",
    )
    out = resolve_form_iframe(page, settle_seconds=0.1)
    assert out == (
        "https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8477773002"
    )


def test_resolve_returns_none_when_no_embed_frame():
    page = _StubPage(
        frames=[
            _StubFrame("https://boards.greenhouse.io/lyft/jobs/8477773002"),
            _StubFrame("about:blank"),
        ],
        url="https://boards.greenhouse.io/lyft/jobs/8477773002",
    )
    out = resolve_form_iframe(page, settle_seconds=0.1)
    assert out is None


def test_resolve_short_circuits_on_direct_ats_host():
    """When already on a direct ATS host (boards.greenhouse.io etc)
    the function must NOT spend the full settle budget polling for
    an iframe that's never going to appear."""
    import time
    page = _StubPage(
        frames=[],
        url="https://boards.greenhouse.io/lyft/jobs/8477773002",
    )
    t0 = time.monotonic()
    out = resolve_form_iframe(page, settle_seconds=10.0)
    elapsed = time.monotonic() - t0
    assert out is None
    assert elapsed < 0.5, f"short-circuit should be near-instant, took {elapsed:.2f}s"


def test_resolve_returns_none_on_empty_frames_list():
    page = _StubPage(
        frames=[],
        url="https://app.careerpuck.com/job-board/lyft/job/x",
    )
    out = resolve_form_iframe(page, settle_seconds=0.1)
    assert out is None


def test_resolve_handles_evolving_frames():
    """Frames may take a moment to populate. Stub that returns empty
    on the first poll, then the embed frame on the second."""

    class _EvolvingPage:
        def __init__(self):
            self.calls = 0
            self.url = "https://app.careerpuck.com/job-board/acme/job/x"

        @property
        def frames(self):
            self.calls += 1
            if self.calls < 2:
                return []
            return [
                _StubFrame(
                    "https://job-boards.greenhouse.io/embed/job_app?for=acme"
                )
            ]

        def wait_for_selector(self, *_a, **_k):
            return None

    out = resolve_form_iframe(_EvolvingPage(), settle_seconds=2.0)
    assert out is not None
    assert "embed/job_app" in out
