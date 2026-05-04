"""Tests for the iframe-wrapper redirect helper."""

from __future__ import annotations

from autoapply.execute.submitter.phases.iframe_resolve import (
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
    def __init__(self, frames: list[_StubFrame]):
        self.frames = frames


def test_resolve_returns_embed_url_when_frame_present():
    page = _StubPage(
        [
            _StubFrame("https://app.careerpuck.com/job-board/lyft/job/8477773002"),
            _StubFrame("https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8477773002"),
            _StubFrame("about:blank"),
        ]
    )
    out = resolve_form_iframe(page, settle_seconds=0.1)
    assert out == (
        "https://job-boards.greenhouse.io/embed/job_app?for=lyft&token=8477773002"
    )


def test_resolve_returns_none_when_no_embed_frame():
    page = _StubPage(
        [
            _StubFrame("https://boards.greenhouse.io/lyft/jobs/8477773002"),
            _StubFrame("about:blank"),
        ]
    )
    out = resolve_form_iframe(page, settle_seconds=0.1)
    assert out is None


def test_resolve_returns_none_on_empty_frames_list():
    page = _StubPage([])
    out = resolve_form_iframe(page, settle_seconds=0.1)
    assert out is None


def test_resolve_handles_evolving_frames():
    """Frames may take a moment to populate. Stub that returns empty
    on the first poll, then the embed frame on the second."""

    class _EvolvingPage:
        def __init__(self):
            self.calls = 0

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

    out = resolve_form_iframe(_EvolvingPage(), settle_seconds=2.0)
    assert out is not None
    assert "embed/job_app" in out
