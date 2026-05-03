"""GET /api/jobs filter + pagination contract."""

from __future__ import annotations


def test_list_jobs_returns_all(client):
    r = client.get("/api/jobs")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3
    assert {it["company"] for it in body["items"]} == {"Acme", "Modal", "WHOOP"}


def test_list_jobs_filter_by_source(client):
    r = client.get("/api/jobs", params={"source": "ashby"})
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["company"] == "Modal"


def test_list_jobs_filter_by_track(client):
    r = client.get("/api/jobs", params={"track": "ml"})
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["company"] == "WHOOP"


def test_list_jobs_min_rank(client):
    r = client.get("/api/jobs", params={"min_rank": 0.8})
    body = r.json()
    assert body["total"] == 2
    for it in body["items"]:
        assert it["scoring"]["final_rank"] >= 0.8


def test_list_jobs_has_applied_false(client):
    r = client.get("/api/jobs", params={"has_applied": "false"})
    body = r.json()
    # Only WHOOP has no application in the seed.
    assert body["total"] == 1
    assert body["items"][0]["company"] == "WHOOP"


def test_list_jobs_has_applied_true(client):
    r = client.get("/api/jobs", params={"has_applied": "true"})
    body = r.json()
    assert body["total"] == 2
    for it in body["items"]:
        assert it["application_count"] >= 1


def test_list_jobs_orders_by_final_rank_desc(client):
    r = client.get("/api/jobs", params={"order_by": "final_rank"})
    body = r.json()
    ranks = [it["scoring"]["final_rank"] for it in body["items"]]
    # Two are 0.85 (tie); WHOOP's 0.50 must be last.
    assert ranks[-1] == 0.50


def test_list_jobs_pagination(client):
    r = client.get("/api/jobs", params={"page": 1, "page_size": 2})
    body = r.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2

    r2 = client.get("/api/jobs", params={"page": 2, "page_size": 2})
    body2 = r2.json()
    assert len(body2["items"]) == 1


def test_get_job_detail_includes_applications(client):
    # Find Acme's id
    r = client.get("/api/jobs", params={"company": "Acme"})
    j = r.json()["items"][0]

    r = client.get(f"/api/jobs/{j['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["company"] == "Acme"
    assert body["description"] == "Backend role"
    assert len(body["applications"]) == 1
    assert body["applications"][0]["outcome"] == "ok"


def test_get_job_404(client):
    r = client.get("/api/jobs/99999")
    assert r.status_code == 404


def test_list_jobs_validates_params(client):
    # min_rank out of bounds
    r = client.get("/api/jobs", params={"min_rank": 99})
    assert r.status_code == 422
