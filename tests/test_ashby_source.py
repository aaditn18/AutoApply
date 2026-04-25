"""AshbySource ingest tests — mocked httpx transport.

Mirrors the pattern in `test_ingest.py` for Greenhouse and Lever.
Confirms:

- Mapping from Ashby's public posting-api JSON to RawJob.
- `isListed=False` postings are filtered out at ingest.
- `descriptionPlain` wins over `descriptionHtml`.
- `applyUrl` wins over `jobUrl`.
- Unpublished-id postings get a stable hash-based source_id.
- Metadata captures isRemote, workplaceType, secondaryLocations,
  compensation summary, applyUrl.
"""

from __future__ import annotations

import httpx

from autoapply.ingest.ashby import (
    ASHBY_BASE,
    AshbySource,
    _department,
    _description,
    _location,
    _metadata,
)


# -- Helpers ---------------------------------------------------------------


def _mock_transport(status_code: int = 200, payload: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload or {})
    return httpx.MockTransport(handler)


_ASHBY_PAYLOAD = {
    "apiVersion": "1",
    "jobs": [
        {
            "id": "11111111-1111-1111-1111-111111111111",
            "title": "Senior Software Engineer",
            "location": "San Francisco, CA",
            "department": "Engineering",
            "team": "Platform",
            "isRemote": False,
            "workplaceType": "OnSite",
            "isListed": True,
            "descriptionHtml": "<p>Build the backend.</p>",
            "descriptionPlain": "Build the backend.",
            "publishedAt": "2026-04-01T00:00:00Z",
            "employmentType": "FullTime",
            "jobUrl": "https://jobs.ashbyhq.com/acme/11111111",
            "applyUrl": "https://jobs.ashbyhq.com/acme/11111111/application",
            "secondaryLocations": [
                {"location": "New York, NY"},
                {"location": "Remote US"},
            ],
            "compensation": {
                "compensationTierSummary": "$200k – $260k + equity",
            },
        },
        {
            # Unpublished draft — must be filtered out at ingest.
            "id": "22222222-2222-2222-2222-222222222222",
            "title": "Draft Role",
            "isListed": False,
            "jobUrl": "https://jobs.ashbyhq.com/acme/22222222",
            "descriptionPlain": "Should not be ingested.",
        },
        {
            # No id in public feed + HTML-only description — source_id
            # falls back to a sha256 of the jobUrl; description comes
            # from stripped HTML.
            "title": "Research Scientist",
            "location": "-REMOTE-",
            "isListed": True,
            "descriptionHtml": "<p>Study <b>LLMs</b> and agents.</p>",
            "employmentType": "FullTime",
            "jobUrl": "https://jobs.ashbyhq.com/acme/no-id-job",
            # No applyUrl — fall back to jobUrl.
        },
    ],
}


# -- Pure-helper tests -----------------------------------------------------


def test_description_prefers_plain():
    p = {"descriptionPlain": "Plain text here.", "descriptionHtml": "<p>ignored</p>"}
    assert _description(p) == "Plain text here."


def test_description_falls_back_to_stripped_html():
    p = {"descriptionHtml": "<p>Build <b>stuff</b>.</p>"}
    out = _description(p)
    assert "Build" in out
    assert "stuff" in out
    assert "<" not in out


def test_description_empty():
    assert _description({}) == ""


def test_location_empty_when_missing():
    assert _location({}) == ""


def test_location_preserves_raw_value():
    assert _location({"location": "-REMOTE, BULGARIA-"}) == "-REMOTE, BULGARIA-"


def test_department_joins_department_and_team():
    assert _department({"department": "Engineering", "team": "Platform"}) == \
        "Engineering / Platform"


def test_department_handles_missing_team():
    assert _department({"department": "Engineering"}) == "Engineering"


def test_department_empty_when_missing():
    assert _department({}) == ""


def test_metadata_stringifies_isremote():
    md = _metadata({"isRemote": True})
    assert md["isRemote"] == "true"
    md2 = _metadata({"isRemote": False})
    assert md2["isRemote"] == "false"


def test_metadata_joins_secondary_locations():
    md = _metadata({
        "secondaryLocations": [
            {"location": "NYC"},
            {"location": "Boston"},
        ],
    })
    assert md["secondaryLocations"] == "NYC, Boston"


def test_metadata_preserves_apply_url_and_comp_summary():
    md = _metadata({
        "applyUrl": "https://jobs.ashbyhq.com/acme/xyz/application",
        "compensation": {"compensationTierSummary": "$100k – $150k"},
    })
    assert md["applyUrl"].endswith("/application")
    assert md["compensationSummary"] == "$100k – $150k"


def test_metadata_skips_missing_fields():
    md = _metadata({})
    assert md == {}


# -- Integration: fetch_board end-to-end ----------------------------------


def test_ashby_fetch_board_happy_path():
    src = AshbySource(client=httpx.Client(transport=_mock_transport(200, _ASHBY_PAYLOAD)))
    jobs = list(src.fetch_board("acme"))
    # 3 postings in, 1 is unlisted and must be skipped → 2 expected.
    assert len(jobs) == 2

    j1 = jobs[0]
    assert j1.source == "ashby"
    assert j1.source_id == "11111111-1111-1111-1111-111111111111"
    assert j1.board_token == "acme"
    assert j1.title == "Senior Software Engineer"
    assert j1.company == "Acme"
    assert j1.location == "San Francisco, CA"
    assert j1.department == "Engineering / Platform"
    assert j1.description == "Build the backend."
    assert j1.employment_type == "FullTime"
    # applyUrl wins over jobUrl as the primary URL.
    assert j1.url.endswith("/application")
    assert j1.posted_at == "2026-04-01T00:00:00Z"
    assert j1.metadata.get("isRemote") == "false"
    assert j1.metadata.get("workplaceType") == "OnSite"
    assert "New York" in j1.metadata.get("secondaryLocations", "")
    assert "$200k" in j1.metadata.get("compensationSummary", "")


def test_ashby_fetch_board_filters_unlisted():
    src = AshbySource(client=httpx.Client(transport=_mock_transport(200, _ASHBY_PAYLOAD)))
    titles = [j.title for j in src.fetch_board("acme")]
    assert "Draft Role" not in titles


def test_ashby_missing_id_falls_back_to_hash():
    src = AshbySource(client=httpx.Client(transport=_mock_transport(200, _ASHBY_PAYLOAD)))
    jobs = [j for j in src.fetch_board("acme") if j.title == "Research Scientist"]
    assert len(jobs) == 1
    j = jobs[0]
    # source_id must be a 16-char hex string (sha256 prefix) since the
    # payload had no `id`.
    assert len(j.source_id) == 16
    assert all(c in "0123456789abcdef" for c in j.source_id)
    # Description came from stripped HTML.
    assert "LLMs" in j.description
    assert "<" not in j.description
    # No applyUrl in payload → URL falls back to jobUrl.
    assert j.url == "https://jobs.ashbyhq.com/acme/no-id-job"


def test_ashby_fetch_board_http_error_yields_empty():
    src = AshbySource(client=httpx.Client(transport=_mock_transport(500, {})))
    assert list(src.fetch_board("doesnotexist")) == []


def test_ashby_fetch_board_non_dict_payload_yields_empty():
    """Ashby has returned stringified errors under load; make sure a
    non-dict payload doesn't crash the source."""
    src = AshbySource(client=httpx.Client(transport=_mock_transport(200, [])))  # type: ignore[arg-type]
    assert list(src.fetch_board("acme")) == []


def test_ashby_company_canonicalizes_hyphenated_tokens():
    """Ashby doesn't echo company per posting — board_token becomes
    the company via title-casing (same pattern as Lever)."""
    src = AshbySource(client=httpx.Client(transport=_mock_transport(200, _ASHBY_PAYLOAD)))
    jobs = list(src.fetch_board("mistral-ai"))
    assert all(j.company == "Mistral Ai" for j in jobs)


def test_ashby_base_url_unchanged():
    """If this module constant is renamed, many tests / scrapers break.
    Freeze the endpoint string."""
    assert ASHBY_BASE == "https://api.ashbyhq.com/posting-api/job-board"
