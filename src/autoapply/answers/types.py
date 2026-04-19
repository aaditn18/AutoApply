"""Screening-question taxonomy.

Every application-form question is classified into exactly one `QuestionType`.
The downstream `AnswerBank` uses the type (plus resume_track when relevant) to
look up a deterministic answer, defer to `Profile` data, or route to LLM /
review depending on policy.

Adding a new type? Also:
  1. Add a regex rule in `answers.classifier._RULES`
  2. Add a canonical example in `tests/test_answer_bank.py`
  3. Decide its SOURCE policy (profile / bank / llm / review)
"""

from __future__ import annotations

from enum import Enum


class QuestionType(str, Enum):
    # -- Identity ---------------------------------------------------------
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    FULL_NAME = "full_name"
    PREFERRED_NAME = "preferred_name"

    # -- Contact ----------------------------------------------------------
    EMAIL = "email"
    PHONE = "phone"
    LINKEDIN_URL = "linkedin_url"
    GITHUB_URL = "github_url"
    PORTFOLIO_URL = "portfolio_url"
    WEBSITE_URL = "website_url"

    # -- Location / logistics --------------------------------------------
    CURRENT_LOCATION = "current_location"        # "City, State" long form
    CURRENT_CITY = "current_city"                # bare city-only field
    CURRENT_STATE = "current_state"              # bare state-only field
    CURRENT_ZIP = "current_zip"                  # bare zip/postal-code field
    STREET_ADDRESS = "street_address"            # address line 1
    ADDRESS_LINE_2 = "address_line_2"            # apt / suite / unit
    FULL_ADDRESS = "full_address"                # full single-line address
    WILLING_TO_RELOCATE = "willing_to_relocate"
    AVAILABLE_START_DATE = "available_start_date"
    NOTICE_PERIOD = "notice_period"

    # -- Work authorization ----------------------------------------------
    WORK_AUTHORIZED_US = "work_authorized_us"
    REQUIRE_SPONSORSHIP_NOW = "require_sponsorship_now"
    REQUIRE_SPONSORSHIP_FUTURE = "require_sponsorship_future"
    VISA_STATUS = "visa_status"
    CITIZENSHIP = "citizenship"              # country of citizenship (e.g. "India")
    US_CITIZEN = "us_citizen"                # Yes/No — "Are you a U.S. citizen?"

    # -- Experience -------------------------------------------------------
    YOE_LANGUAGE = "yoe_language"           # slot: skill
    YOE_GENERAL = "yoe_general"

    # -- Education --------------------------------------------------------
    DEGREE = "degree"
    SCHOOL = "school"
    MAJOR = "major"
    MINOR = "minor"
    GPA = "gpa"
    GRADUATION_DATE = "graduation_date"
    EXPECTED_GRADUATION = "expected_graduation"

    # -- Compensation -----------------------------------------------------
    SALARY_EXPECTATION = "salary_expectation"
    HOURLY_RATE = "hourly_rate"

    # -- Freeform essays --------------------------------------------------
    WHY_COMPANY = "why_company"             # LLM required, per-job
    WHY_ROLE = "why_role"                   # bank, per-track
    COVER_LETTER_BODY = "cover_letter_body" # LLM
    STRENGTHS = "strengths"
    WEAKNESSES = "weaknesses"

    # -- Referral / source -----------------------------------------------
    HOW_HEARD_ABOUT = "how_heard_about"
    REFERRAL_KNOW_SOMEONE = "referral_know_someone"   # yes/no — "do you know someone here?"
    REFERRAL_NAME = "referral_name"                    # contact name (deterministic bank default)
    REFERRAL_EMAIL = "referral_email"                  # contact email (deterministic bank default)

    # -- Demographics (EEO) -----------------------------------------------
    DEMO_GENDER = "demo_gender"
    DEMO_RACE = "demo_race"
    DEMO_VETERAN = "demo_veteran"
    DEMO_DISABILITY = "demo_disability"
    DEMO_SEXUAL_ORIENTATION = "demo_sexual_orientation"
    DEMO_TRANSGENDER = "demo_transgender"
    DEMO_PRONOUNS = "demo_pronouns"
    DEMO_HISPANIC_LATINO = "demo_hispanic_latino"

    # -- Security clearance (government / defense roles) ----------------
    SECURITY_CLEARANCE_HAVE = "security_clearance_have"
    SECURITY_CLEARANCE_LEVEL = "security_clearance_level"

    # -- Prior / current employment --------------------------------------
    PREVIOUSLY_EMPLOYED = "previously_employed_here"
    CURRENTLY_EMPLOYED = "currently_employed_elsewhere"

    # -- Consents ---------------------------------------------------------
    AGREE_TO_TERMS = "agree_to_terms"
    AGREE_TO_COMMS = "agree_to_comms"
    BACKGROUND_CHECK_CONSENT = "background_check_consent"

    # -- Fallback ---------------------------------------------------------
    UNKNOWN = "unknown"


# Which types should be answered from the parsed `Profile` object rather than
# the bank YAML. These are deterministic facts we already extracted from the
# .tex resumes.
PROFILE_SOURCED: frozenset[QuestionType] = frozenset({
    QuestionType.FIRST_NAME,
    QuestionType.LAST_NAME,
    QuestionType.FULL_NAME,
    QuestionType.PREFERRED_NAME,
    QuestionType.EMAIL,
    QuestionType.PHONE,
    QuestionType.LINKEDIN_URL,
    QuestionType.GITHUB_URL,
    QuestionType.PORTFOLIO_URL,
    QuestionType.WEBSITE_URL,
    QuestionType.SCHOOL,
    QuestionType.DEGREE,
    QuestionType.MAJOR,
    QuestionType.MINOR,
    QuestionType.GPA,
    QuestionType.GRADUATION_DATE,
    QuestionType.EXPECTED_GRADUATION,
    QuestionType.YOE_LANGUAGE,
})

# Types that always require LLM generation (never answer from static data).
LLM_REQUIRED: frozenset[QuestionType] = frozenset({
    QuestionType.WHY_COMPANY,
    QuestionType.COVER_LETTER_BODY,
})

# Types that always require human review (never auto-submit).
# Note: REFERRAL_NAME / REFERRAL_EMAIL are NOT in this set anymore —
# they default to empty / "None" via the bank so we can auto-submit forms
# that ask "Who referred you?" without a referral. When we later pass a
# specific known contact via Profile, that overrides the bank default.
REVIEW_REQUIRED: frozenset[QuestionType] = frozenset({
    QuestionType.UNKNOWN,
    QuestionType.STRENGTHS,
    QuestionType.WEAKNESSES,
})
