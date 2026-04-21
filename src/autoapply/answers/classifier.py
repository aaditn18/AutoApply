"""Question-type classifier — deterministic rule table first, embeddings fallback.

Contract:
  classify(raw_question) -> ClassifiedQuestion

  * Rule-table hit  -> confidence=1.0, slot populated when applicable
  * Embedding hit   -> confidence=[0.0, 1.0], requires sentence-transformers
  * Nothing hits    -> UNKNOWN, routed to review queue

The rule table is hand-crafted. Adding a new pattern is safer than fitting a
model — every row is auditable and can be unit-tested. Embeddings are only a
fallback for paraphrases the rules didn't anticipate.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from autoapply.answers.types import QuestionType
from autoapply.rules import load_rules


# -- Public data structure ---------------------------------------------------


@dataclass
class ClassifiedQuestion:
    type: QuestionType
    confidence: float = 0.0          # 1.0 for rule hit; 0.0 for UNKNOWN
    slot: dict[str, str] = field(default_factory=dict)
    match_text: str = ""
    original: str = ""
    source: str = "rule"             # "rule" | "embedding" | "fallback"


# -- Rule table --------------------------------------------------------------
# Each rule is (QuestionType, compiled pattern, optional slot extractor).
# Order matters — earlier rules win. Put more specific patterns first.

SlotFn = Callable[[re.Match[str]], dict[str, str]]


def _norm(q: str) -> str:
    q = q.strip().lower()
    q = re.sub(r"\s+", " ", q)
    # drop trailing punctuation noise; keep internal punctuation that might
    # affect matching (hyphens in "c++", "c/c++"). Also strip the "*" /
    # "(required)" required-field markers that appear on ATS labels so
    # anchored rules ("^city$") still fire on inputs like "City*".
    q = q.rstrip(".?!:; *")
    q = re.sub(r"\s*\(required\)\s*$", "", q)
    q = re.sub(r"\s*\*\s*$", "", q)
    return q


# --- Slot extractors --------------------------------------------------------


# Skill canonicalization + stopwords — loaded from
# state/rules/skill_aliases.yml. Kept at module scope so the YAML parse
# happens once at import. See the rule file's header for the rationale.
_SKILL_RULES = load_rules("skill_aliases")
_SKILL_ALIASES: dict[str, str] = dict(_SKILL_RULES["aliases"])
_SKILL_STOPWORDS: frozenset[str] = frozenset(_SKILL_RULES["stopwords"])


def _canon_skill(raw: str) -> str:
    key = raw.strip().lower()
    return _SKILL_ALIASES.get(key, raw.strip())


def _slot_skill(m: re.Match[str]) -> dict[str, str]:
    raw = m.group("skill")
    canon = _canon_skill(raw)
    return {"skill": canon}


def _is_noise_skill(raw: str) -> bool:
    """True if the captured skill is a generic adjective/noun, not a real tech."""
    key = raw.strip().lower()
    return key in _SKILL_STOPWORDS


# --- Rule list --------------------------------------------------------------
# NOTE: patterns match against the *normalized* lowercase question. Keep rules
# specific enough to avoid cross-type collisions. For YOE_LANGUAGE the skill
# capture group must be named `skill`.

_Rule = tuple[QuestionType, re.Pattern[str], SlotFn | None]

_RULES: list[_Rule] = [
    # -- YOE_LANGUAGE (with explicit skill via preposition) --------------
    # Note: "of" is intentionally excluded from the preposition list —
    # "years of experience" would otherwise match with skill="experience".
    (
        QuestionType.YOE_LANGUAGE,
        re.compile(
            r"(?:how\s+many\s+years|years?\s+of\s+experience|yoe)"
            r"[^?]{0,80}?\b(?:with|in|using)\s+"
            r"(?P<skill>[a-z0-9+/#.\- ]{1,30}?)"
            r"(?:\s*(?:\?|$|\.|,| do you| have you| please))"
        ),
        _slot_skill,
    ),
    # -- YOE_GENERAL — "total/relevant/work/professional years of experience"
    # Must come before the bare-skill YOE_LANGUAGE rule so "work experience"
    # isn't mis-classified with skill="work".
    (
        QuestionType.YOE_GENERAL,
        re.compile(
            r"(?:total|overall|prior|general|past)?\s*years?\s+of"
            r"\s+(?:\w+\s+)*?experience\b"
        ),
        None,
    ),
    # -- YOE_LANGUAGE rule 2 — "<Skill> experience" shorthand -----------
    # Skill must not be one of the generic adjective/nouns below.
    (
        QuestionType.YOE_LANGUAGE,
        re.compile(
            r"\b(?P<skill>[a-z][a-z0-9+/#.\- ]{0,30}?)\s+(?:experience|yoe)\b"
        ),
        _slot_skill,
    ),

    # -- Work authorization ------------------------------------------------
    (
        QuestionType.REQUIRE_SPONSORSHIP_NOW,
        re.compile(
            r"(?:now|currently|presently|at\s+this\s+time|today)\s+"
            r"(?:require|need)\s+(?:visa\s+)?sponsorship"
            r"|sponsorship\s+(?:now|currently|at\s+this\s+time)"
        ),
        None,
    ),
    (
        QuestionType.REQUIRE_SPONSORSHIP_FUTURE,
        re.compile(
            # "... sponsorship in the future/later/eventually/down the road"
            r"sponsor(?:ship)?\s+(?:in\s+the\s+future|eventually|later|down\s+the\s+(?:road|line))"
            # "in the future / later / eventually ... sponsorship"
            r"|(?:future|eventually|later|in\s+the\s+future|down\s+the\s+(?:road|line))"
            r"[^?]{0,40}?sponsor"
            # "anticipate needing sponsorship" (any verb + sponsorship + later)
            r"|(?:anticipat|needing|require)[a-z]*\s+[^?]{0,40}?sponsor(?:ship)?\s+(?:later|in\s+the\s+future|eventually)"
        ),
        None,
    ),
    (
        QuestionType.REQUIRE_SPONSORSHIP_NOW,  # generic "do you require sponsorship"
        re.compile(r"(?:require|need)\s+(?:visa\s+)?sponsorship"),
        None,
    ),
    (
        QuestionType.WORK_AUTHORIZED_US,
        re.compile(
            r"(?:authorized|authorised|eligible|legally\s+able)\s+to\s+work\s+(?:in\s+)?"
            r"(?:the\s+)?(?:us|u\.?s\.?|united\s+states|usa)"
            r"|legal\s+right\s+to\s+work"
            r"|work\s+authorization"
        ),
        None,
    ),
    (
        QuestionType.VISA_STATUS,
        re.compile(r"\bvisa\s+(?:status|type|category)\b|current\s+visa"),
        None,
    ),
    # Yes/No "Are you a U.S. citizen?" must match BEFORE the generic
    # CITIZENSHIP rule — the latter also matches this text. For Aadit the
    # answer is "No" (Indian citizen on F-1 OPT), while CITIZENSHIP asks
    # for the country of citizenship ("India").
    (
        QuestionType.US_CITIZEN,
        re.compile(
            r"are\s+you\s+(?:a\s+)?(?:us|u\.?s\.?|u\.?s\.?a|united\s+states)\s+citizen"
            r"|\b(?:us|u\.?s\.?|u\.?s\.?a)\s+citizen(?:ship)?\s*\?"
            r"|\bu\.?s\.?\s+citizen(?:\s+of\s+the\s+u\.?s\.?)?"
        ),
        None,
    ),
    (
        QuestionType.CITIZENSHIP,
        re.compile(r"\b(?:us\s+)?citizen(?:ship)?\b"),
        None,
    ),

    # -- Contact ----------------------------------------------------------
    (
        QuestionType.EMAIL,
        re.compile(r"^e[- ]?mail(?:\s+address)?$|your\s+e[- ]?mail|email\s+address"),
        None,
    ),
    (
        QuestionType.PHONE,
        re.compile(r"\b(?:phone|mobile|cell)(?:\s+number)?\b|telephone"),
        None,
    ),
    (
        QuestionType.LINKEDIN_URL,
        re.compile(r"linkedin(?:\s+(?:url|profile|link))?"),
        None,
    ),
    (
        QuestionType.GITHUB_URL,
        re.compile(r"github(?:\s+(?:url|profile|link))?"),
        None,
    ),
    (
        QuestionType.PORTFOLIO_URL,
        re.compile(r"portfolio(?:\s+(?:url|link|site))?"),
        None,
    ),
    (
        QuestionType.WEBSITE_URL,
        re.compile(r"(?:personal\s+)?(?:website|blog)(?:\s+url)?"),
        None,
    ),

    # -- Identity ---------------------------------------------------------
    (
        QuestionType.PREFERRED_NAME,
        re.compile(r"preferred\s+(?:first\s+)?name|nickname|what\s+should\s+we\s+call"),
        None,
    ),
    (
        QuestionType.FIRST_NAME,
        re.compile(r"^first\s+name$|your\s+first\s+name|given\s+name"),
        None,
    ),
    (
        QuestionType.LAST_NAME,
        re.compile(r"^last\s+name$|your\s+last\s+name|surname|family\s+name"),
        None,
    ),
    (
        QuestionType.FULL_NAME,
        re.compile(r"^(?:your\s+)?full\s+name$|full\s+legal\s+name|^name$"),
        None,
    ),

    # -- Security clearance (government / defense roles) -----------------
    (
        QuestionType.SECURITY_CLEARANCE_LEVEL,
        re.compile(
            r"(?:clearance|clearance\s+level|security\s+clearance)\s+"
            r"(?:level|type|status|hold|possess)"
            r"|current\s+(?:security\s+)?clearance\s+level"
            r"|what\s+is\s+your\s+(?:current\s+)?(?:security\s+)?clearance"
        ),
        None,
    ),
    (
        QuestionType.SECURITY_CLEARANCE_HAVE,
        re.compile(
            r"(?:do\s+you\s+have|possess|hold)\s+(?:an?\s+)?(?:active\s+)?security\s+clearance"
            r"|active\s+security\s+clearance"
            r"|security\s+clearance\s+(?:held|required|eligible)"
        ),
        None,
    ),

    # -- Location ---------------------------------------------------------
    (
        QuestionType.WILLING_TO_RELOCATE,
        re.compile(
            r"willing\s+to\s+relocate|open\s+to\s+relocation|able\s+to\s+relocate"
            r"|will\s+you\s+relocate"
        ),
        None,
    ),
    # Address line 2 (apt / suite / unit) — checked BEFORE street_address
    # because "address line 2" also contains "address".
    # Word boundaries matter: without \b, "unit" matches inside "United
    # States" and mis-classifies "Are you a veteran of the United States
    # Armed Forces?" as an apartment field.
    (
        QuestionType.ADDRESS_LINE_2,
        re.compile(
            r"address\s+line\s*2|address\s+2(?:nd)?\s+line"
            r"|\b(?:apt|apartment|suite|unit)\b(?:\s+(?:#|number|no\.?))?"
            r"|apt\s*/\s*suite|apt\.?\s*/\s*ste"
        ),
        None,
    ),
    # Street / address line 1 — line1 | street address | mailing-street.
    (
        QuestionType.STREET_ADDRESS,
        re.compile(
            r"street\s+address|address\s+line\s*1"
            r"|^street$|^address$"           # bare labels
            r"|mailing\s+street"
        ),
        None,
    ),
    # Zip / postal — must match bare "ZIP*" labels too.
    (
        QuestionType.CURRENT_ZIP,
        re.compile(
            r"\b(?:zip(?:\s*code)?|postal(?:\s*code)?|postcode)\b"
        ),
        None,
    ),
    # -- City / State / Location --
    # Written as generic families, in order:
    #   STATE (atomic)  → CITY (atomic)  → FULL_ADDRESS  → LOCATION (combined/general)
    # Ordering rationale: atomic-field labels ("City", "State") must win
    # over the broad LOCATION rule so that a single-city input never gets
    # mis-classified as the combined city-and-state form.
    #
    # Shared qualifiers for "which field of residence" questions:
    #   QUALS: current | home | residence | primary | preferred | mailing |
    #          residential | your | present | main
    # Shared verbs for "where do you X" questions:
    #   VERBS: live | reside | based | located | call home | currently …
    # These are inlined into each rule below for readability; a change
    # here means adding the alternate to each of the three rules.

    # State — atomic state-only field.
    # Matches: "State", "State*", "State / Province", "State or Province",
    # "Current/Home/Your/Primary/Preferred/Mailing/Residential state",
    # "State of residence", "What state do you live/reside in?",
    # "Which state are you in?", "In what state do you live?",
    # "Location (State)", "Location - State", "Location: State",
    # "State name", "State code", "State abbreviation".
    (
        QuestionType.CURRENT_STATE,
        re.compile(
            # Bare labels
            r"^state$|^us\s+state$"
            r"|^state\s*(?:/|\s+or\s+)\s*(?:province|territory|region)$"
            r"|^(?:state|us\s+state)\s+(?:name|code|abbreviation|abbr)$"
            # Qualified forms: "current state", "home state", …
            r"|\b(?:current(?:ly)?|home|your|primary|preferred|mailing|residential|present|main|residence)\s+state\b"
            # State of residence / state of residency / state you live in
            r"|\bstate\s+(?:of\s+(?:(?:your\s+|current\s+|primary\s+)?(?:residence|residency|living)"
            r"|(?:the\s+)?us\s+you\s+(?:live|reside))"
            r"|you\s+(?:currently\s+)?(?:live|reside|are\s+based|are\s+located|call\s+home)\s+in)\b"
            # "What/which state do you live/reside/are based in"
            r"|\b(?:what|which|in\s+what|in\s+which)\s+state\s+(?:do\s+you|are\s+you)\b"
            # "Please enter / provide / specify your state"
            r"|\b(?:enter|provide|specify|select)\s+(?:your\s+)?state\b"
            # Parenthetical: "Location (State)", "Location - State", "Location: State"
            r"|\blocation\s*[\-\u2013\u2014\(:/]\s*state\b"
        ),
        None,
    ),
    # City — atomic city-only field.
    # Matches: "City", "City*", "City/Town", "City or Town", "Town", "Town/City",
    # "Current/Home/Your/Primary/Preferred/Mailing/Residential city",
    # "City of residence / residency", "What city do you live/reside in?",
    # "Which city are you (currently) (based|located) in?",
    # "In what city do you live?", "City you live in", "City name",
    # "Location (City)", "Location - City", "Location: City",
    # "Nearest city", "Metro area" (treated as CITY for ATS purposes).
    #
    # Explicitly does NOT match "city and state", "city, state",
    # "city / state" — those fall through to CURRENT_LOCATION.
    (
        QuestionType.CURRENT_CITY,
        re.compile(
            # Bare labels — exclude if "and"/"/"/","/"or" follows "city" (combined form)
            r"^city$"
            r"|^city\s*(?:/|\s+or\s+)\s*town$|^town\s*(?:/|\s+or\s+)\s*city$|^town$"
            r"|^(?:nearest\s+)?(?:major\s+)?(?:city|metro(?:politan)?(?:\s+area)?)$"
            r"|^city\s+name$"
            # Qualified forms: "current city", "home city", …
            r"|\b(?:current(?:ly)?|home|your|primary|preferred|mailing|residential|present|main|residence|nearest)\s+city\b"
            # City of residence / of residency / you live in
            r"|\bcity\s+(?:of\s+(?:(?:your\s+|current\s+|primary\s+)?(?:residence|residency|living))"
            r"|you\s+(?:currently\s+)?(?:live|reside|are\s+based|are\s+located|call\s+home)\s+in)\b"
            # "What/which city do you live/reside in" — must NOT be
            # followed by "and state" / ", state" (that is LOCATION combined).
            r"|\b(?:what|which|in\s+what|in\s+which)\s+city(?!\s*(?:,|/|\s+and|\s+or)\s*state)"
            r"\s+(?:do\s+you|are\s+you|you(?:\s+(?:live|reside|are|call))?)\b"
            # "Please enter / provide / specify / select your city"
            r"|\b(?:enter|provide|specify|select)\s+(?:your\s+)?city\b"
            # Parenthetical: "Location (City)", "Location - City", "Location: City"
            # But NOT "Location (City, State)" — that's LOCATION. Enforced by
            # the negative lookahead on `,`/`and`/`/` + state/province.
            r"|\blocation\s*[\-\u2013\u2014\(:/]\s*city(?!\s*(?:,|/|\s+and|\s+or)\s*(?:state|province))\b"
        ),
        None,
    ),
    # Full single-line address.
    (
        QuestionType.FULL_ADDRESS,
        re.compile(
            r"full\s+address|complete\s+address|^(?:your\s+)?(?:full\s+)?mailing\s+address$"
        ),
        None,
    ),
    # Long-form / combined "current location" / "where do you live".
    # Matches broadly — the atomic CITY / STATE / ZIP / ADDRESS rules above
    # have already fired and returned if they hit.
    #
    # Covers:
    #   - Qualified location: "current location", "home location",
    #     "primary location", "preferred location", "residence location",
    #     "your location".
    #   - "Where" questions: "where do you live", "where are you based",
    #     "where are you located", "where do you currently reside",
    #     "where are you currently (based|located|living|residing)",
    #     "where do you call home", "where are you from" (treated as
    #     current-location since ATS forms don't distinguish), "where
    #     can we reach you".
    #   - Generic address: "your address", "home address", "mailing
    #     address", "residential address", "current address".
    #   - Combined: "city and state", "city, state", "city/state",
    #     "city or state", "Location (City, State)",
    #     "Location (City, State, Country)".
    #   - Bare label: "Location", "Location*".
    (
        QuestionType.CURRENT_LOCATION,
        re.compile(
            # Qualified location: "current location", "home location", etc.
            r"\b(?:current(?:ly)?|home|primary|preferred|residence|residential|your|present|main)\s+location\b"
            # "Where" questions — all tenses of live/reside/based/located/call-home.
            r"|\bwhere\s+(?:do|are|have)\s+you\s+(?:(?:currently|presently|now)\s+)?"
            r"(?:live|living|reside|residing|based|located|situated|call\s+home|from|been\s+living)\b"
            r"|\bwhere\s+(?:are\s+you\s+)?currently\s+(?:based|located|living|residing)\b"
            r"|\bwhere\s+can\s+we\s+(?:reach|contact|find)\s+you\b"
            # Generic address phrasings
            r"|\byour\s+(?:current\s+)?(?:mailing\s+|home\s+|residential\s+)?address\b"
            r"|\b(?:home|mailing|residential|current|permanent)\s+address\b"
            # Combined city + state / country
            r"|\bcity\s*(?:,|/|\s+and|\s+or)\s*state\b"
            r"|\bcity\s*(?:,|/)\s*state\s*(?:,|/)\s*country\b"
            # Parenthetical combined: "Location (City, State)", "Location - City, State"
            r"|\blocation\s*[\-\u2013\u2014\(:/]\s*city\s*(?:,|/|\s+and|\s+or)\s*(?:state|province)\b"
            # Bare "Location" label (last resort — fires only when nothing else did)
            r"|^location$"
        ),
        None,
    ),

    # -- Availability / timing -------------------------------------------
    (
        QuestionType.AVAILABLE_START_DATE,
        re.compile(
            r"(?:earliest|expected|available)\s+start\s+date"
            r"|when\s+(?:can|could)\s+you\s+start"
            r"|start\s+date\s+preference"
        ),
        None,
    ),
    (
        QuestionType.NOTICE_PERIOD,
        re.compile(r"notice\s+period|how\s+(?:much|many\s+weeks?)\s+of?\s+notice"),
        None,
    ),

    # -- Education --------------------------------------------------------
    (
        QuestionType.GPA,
        re.compile(r"\bgpa\b|grade\s+point\s+average|cumulative\s+gpa"),
        None,
    ),
    (
        QuestionType.DEGREE,
        re.compile(
            r"(?:highest\s+)?degree"
            r"|qualification"
            r"|level\s+of\s+(?:\w+\s+)*education"
            r"|highest\s+(?:level\s+of\s+)?(?:\w+\s+){0,3}education"
            r"|education\s+(?:level|attained|completed)"
        ),
        None,
    ),
    (
        QuestionType.SCHOOL,
        re.compile(r"\b(?:school|university|college|institution)\b|alma\s+mater"),
        None,
    ),
    (
        QuestionType.MAJOR,
        re.compile(
            r"\b(?:major|field\s+of\s+study|area\s+of\s+study|"
            r"concentration|discipline|course\s+of\s+study)\b"
        ),
        None,
    ),
    (
        QuestionType.MINOR,
        re.compile(r"\bminor\b(?!\s+(?:league|detail|issue))"),
        None,
    ),
    (
        QuestionType.EXPECTED_GRADUATION,
        re.compile(r"(?:expected|anticipated)\s+graduation|graduat(?:ion|ing)\s+(?:date|year)"),
        None,
    ),
    (
        QuestionType.GRADUATION_DATE,
        re.compile(r"graduation\s+date|when\s+(?:did|will)\s+you\s+graduate"),
        None,
    ),

    # -- Compensation -----------------------------------------------------
    (
        QuestionType.SALARY_EXPECTATION,
        re.compile(
            r"salary\s+(?:expectation|requirement|range)"
            r"|compensation\s+expectation"
            r"|desired\s+(?:salary|compensation|pay)"
            r"|expected\s+(?:salary|compensation)"
        ),
        None,
    ),
    (
        QuestionType.HOURLY_RATE,
        re.compile(r"hourly\s+(?:rate|pay)|rate\s+per\s+hour"),
        None,
    ),

    # -- Demographics -----------------------------------------------------
    (
        QuestionType.DEMO_HISPANIC_LATINO,
        re.compile(r"hispanic|latino|latina|latinx"),
        None,
    ),
    (
        QuestionType.DEMO_GENDER,
        re.compile(
            r"^gender$|what\s+is\s+your\s+gender|gender\s+identity"
            # "I identify my gender as:" / "Gender:"
            r"|identify\s+(?:my\s+)?gender"
            # Mthree / Greenhouse-EEO variants
            r"|my\s+gender\s+(?:is|identity)"
        ),
        None,
    ),
    (
        QuestionType.DEMO_RACE,
        re.compile(r"^race$|race(?:/ethnicity|\s+or\s+ethnicity)?|ethnicity"),
        None,
    ),
    (
        QuestionType.DEMO_VETERAN,
        re.compile(r"veteran\s+status|protected\s+veteran|are\s+you\s+a\s+veteran"),
        None,
    ),
    (
        QuestionType.DEMO_DISABILITY,
        re.compile(r"disability\s+status|do\s+you\s+have\s+a\s+disability"),
        None,
    ),
    (
        QuestionType.DEMO_SEXUAL_ORIENTATION,
        re.compile(r"sexual\s+orientation"),
        None,
    ),
    (
        QuestionType.DEMO_TRANSGENDER,
        re.compile(r"transgender|gender\s+identity\s+different"),
        None,
    ),
    (
        QuestionType.DEMO_PRONOUNS,
        re.compile(r"\bpronouns?\b|preferred\s+pronoun"),
        None,
    ),

    # -- Military service (distinct from EEO veteran status) -------------
    # Covers "Are you in/have you served in the military", "Military
    # Service*" (jjsnackfoods), "Served in the US Armed Forces?",
    # "Active duty", etc. This is the "are you currently or have you
    # ever been in the military" flavor — NOT the EEO-veteran self-ID
    # question which is ``demo_veteran`` above. Order-sensitive: must
    # come AFTER demo_veteran so the EEO form's "protected veteran"
    # phrasing matches there.
    (
        QuestionType.MILITARY_SERVICE,
        re.compile(
            r"^military\s+service$"
            r"|(?:have\s+you\s+)?served\s+(?:in\s+the\s+)?(?:us\s+|u\.?s\.?\s+)?(?:armed\s+forces|military)"
            r"|active\s+(?:duty|military)"
            r"|(?:are\s+you\s+)?(?:currently\s+)?in\s+the\s+(?:us\s+)?(?:armed\s+forces|military)"
            r"|military\s+(?:experience|background|history)"
            r"|(?:prior|current|former)\s+military"
        ),
        None,
    ),

    # -- Permanent work authorization (green-card-level) -----------------
    # Distinct from ``work_authorized_us`` (which OPT satisfies) — this
    # one specifically asks about PERMANENT authorization (green card
    # or citizenship). For an F-1 OPT student the answer is always No.
    (
        QuestionType.PERMANENT_WORK_AUTHORIZATION,
        re.compile(
            r"permanent\s+(?:(?:and\s+)?unrestricted\s+)?(?:right|authorization)\s+to\s+work"
            r"|have\s+(?:the\s+)?permanent\s+(?:right\s+)?(?:to\s+)?work"
            r"|permanent(?:ly)?\s+authorized\s+to\s+work"
            r"|lawfully\s+authorized\s+to\s+work\s+.*?permanent"
        ),
        None,
    ),

    # -- Willing to work from a specific location / office --------------
    # "Are you willing to work from our Sterling, VA office?" /
    # "This role is work from home but requires you to be based out of X"
    # / "Are you able to work onsite 4 days/week" — all route to a
    # Yes/No answer that defaults to Yes per the "answer subjective
    # willingness questions positively" policy.
    (
        QuestionType.WILLING_WORK_LOCATION,
        re.compile(
            r"willing\s+to\s+work\s+(?:from|at|in)\s+(?:our|the)"
            r"|based\s+out\s+of\s+(?:our\s+)?\w+,?\s+\w+"
            r"|(?:able|willing)\s+to\s+(?:work|be)\s+(?:onsite|on[- ]?site|in[- ]?(?:office|person))"
            r"|(?:this\s+role\s+)?requires\s+(?:you\s+to\s+be\s+)?(?:onsite|on[- ]?site|in[- ]?(?:office|person))"
            r"|work\s+(?:from|at)\s+(?:our\s+)?(?:office|hq|headquarters)"
            r"|onsite\s+\d+\s+days?"
        ),
        None,
    ),

    # -- Prior / current employment --------------------------------------
    (
        QuestionType.PREVIOUSLY_EMPLOYED,
        re.compile(
            r"(?:previously|formerly|ever)\s+(?:worked|employed)\s+(?:at|for|here|with\s+us)"
            r"|have\s+you\s+worked\s+(?:at|for|here|with\s+us)"
        ),
        None,
    ),
    (
        QuestionType.CURRENTLY_EMPLOYED,
        re.compile(r"currently\s+employed|are\s+you\s+(?:currently\s+)?working"),
        None,
    ),

    # -- Referral / source -----------------------------------------------
    (
        QuestionType.HOW_HEARD_ABOUT,
        re.compile(
            r"how\s+(?:did\s+you|do\s+you)\s+hear\s+about|where\s+did\s+you\s+hear"
            r"|how\s+did\s+you\s+find\s+(?:us|this)"
        ),
        None,
    ),
    # "Do you know someone here?" / "Were you referred?" — yes/no style.
    # Must match BEFORE REFERRAL_NAME so "were you referred by an employee?"
    # (which contains "referred by") routes to the yes/no slot, not the
    # name-of-referrer slot.
    (
        QuestionType.REFERRAL_KNOW_SOMEONE,
        re.compile(
            r"do\s+you\s+know\s+(?:someone|anyone|any\s+(?:current|existing))"
            r"|know\s+anyone\s+(?:at|who\s+works)"
            r"|were\s+you\s+referred(?:\s+by\s+(?:an?\s+)?(?:employee|someone))?\??"
            r"|have\s+(?:you\s+been\s+)?referred"
            r"|are\s+you\s+(?:being\s+)?referred"
            r"|do\s+you\s+have\s+a\s+referral"
        ),
        None,
    ),
    (
        QuestionType.REFERRAL_NAME,
        re.compile(
            r"referr(?:al|ed\s+by|er)\s+(?:name|employee|person)"
            r"|who\s+referred\s+you"
            r"|name\s+of\s+(?:the\s+)?(?:person\s+who\s+)?referr"
            r"|employee\s+(?:who\s+)?referr(?:ed)?"
        ),
        None,
    ),
    (
        QuestionType.REFERRAL_EMAIL,
        re.compile(
            r"referr(?:al|ed|er)\s+e[- ]?mail"
            r"|e[- ]?mail\s+of\s+(?:the\s+)?(?:person\s+who\s+)?referr"
        ),
        None,
    ),

    # -- Essays -----------------------------------------------------------
    (
        QuestionType.WHY_COMPANY,
        re.compile(
            r"why\s+(?:do\s+you\s+)?(?:want\s+to\s+)?(?:work|join|apply)\s+(?:at|for|with)\s+(?:us|our\s+company|\w)"
            r"|why\s+are\s+you\s+interested\s+in\s+(?:our\s+company|us|working\s+at)"
            r"|what\s+(?:draws|attracts)\s+you\s+to\s+(?:us|our\s+company)"
        ),
        None,
    ),
    (
        QuestionType.WHY_ROLE,
        re.compile(
            r"why\s+(?:are\s+you\s+)?(?:interested\s+in|do\s+you\s+want|excited\s+about)\s+this\s+(?:role|position|job|opportunity)"
            r"|why\s+this\s+(?:role|position|job)"
        ),
        None,
    ),
    (
        QuestionType.COVER_LETTER_BODY,
        re.compile(r"cover\s+letter"),
        None,
    ),
    (
        QuestionType.STRENGTHS,
        re.compile(r"\b(?:greatest|your)\s+strength|what\s+are\s+you\s+good\s+at"),
        None,
    ),
    (
        QuestionType.WEAKNESSES,
        re.compile(r"\b(?:biggest|your)\s+weakness|area\s+for\s+improvement"),
        None,
    ),

    # -- Consents ---------------------------------------------------------
    (
        QuestionType.BACKGROUND_CHECK_CONSENT,
        re.compile(r"background\s+check|consent\s+to\s+(?:a\s+)?background"),
        None,
    ),
    (
        QuestionType.AGREE_TO_COMMS,
        re.compile(
            r"agree\s+to\s+(?:receive|be\s+contacted)|opt[- ]?in\s+(?:to|for)\s+(?:emails?|comms|communication)"
        ),
        None,
    ),
    (
        QuestionType.AGREE_TO_TERMS,
        re.compile(
            # Canonical forms
            r"agree\s+to\s+(?:the\s+)?(?:terms|privacy|policy)"
            # "I consent to..." / "I agree that..." / "By checking this box"
            r"|\bi\s+(?:agree|consent|accept|acknowledge)\b"
            r"|by\s+(?:checking|clicking|submitting)\s+(?:this\s+)?(?:box|information|your\s+info)"
            # "Please accept the terms" / "Accept the terms"
            r"|(?:please\s+)?accept\s+(?:the\s+)?(?:terms|conditions|privacy|policy|agreement)"
            # "Read and agree to the privacy notice"
            r"|(?:read\s+(?:and\s+)?(?:agree|accept))"
        ),
        None,
    ),
]


# -- Entry point -------------------------------------------------------------


def classify(raw: str) -> ClassifiedQuestion:
    """Classify a single question string. Returns UNKNOWN if no rule hits."""
    norm = _norm(raw)
    if not norm:
        return ClassifiedQuestion(type=QuestionType.UNKNOWN, original=raw)

    for qt, pattern, slotfn in _RULES:
        m = pattern.search(norm)
        if not m:
            continue
        slot = slotfn(m) if slotfn else {}
        # Reject YOE_LANGUAGE matches where the captured skill is a generic
        # stopword ("work", "relevant", etc.) — let a later rule handle it.
        if qt is QuestionType.YOE_LANGUAGE and "skill" in slot and _is_noise_skill(m.group("skill")):
            continue
        return ClassifiedQuestion(
            type=qt,
            confidence=1.0,
            slot=slot,
            match_text=m.group(0),
            original=raw,
            source="rule",
        )

    # Embedding fallback — only if sentence-transformers is installed and a
    # canonical-examples file exists. Otherwise return UNKNOWN.
    emb = _embedding_fallback(raw, norm)
    if emb is not None:
        return emb

    return ClassifiedQuestion(type=QuestionType.UNKNOWN, original=raw, source="fallback")


# -- Embedding fallback (optional) -------------------------------------------


def _embedding_fallback(raw: str, norm: str) -> ClassifiedQuestion | None:
    """Optional embedding-based classifier.

    Gracefully returns None if sentence-transformers isn't available. When
    implemented, this would load canonical examples from
    `classifier_examples.yml` and cosine-match at threshold ≥ 0.85.
    """
    # Deliberately not importing sentence_transformers at module load — it's
    # heavy and we don't want to pay the cost in CI unit tests.
    return None
