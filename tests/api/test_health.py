"""Trivial app boot test."""

from __future__ import annotations


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_openapi_schema_exposed(client):
    r = client.get("/api/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    # Sanity-check the routes the typegen pipeline expects.
    paths = set(schema["paths"].keys())
    for required in (
        "/api/dashboard",
        "/api/jobs",
        "/api/jobs/{job_id}",
        "/api/applications",
        "/api/applications/{app_id}",
        "/api/review/spam",
        "/api/review/flags",
    ):
        assert required in paths, f"missing {required}"
