"""Ingest tests — mock HTTP, assert RawJob shape + HTML stripping."""

from __future__ import annotations

import json

import httpx
import pytest

from autoapply.ingest.base import JobSource, RawJob
from autoapply.ingest.greenhouse import (
    GreenhouseSource,
    _company_from_metadata,
    _first_location,
    _strip_html,
)
from autoapply.ingest.lever import LeverSource, _flatten_description, _location


# -- HTML strip -------------------------------------------------------------


def test_strip_html_basic_tags():
    # Block-level tags become newlines; we accept single or double newline
    # between paragraphs (depends on how collapse runs).
    out = _strip_html("<p>Hello</p><p>World</p>")
    assert "Hello" in out
    assert "World" in out
    assert "\n" in out


def test_strip_html_preserves_word_boundaries():
    """<li> tags must not run words together."""
    s = _strip_html("<ul><li>Python</li><li>Rust</li></ul>")
    assert "Python" in s
    assert "Rust" in s
    assert "PythonRust" not in s


def test_strip_html_handles_entities():
    s = _strip_html("&lt;div&gt; &amp; &nbsp;stuff&quot;")
    # Exact whitespace isn't important — collapsed to single space is fine.
    assert "<div>" in s
    assert "&" in s
    assert "stuff" in s
    assert '"' in s


def test_strip_html_collapses_whitespace():
    s = _strip_html("<p>hello      world</p>")
    assert s == "hello world"


def test_strip_html_empty():
    assert _strip_html("") == ""
    assert _strip_html(None) == ""  # type: ignore[arg-type]


# -- Greenhouse helpers -----------------------------------------------------


def test_first_location_dict():
    assert _first_location({"location": {"name": "NYC"}}) == "NYC"


def test_first_location_offices_fallback():
    assert _first_location({"offices": [{"name": "SF"}, {"name": "NYC"}]}) == "SF, NYC"


def test_first_location_missing():
    assert _first_location({}) == ""


def test_company_from_metadata_prefers_payload():
    assert _company_from_metadata(
        {"metadata": [{"name": "Company", "value": "Acme Inc"}]},
        "acme-corp",
    ) == "Acme Inc"


def test_company_from_metadata_falls_back_to_token():
    assert _company_from_metadata({}, "acme-corp") == "Acme Corp"


# -- Greenhouse end-to-end via mock transport ------------------------------


_GH_PAYLOAD = {
    "jobs": [
        {
            "id": 12345,
            "title": "Software Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/12345",
            "content": "<p>Build backend services with <b>Python</b> and Go.</p>",
            "location": {"name": "New York, NY"},
            "departments": [{"name": "Engineering"}],
            "updated_at": "2026-04-15T10:00:00Z",
        },
        {
            "id": 23456,
            "title": "ML Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/23456",
            "content": "<ul><li>PyTorch</li><li>CUDA</li></ul>",
            "offices": [{"name": "Palo Alto"}, {"name": "Remote US"}],
            "departments": [{"name": "ML Platform"}],
        },
    ]
}


def _mock_transport(status_code: int = 200, payload: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload or {})
    return httpx.MockTransport(handler)


def test_greenhouse_fetch_board_happy_path():
    src = GreenhouseSource()
    src._client = httpx.Client(transport=_mock_transport(200, _GH_PAYLOAD))
    jobs = list(src.fetch_board("acme"))
    assert len(jobs) == 2
    j1 = jobs[0]
    assert j1.source == "greenhouse"
    assert j1.source_id == "12345"
    assert j1.title == "Software Engineer"
    assert j1.location == "New York, NY"
    assert j1.department == "Engineering"
    assert "Python" in j1.description
    assert "Go" in j1.description
    assert "<" not in j1.description  # HTML stripped

    j2 = jobs[1]
    assert j2.location == "Palo Alto, Remote US"
    assert "PyTorch" in j2.description
    assert "CUDA" in j2.description


def test_greenhouse_fetch_board_http_error_yields_empty():
    src = GreenhouseSource()
    src._client = httpx.Client(transport=_mock_transport(500, {}))
    assert list(src.fetch_board("doesnotexist")) == []


# -- Lever end-to-end via mock transport -----------------------------------


_LEVER_PAYLOAD = [
    {
        "id": "abc123",
        "text": "Software Engineer, Platform",
        "hostedUrl": "https://jobs.lever.co/netflix/abc123",
        "descriptionPlain": "Build the streaming platform.",
        "lists": [
            {"text": "Responsibilities", "content": "<ul><li>Design APIs</li></ul>"},
            {"text": "Qualifications", "content": "<p>5+ years of experience.</p>"},
        ],
        "additionalPlain": "Equal opportunity employer.",
        "categories": {
            "location": "Los Gatos, CA",
            "department": "Engineering",
            "team": "Platform",
            "commitment": "Full-time",
        },
        "createdAt": 1_700_000_000_000,
    },
    {
        "id": "xyz789",
        "text": "ML Researcher",
        "hostedUrl": "https://jobs.lever.co/netflix/xyz789",
        "description": "<p>Research recommendation systems.</p>",
        "categories": {
            "allLocations": ["New York, NY", "Remote US"],
            "department": "Research",
        },
    },
]


def test_lever_fetch_board_happy_path():
    src = LeverSource()
    src._client = httpx.Client(transport=_mock_transport(200, _LEVER_PAYLOAD))
    jobs = list(src.fetch_board("netflix"))
    assert len(jobs) == 2

    j1 = jobs[0]
    assert j1.source == "lever"
    assert j1.source_id == "abc123"
    assert j1.title == "Software Engineer, Platform"
    assert j1.location == "Los Gatos, CA"
    assert "Responsibilities" in j1.description
    assert "Design APIs" in j1.description
    assert "Equal opportunity" in j1.description
    assert j1.employment_type == "Full-time"
    assert j1.department.startswith("Engineering")

    j2 = jobs[1]
    assert j2.location == "New York, NY, Remote US"
    assert "recommendation systems" in j2.description


def test_lever_location_extractor_handles_list():
    assert _location({"categories": {"allLocations": ["A", "B"]}}) == "A, B"


def test_lever_description_concatenates_sections():
    p = {
        "descriptionPlain": "Intro.",
        "lists": [{"text": "Req", "content": "<p>Python</p>"}],
        "additionalPlain": "Notes.",
    }
    desc = _flatten_description(p)
    assert "Intro." in desc
    assert "Req" in desc
    assert "Python" in desc
    assert "Notes." in desc


# -- Base class contract ---------------------------------------------------


def test_jobsource_is_abstract():
    with pytest.raises(TypeError):
        JobSource()  # type: ignore[abstract]


def test_rawjob_defaults():
    j = RawJob(source="x", source_id="1", board_token="t", url="u", title="T", company="C")
    assert j.location == ""
    assert j.metadata == {}
