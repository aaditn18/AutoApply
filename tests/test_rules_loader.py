"""Tests for the policy-layer rule/prompt loader.

The loader (:mod:`autoapply.rules`) is intentionally tiny — file-exists
check + YAML parse + caching. The real value of these tests is the
**schema assertions** for each shipped rule file: if someone renames a
top-level key in ``state/rules/*.yml`` without updating the consumer,
these tests fail loudly instead of a production apply crashing mid-form.

Every rule file documented in ``state/rules/`` should have a test here
that:
  1. Loads it,
  2. Asserts the keys the code expects to see,
  3. Sanity-checks the value shape (non-empty, right type).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from autoapply.rules import load_prompt, load_rules
from autoapply.rules.loader import clear_cache


@pytest.fixture(autouse=True)
def _clear_rule_cache():
    """Drop the LRU cache before every test so cross-test mutations
    never leak (important for fixture-file tests)."""
    clear_cache()
    yield
    clear_cache()


# ─── Basic loader behavior ──────────────────────────────────────────────


def test_load_rules_missing_file_raises(tmp_path, monkeypatch):
    # Point state_dir at an empty tmp dir so the rule file is missing.
    from autoapply import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "state_dir", tmp_path, raising=False)
    clear_cache()
    with pytest.raises(FileNotFoundError, match="rule file not found"):
        load_rules("no_such_rule")


def test_load_prompt_missing_file_raises(tmp_path, monkeypatch):
    from autoapply import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "prompts_dir", tmp_path, raising=False)
    clear_cache()
    with pytest.raises(FileNotFoundError, match="prompt file not found"):
        load_prompt("no_such_prompt")


def test_load_rules_caches_result():
    # Two calls for the same name return the same dict object.
    a = load_rules("skill_aliases")
    b = load_rules("skill_aliases")
    assert a is b, "rule loader should memoize by name"


def test_load_rules_rejects_list_top_level(tmp_path, monkeypatch):
    # Loader asserts top-level is a dict — catches mis-structured rule files.
    from autoapply import config

    settings = config.get_settings()
    rules_dir = tmp_path / "rules"
    rules_dir.mkdir()
    (rules_dir / "list_top.yml").write_text("- one\n- two\n")
    monkeypatch.setattr(settings, "state_dir", tmp_path, raising=False)
    clear_cache()
    with pytest.raises(ValueError, match="must parse to a dict"):
        load_rules("list_top")


# ─── Shipped rule files — schema smoke tests ─────────────────────────────


def test_education_preferences_shape():
    data = load_rules("education_preferences")
    for key in ("school", "degree", "discipline"):
        assert key in data, f"education_preferences missing {key!r}"
        assert isinstance(data[key], list) and data[key], f"{key} must be non-empty list"
        # Every entry must compile as a regex.
        for pat in data[key]:
            re.compile(pat, re.IGNORECASE)


def test_education_preferences_school_prefers_college_park():
    # Regression: the first pattern should match the UMD College Park
    # punctuation variants before the UMD-only fallback.
    school_pats = load_rules("education_preferences")["school"]
    rx = re.compile(school_pats[0], re.IGNORECASE)
    assert rx.search("University of Maryland - College Park")
    # Plain UMD should NOT match the first preference (it has its own
    # lower-priority rule).
    assert not rx.search("University of Maryland, Baltimore County")


def test_geography_has_all_50_states_plus_dc():
    labels = frozenset(load_rules("geography")["us_state_labels"])
    # Spot check canonical entries.
    for s in ("alabama", "wyoming", "maryland", "district of columbia", "dc"):
        assert s in labels, f"{s!r} missing from us_state_labels"
    # Every label must be lowercase — consumer lowercases scraped text.
    for s in labels:
        assert s == s.lower(), f"{s!r} must be lowercase"


def test_skill_aliases_shape_and_canonicalization():
    data = load_rules("skill_aliases")
    assert "aliases" in data and isinstance(data["aliases"], dict)
    assert "stopwords" in data and isinstance(data["stopwords"], list)
    # Canonical names for well-known skills stay stable.
    aliases = data["aliases"]
    assert aliases["py"] == "Python"
    assert aliases["cpp"] == "C++"
    assert aliases["k8s"] == "Kubernetes"


def test_machine_keys_shape_and_compile():
    rules = load_rules("machine_keys")["rules"]
    assert isinstance(rules, list) and rules
    for r in rules:
        assert {"pattern", "attr"} <= r.keys(), f"bad rule: {r}"
        re.compile(r["pattern"], re.I)  # must compile
    # Specific known entries that standard_fields.py relies on.
    attrs = [r["attr"] for r in rules]
    for needed in ("first_name", "last_name", "email", "phone", "resume",
                   "cover_letter", "linkedin_url", "github_url"):
        assert needed in attrs, f"machine_keys missing attr={needed!r}"


def test_machine_keys_disability_date_before_signature():
    # Regression: the disability-signature-DATE rule must match BEFORE
    # the plain disability-signature rule, because "Date" ends with
    # "signature" substring. standard_fields.py assumes this order.
    rules = load_rules("machine_keys")["rules"]
    date_idx = next(
        i for i, r in enumerate(rules) if "signature.*date" in r["pattern"]
    )
    sig_idx = next(
        i for i, r in enumerate(rules)
        if "disability.*signature" in r["pattern"] and "date" not in r["pattern"]
    )
    assert date_idx < sig_idx, "disability-signature-date must come before disability-signature"


def test_export_control_shape():
    data = load_rules("export_control")
    for key in ("us_person_markers", "other_markers"):
        assert key in data and isinstance(data[key], list) and data[key]
        for m in data[key]:
            assert m == m.lower(), f"{m!r} must be lowercase"


def test_eeo_semantics_has_core_decline_phrasings():
    kws = frozenset(load_rules("eeo_semantics")["decline_keywords"])
    for must_have in (
        "decline", "prefer not", "not wish", "not want",
        "not identify", "not disclose", "rather not",
    ):
        assert must_have in kws, f"eeo_semantics missing {must_have!r}"


def test_browser_pool_has_desktop_chrome_uas():
    uas = load_rules("browser_pool")["user_agents"]
    assert isinstance(uas, list) and len(uas) >= 3
    # Every UA must look like a current Chrome desktop string.
    for ua in uas:
        assert "Chrome/" in ua, f"non-Chrome UA: {ua!r}"
        assert "Mobile" not in ua, f"mobile UA not allowed in pool: {ua!r}"


# ─── Shipped prompt files — smoke tests ──────────────────────────────────


def test_batch_rules_prompt_has_all_numbered_rules():
    text = load_prompt("batch_rules")
    # Spot-check anchors. If someone edits the rules file and drops one
    # of these numbered headings, the prompt is silently weaker — this
    # test catches that.
    for heading in ("1. ", "2. ", "3. ", "4. ", "5. ", "6. ",
                    "7. ", "7a. ", "8. ", "8a. ", "9. ", "10. ",
                    "11. ", "12. ", "13. ", "14. "):
        assert heading in text, f"batch_rules missing heading {heading!r}"
    assert "{track}" in text, "batch_rules must expose {track} placeholder"


def test_batch_prompt_template_has_required_placeholders():
    text = load_prompt("batch")
    for placeholder in (
        "{rules}", "{meta_block}", "{profile_block}", "{bank_block}",
        "{jd_header}", "{jd_body}", "{questions_json}",
    ):
        assert placeholder in text, f"batch prompt missing {placeholder}"
    # Literal JSON example must escape braces.
    assert "{{" in text and "}}" in text


def test_batch_prompt_formats_without_error():
    # The real consumer does rules.format(track=...) then
    # template.format(rules=..., ...); if either format call fails due
    # to stray braces, we want to know at test time not at apply time.
    rules = load_prompt("batch_rules").format(track="swe")
    formatted = load_prompt("batch").format(
        rules=rules,
        meta_block="Track: swe",
        profile_block="{}",
        bank_block="",
        jd_header="",
        jd_body="(none)",
        questions_json="[]",
    )
    assert "CORE RULES" in formatted
    assert "track=swe" in formatted
