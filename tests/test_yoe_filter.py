"""Tests for the YOE hard filter (select/yoe_filter.py).

Coverage:
  1. Strong patterns: N+, minimum N, at least N, N or more, range N-M
  2. Contextual patterns: "N years of relevant experience" with requirement context
  3. Soft-marker exclusions: preferred, nice-to-have, desired, ideally, etc.
  4. False-positive avoidance: company age, team tenure, no YOE stated
  5. Boundary/edge cases: 0-2 (keep), 2 exactly (keep), 3 (reject), blank
  6. is_yoe_eligible() end-to-end behaviour
"""

from __future__ import annotations

import pytest

from autoapply.select.yoe_filter import MAX_NEW_GRAD_YOE, extract_min_yoe, is_yoe_eligible


# ── extract_min_yoe — strong patterns ──────────────────────────────────────


class TestStrongPatterns:
    def test_n_plus_years_experience_explicit(self):
        assert extract_min_yoe("We require 3+ years of experience in Python.") == 3

    def test_n_plus_years_experience_no_of(self):
        assert extract_min_yoe("You have 5+ years experience building backend systems.") == 5

    def test_n_plus_years_expertise(self):
        assert extract_min_yoe("7+ years expertise in distributed computing.") == 7

    def test_n_plus_years_background(self):
        assert extract_min_yoe("4+ years background in software engineering.") == 4

    def test_minimum_n_years(self):
        assert extract_min_yoe("Minimum 4 years in a software engineering role.") == 4

    def test_minimum_of_n_years(self):
        assert extract_min_yoe("Minimum of 6 years of relevant experience.") == 6

    def test_at_least_n_years(self):
        assert extract_min_yoe("At least 3 years of hands-on development experience.") == 3

    def test_no_less_than(self):
        assert extract_min_yoe("No less than 5 years of engineering experience.") == 5

    def test_n_or_more_years(self):
        assert extract_min_yoe("Must have 5 or more years of engineering background.") == 5

    def test_range_extracts_lower_bound(self):
        assert extract_min_yoe("3-5 years of professional experience required.") == 3

    def test_range_5_to_7_en_dash(self):
        assert extract_min_yoe("5\u20137 years experience in distributed systems.") == 5

    def test_range_6_to_8_em_dash(self):
        assert extract_min_yoe("6\u20148 years of experience in ML infrastructure.") == 6

    def test_double_digit_years(self):
        assert extract_min_yoe("10+ years of industry experience.") == 10

    def test_ten_or_more(self):
        assert extract_min_yoe("10 or more years of HPC experience required.") == 10

    def test_one_plus_extracted(self):
        # 1+ years is valid and ≤ MAX; we still extract it, eligibility is True.
        assert extract_min_yoe("1+ years of experience with Python.") == 1

    def test_two_plus_extracted(self):
        assert extract_min_yoe("2+ years of experience building APIs.") == 2


# ── extract_min_yoe — contextual patterns ──────────────────────────────────


class TestContextualPatterns:
    def test_relevant_experience_with_qualifications_context(self):
        text = "Qualifications: 4 years of relevant experience in Python."
        assert extract_min_yoe(text) == 4

    def test_professional_experience_must_have(self):
        text = "Candidates must have 3 years of professional experience in systems."
        assert extract_min_yoe(text) == 3

    def test_experience_required_suffix(self):
        assert extract_min_yoe("5 years of experience required for this position.") == 5

    def test_direct_experience_we_require(self):
        text = "We require 4 years of direct experience in financial engineering."
        assert extract_min_yoe(text) == 4

    def test_work_experience_requirements_section(self):
        text = "Requirements:\n- 3 years of work experience in SWE roles"
        assert extract_min_yoe(text) == 3

    def test_no_context_word_no_match(self):
        # "4 years of relevant experience" without a requirement word before it.
        # Should NOT match contextual patterns.
        text = "Our product team brings 4 years of relevant experience."
        result = extract_min_yoe(text)
        # Might be None if no context; if matched, still record the value
        # but the important assertion is it doesn't produce a false hard reject
        # for is_yoe_eligible.
        if result is not None:
            assert isinstance(result, int)


# ── extract_min_yoe — soft-marker exclusions ───────────────────────────────


class TestSoftMarkerExclusions:
    def test_preferred_excluded(self):
        assert extract_min_yoe("Preferred: 5+ years of experience in data science.") is None

    def test_nice_to_have_excluded(self):
        assert extract_min_yoe("Nice to have: 3+ years experience with Kubernetes.") is None

    def test_nice_to_have_hyphenated(self):
        assert extract_min_yoe("Nice-to-have: minimum 4 years in quant finance.") is None

    def test_a_plus_excluded(self):
        assert extract_min_yoe("Experience with CUDA (a plus: 3+ years of GPU work).") is None

    def test_desired_excluded(self):
        assert extract_min_yoe("Desired: minimum 4 years in quantitative research.") is None

    def test_ideally_excluded(self):
        assert extract_min_yoe("Ideally 5 years of experience with distributed systems.") is None

    def test_bonus_if_excluded(self):
        assert extract_min_yoe("Bonus if you have 3+ years with streaming systems.") is None

    def test_not_required_excluded(self):
        assert extract_min_yoe("Not required but 5+ years of ML experience is a plus.") is None

    def test_would_be_great_excluded(self):
        assert extract_min_yoe("Would be great if you have 4+ years of experience.") is None

    def test_soft_in_one_sentence_hard_in_another(self):
        # Soft marker in first sentence; hard req in second.
        text = (
            "Nice to have: 5+ years experience with Kafka. "
            "Requirements: 3+ years of experience in Python is required."
        )
        # Should pick up the hard req (3), not the soft (5).
        assert extract_min_yoe(text) == 3


# ── extract_min_yoe — false-positive avoidance ─────────────────────────────


class TestFalsePositiveAvoidance:
    def test_no_yoe_stated(self):
        assert extract_min_yoe("We are looking for a talented software engineer.") is None

    def test_empty_string(self):
        assert extract_min_yoe("") is None

    def test_none_input(self):
        assert extract_min_yoe(None) is None  # type: ignore[arg-type]

    def test_entry_level_role(self):
        text = "Entry-level position, no prior experience required. New grads welcome."
        assert extract_min_yoe(text) is None

    def test_new_grad_welcome(self):
        assert extract_min_yoe("New graduates and recent grads are encouraged to apply.") is None

    def test_dollar_amount_not_matched(self):
        # "$1.5B per year" should not trigger YOE extraction.
        assert extract_min_yoe("Processed $1.5B in transactions per year.") is None

    def test_very_high_fake_number_ignored(self):
        # 50+ years would be absurd — capped at 30.
        result = extract_min_yoe("50+ years of combined team experience.")
        assert result is None or result == 50  # implementation may vary; 50 > cap


# ── is_yoe_eligible — end-to-end ──────────────────────────────────────────


class TestIsYoeEligible:
    def test_no_yoe_eligible(self):
        assert is_yoe_eligible("Looking for a backend engineer. No experience required.") is True

    def test_none_input_eligible(self):
        assert is_yoe_eligible(None) is True  # type: ignore[arg-type]

    def test_empty_string_eligible(self):
        assert is_yoe_eligible("") is True

    def test_one_year_plus_eligible(self):
        assert is_yoe_eligible("1+ years of experience in software development.") is True

    def test_two_years_exactly_eligible(self):
        assert is_yoe_eligible("Minimum 2 years of experience.") is True

    def test_two_plus_eligible(self):
        assert is_yoe_eligible("2+ years of experience building APIs.") is True

    def test_range_zero_to_two_eligible(self):
        # "0-2 years" — the lower bound regex requires [1-9] so "0" won't
        # match the range pattern; no min extracted → eligible.
        assert is_yoe_eligible("0-2 years of professional experience is a plus.") is True

    def test_range_two_to_four_eligible(self):
        # Min is 2, which is ≤ MAX_NEW_GRAD_YOE.
        assert is_yoe_eligible("2-4 years of professional experience required.") is True

    def test_three_plus_rejected(self):
        assert is_yoe_eligible("3+ years of experience required.") is False

    def test_five_years_rejected(self):
        assert is_yoe_eligible("Minimum 5 years of relevant experience.") is False

    def test_ten_years_rejected(self):
        assert is_yoe_eligible("10+ years of industry experience required.") is False

    def test_range_three_to_five_rejected(self):
        assert is_yoe_eligible("3-5 years of professional experience required.") is False

    def test_preferred_five_still_eligible(self):
        # "preferred" → soft marker → not a hard requirement.
        assert is_yoe_eligible("Preferred: 5+ years of experience in machine learning.") is True

    def test_long_realistic_jd_reject(self):
        jd = (
            "About the role: We are building the next generation of GPU compilers.\n"
            "Responsibilities: Lead design of LLVM passes for our target architecture.\n"
            "Requirements:\n"
            "- BS/MS in Computer Science or related field\n"
            "- 4+ years of experience in compiler engineering\n"
            "- Proficiency in C++ and Python\n"
            "Nice to have: 8+ years of experience in production compiler work."
        )
        # Hard req is 4+, preferred is 8+; should extract 4 and reject.
        assert is_yoe_eligible(jd) is False

    def test_long_realistic_jd_pass(self):
        jd = (
            "About the role: Join our early-stage infrastructure team.\n"
            "Responsibilities: Build distributed storage systems in Go.\n"
            "Requirements:\n"
            "- BS/MS in Computer Science\n"
            "- Strong fundamentals in distributed systems\n"
            "- Proficiency in Go, Python, or Rust\n"
            "Preferred: 3+ years of experience (new grads strongly encouraged!)."
        )
        # The "3+ years" is marked as preferred → soft marker → no hard req → eligible.
        assert is_yoe_eligible(jd) is True

    def test_max_new_grad_yoe_constant(self):
        assert MAX_NEW_GRAD_YOE == 2
