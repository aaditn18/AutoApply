"""Hermetic tests for the Phase 2 settings routers.

The ``settings_temp_paths`` fixture (defined in ``tests/api/conftest.py``)
redirects every config-writer path into a temp dir. We auto-apply it
here so every test in this module gets isolation without naming it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_config(settings_temp_paths):
    """Auto-apply isolation for this whole module."""
    yield settings_temp_paths


# ── /api/profile ─────────────────────────────────────────────────────


def test_profile_summary(client):
    r = client.get("/api/profile")
    assert r.status_code == 200
    body = r.json()
    assert len(body["tracks"]) == 1
    t = body["tracks"][0]
    assert t["track"] == "swe"
    assert t["full_name"] == "Aadit"
    assert t["skills_count"] == 3


# ── /api/answer_bank ─────────────────────────────────────────────────


def test_answer_bank_get(client):
    r = client.get("/api/answer_bank")
    body = r.json()
    assert r.status_code == 200
    assert "salary_expectation" in body["yaml_text"]
    assert body["keys"] == ["salary_expectation"]


def test_answer_bank_put_round_trips(client, settings_temp_paths):
    new_text = "salary_expectation: tweaked\nfresh_field: x\n"
    r = client.put("/api/answer_bank", json={"yaml_text": new_text})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert settings_temp_paths["answer_bank"].read_text() == new_text


def test_answer_bank_put_rejects_bad_yaml(client):
    r = client.put("/api/answer_bank", json={"yaml_text": ":\n  bad: : :"})
    assert r.status_code == 400
    assert "YAML parse failed" in r.json()["detail"]


def test_answer_bank_preview_hit(client):
    r = client.post(
        "/api/answer_bank/preview",
        json={"question_type": "salary_expectation"},
    )
    body = r.json()
    assert body["found"] is True
    assert body["value"] == "market rate"


def test_answer_bank_preview_miss(client):
    r = client.post(
        "/api/answer_bank/preview",
        json={"question_type": "made_up_key"},
    )
    body = r.json()
    assert body["found"] is False
    assert body["value"] is None


# ── /api/companies ───────────────────────────────────────────────────


def test_companies_get(client):
    r = client.get("/api/companies")
    body = r.json()
    assert body["sources"]["greenhouse"]["test_safe"] == ["acme"]
    assert body["sources"]["greenhouse"]["live_only"] == ["openai"]


def test_companies_put_adds_token(client, settings_temp_paths):
    grid = {
        "greenhouse": {
            "test_safe": ["acme", "brewco"],
            "live_only": ["openai"],
        }
    }
    r = client.put("/api/companies", json={"sources": grid})
    assert r.status_code == 200
    after = settings_temp_paths["companies"].read_text()
    assert "brewco" in after


def test_companies_put_rejects_bad_token(client):
    r = client.put(
        "/api/companies",
        json={"sources": {"greenhouse": {"test_safe": ["bad token"]}}},
    )
    assert r.status_code == 400
    assert "invalid char" in r.json()["detail"]


# ── /api/env ─────────────────────────────────────────────────────────


def test_env_get_masks_secrets(client, settings_temp_paths):
    settings_temp_paths["env"].write_text(
        "DRY_RUN=true\nGEMINI_API_KEY=secret-token-1234\n"
    )
    r = client.get("/api/env")
    body = r.json()
    by_key = {k["key"]: k for k in body["keys"]}
    assert by_key["DRY_RUN"]["value"] == "true"
    assert by_key["DRY_RUN"]["is_secret"] is False
    # masked
    assert by_key["GEMINI_API_KEY"]["is_secret"] is True
    assert by_key["GEMINI_API_KEY"]["value"].endswith("1234")
    assert "secret-token" not in by_key["GEMINI_API_KEY"]["value"]


def test_env_put_updates_in_place(client, settings_temp_paths):
    r = client.put("/api/env", json={"updates": {"DRY_RUN": "false"}})
    assert r.status_code == 200
    after = settings_temp_paths["env"].read_text()
    assert "DRY_RUN=false" in after
    assert "LOG_LEVEL=INFO" in after  # untouched key survives


def test_env_put_rejects_empty(client):
    r = client.put("/api/env", json={"updates": {}})
    assert r.status_code == 400
