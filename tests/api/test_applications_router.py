"""GET /api/applications + /api/applications/{id} contract."""

from __future__ import annotations


def test_list_applications_default(client):
    r = client.get("/api/applications")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2


def test_list_applications_filter_outcome(client):
    r = client.get("/api/applications", params={"outcome": "ok"})
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["job_company"] == "Acme"


def test_list_applications_filter_source(client):
    r = client.get("/api/applications", params={"source": "ashby"})
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["job_company"] == "Modal"


def test_list_applications_orders_newest_first(client):
    r = client.get("/api/applications")
    items = r.json()["items"]
    # a2 (Modal, 2026-04-30) is newer than a1 (Acme, 2026-04-28).
    assert items[0]["job_company"] == "Modal"
    assert items[1]["job_company"] == "Acme"


def test_application_detail_resolves_fields(client):
    # find acme's app id
    r = client.get("/api/applications", params={"outcome": "ok"})
    app_id = r.json()["items"][0]["id"]

    r = client.get(f"/api/applications/{app_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "ok"
    assert body["resolved_field_count"] == 4
    assert body["llm_model"] == "gemini-2.5-flash-lite"
    assert body["screenshot_url"].startswith("/api/static/screenshots/presubmit_")

    # Source-tagging heuristic
    by_source = {f["label"]: f["source"] for f in body["resolved_fields"]}
    assert by_source["first_name"] == "profile"
    assert by_source["email"] == "profile"
    assert by_source["salary_expectation"] == "bank"
    assert by_source["why_company"] == "llm"  # was in batch_asked


def test_application_detail_attaches_review_flags(client):
    # The Modal failed app has 1 review flag in the seed
    r = client.get("/api/applications", params={"outcome": "failed"})
    app_id = r.json()["items"][0]["id"]

    r = client.get(f"/api/applications/{app_id}")
    body = r.json()
    assert len(body["review_flags"]) == 1
    flag = body["review_flags"][0]
    assert flag["reason"] == "unresolved"
    assert "Bulgaria" in flag["field_label"]


def test_application_detail_404(client):
    r = client.get("/api/applications/99999")
    assert r.status_code == 404
