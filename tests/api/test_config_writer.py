"""Hermetic tests for the config_writer service.

Each test sets ALLOWED_PATHS to point at temp files, exercises the
read/write paths, asserts content + that comments survive a round
trip + that backups are created.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from api.services import config_writer as cw


@pytest.fixture
def tmp_paths(tmp_path: Path, monkeypatch):
    """Redirect ALLOWED_PATHS + BACKUP_DIR to a temp dir."""
    paths = {
        "answer_bank": tmp_path / "answer_bank.yml",
        "companies": tmp_path / "companies.yml",
        "env": tmp_path / ".env",
        "profile": tmp_path / "profile.json",
    }
    monkeypatch.setattr(cw, "ALLOWED_PATHS", paths)
    monkeypatch.setattr(cw, "BACKUP_DIR", tmp_path / "backups")
    return paths


# ── Answer bank ─────────────────────────────────────────────────────


def test_answer_bank_round_trip_preserves_comments(tmp_paths):
    src = """\
# top-level comment
work_authorized_us: true   # inline comment
salary_expectation:
  swe: "Market rate for new-grad SWE"
"""
    tmp_paths["answer_bank"].write_text(src)

    text, parsed = cw.read_answer_bank()
    assert "top-level comment" in text
    assert parsed["work_authorized_us"] is True

    # Re-write the same text and confirm the comment survives.
    cw.write_answer_bank(text)
    after = tmp_paths["answer_bank"].read_text()
    assert "top-level comment" in after


def test_answer_bank_rejects_invalid_yaml(tmp_paths):
    with pytest.raises(ValueError, match="YAML parse failed"):
        cw.write_answer_bank("key: : invalid")


def test_answer_bank_rejects_non_mapping(tmp_paths):
    with pytest.raises(ValueError, match="top-level mapping"):
        cw.write_answer_bank("- list\n- only\n")


def test_answer_bank_creates_backup(tmp_paths):
    tmp_paths["answer_bank"].write_text("a: 1\n")
    backup = cw.write_answer_bank("a: 2\n")
    assert backup is not None and backup.exists()
    assert backup.read_text() == "a: 1\n"
    assert tmp_paths["answer_bank"].read_text() == "a: 2\n"


# ── Companies ───────────────────────────────────────────────────────


SAMPLE_COMPANIES = """\
# AutoApply — board token registry
greenhouse:
  test_safe:
    - acme
    - widget
  live_only:
    - openai
lever:
  test_safe:
    - whoop
ashby:
  test_safe:
    - mux
"""


def test_companies_read(tmp_paths):
    tmp_paths["companies"].write_text(SAMPLE_COMPANIES)
    grid = cw.read_companies()
    assert grid["greenhouse"]["test_safe"] == ["acme", "widget"]
    assert grid["greenhouse"]["live_only"] == ["openai"]
    assert grid["ashby"]["test_safe"] == ["mux"]


def test_companies_write_preserves_top_comment(tmp_paths):
    tmp_paths["companies"].write_text(SAMPLE_COMPANIES)
    grid = cw.read_companies()
    grid["greenhouse"]["test_safe"].append("brewco")
    cw.write_companies(grid)
    after = tmp_paths["companies"].read_text()
    assert "AutoApply — board token registry" in after
    assert "brewco" in after


def test_companies_validate_rejects_bad_source(tmp_paths):
    with pytest.raises(ValueError, match="unknown source"):
        cw.write_companies({"workday": {"test_safe": ["a"]}})


def test_companies_validate_rejects_bad_token(tmp_paths):
    with pytest.raises(ValueError, match="invalid char"):
        cw.write_companies({"greenhouse": {"test_safe": ["bad token"]}})


def test_companies_validate_rejects_duplicate(tmp_paths):
    with pytest.raises(ValueError, match="duplicate"):
        cw.write_companies({"greenhouse": {"test_safe": ["a", "a"]}})


# ── Env ─────────────────────────────────────────────────────────────


def test_env_read_parses_assignments(tmp_paths):
    tmp_paths["env"].write_text(
        "# comment\nDRY_RUN=true\nLOG_LEVEL=INFO\n# another\nFOO=bar\n"
    )
    parsed = cw.read_env()
    assert parsed == {"DRY_RUN": "true", "LOG_LEVEL": "INFO", "FOO": "bar"}


def test_env_write_replaces_in_place(tmp_paths):
    src = "# header\nDRY_RUN=true\n# mid\nLOG_LEVEL=INFO\n"
    tmp_paths["env"].write_text(src)
    cw.write_env({"DRY_RUN": "false"})
    after = tmp_paths["env"].read_text()
    assert "DRY_RUN=false" in after
    assert "DRY_RUN=true" not in after
    assert "# header" in after  # comment preserved
    assert "# mid" in after


def test_env_write_appends_new_keys(tmp_paths):
    tmp_paths["env"].write_text("DRY_RUN=true\n")
    cw.write_env({"NEW_KEY": "new_value"})
    after = tmp_paths["env"].read_text()
    assert "NEW_KEY=new_value" in after


def test_env_write_rejects_invalid_key(tmp_paths):
    with pytest.raises(ValueError, match="invalid env key"):
        cw.write_env({"BAD KEY": "x"})


def test_env_mask_secret():
    assert cw.mask_secret("") == ""
    assert cw.mask_secret("abc") == "****"
    assert cw.mask_secret("longsecret") == "****cret"
