"""GET /api/review/spam + /api/review/flags contract."""

from __future__ import annotations


def test_review_spam_matches_ashby_no_confirmation(client):
    r = client.get("/api/review/spam")
    assert r.status_code == 200
    body = r.json()
    # The seed has one ashby+no_confirmation+/application failure.
    assert len(body) == 1
    item = body[0]
    assert item["job_company"] == "Modal"
    assert item["error_code"] == "no_confirmation"


def test_review_flags_lists_seed_flag(client):
    r = client.get("/api/review/flags")
    body = r.json()
    assert body["total"] == 1
    flag = body["items"][0]
    assert flag["reason"] == "unresolved"
    assert "Bulgaria" in flag["field_label"]


def test_review_flags_filter_by_reason(client):
    r = client.get("/api/review/flags", params={"reason": "captcha"})
    body = r.json()
    assert body["total"] == 0


def test_dashboard_kpis(client):
    r = client.get("/api/dashboard")
    body = r.json()
    assert body["total_jobs"] == 3
    assert body["scored_jobs"] == 3
    assert body["unapplied_scored"] == 1     # only WHOOP unapplied
    assert body["apps_total"] == 2
    assert body["outcomes"]["ok"] == 1
    assert body["outcomes"]["failed"] == 1
    assert len(body["top_unapplied"]) == 1
    assert body["top_unapplied"][0]["company"] == "WHOOP"

    # Spam-rate row for ashby should reflect the seed (1 failed, 1 spam).
    by_src = {sr["source"]: sr for sr in body["spam_rates"]}
    assert by_src["ashby"]["total_failed"] == 1
    assert by_src["ashby"]["spam_flagged"] == 1
    assert by_src["ashby"]["spam_rate"] == 1.0
    assert by_src["greenhouse"]["total_failed"] == 0
