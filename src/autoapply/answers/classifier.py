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
    # affect matching (hyphens in "c++", "c/c++")
    q = q.rstrip(".?!:; ")
    return q


# --- Slot extractors --------------------------------------------------------


_SKILL_ALIASES = {
    "py": "Python",
    "python3": "Python",
    "python": "Python",
    "cpp": "C++",
    "c++": "C++",
    "c/c++": "C++",
    "c plus plus": "C++",
    "c sharp": "C#",
    "c#": "C#",
    "csharp": "C#",
    "golang": "Go",
    "go": "Go",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "node": "Node.js",
    "node.js": "Node.js",
    "nodejs": "Node.js",
    "react": "React",
    "reactjs": "React",
    "java": "Java",
    "kotlin": "Kotlin",
    "swift": "Swift",
    "rust": "Rust",
    "ruby": "Ruby",
    "scala": "Scala",
    "sql": "SQL",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
    "mongodb": "MongoDB",
    "redis": "Redis",
    "aws": "AWS",
    "gcp": "GCP",
    "azure": "Azure",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "docker": "Docker",
    "pytorch": "PyTorch",
    "tensorflow": "TensorFlow",
    "ml": "Machine Learning",
    "nlp": "NLP",
    "cuda": "CUDA",
    "mpi": "MPI",
    "openmp": "OpenMP",
    "sklearn": "Scikit-learn",
    "scikit-learn": "Scikit-learn",
    "pandas": "Pandas",
    "numpy": "NumPy",
}


# Generic/noise words that should never be captured as a skill.
_SKILL_STOPWORDS = frozenset({
    "work", "relevant", "professional", "total", "overall", "prior",
    "general", "past", "full-time", "full time", "industry", "the", "a", "an",
    "experience", "years",
})


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
    (
        QuestionType.ADDRESS_LINE_2,
        re.compile(
            r"address\s+line\s*2|address\s+2(?:nd)?\s+line"
            r"|(?:apt|apartment|suite|unit)(?:\s+(?:#|number|no\.?))?"
            r"|apt/suite|apt\.?\s*/\s*ste"
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
    # State — bare "State*" or "Location (State)" / "State/Province".
    # Written narrowly so it doesn't swallow general "state" prose.
    (
        QuestionType.CURRENT_STATE,
        re.compile(
            r"^state$|^state\s*\*?$"
            r"|location\s*\(?\s*state\s*\)?"
            r"|^state\s*/\s*province$|^state\s+or\s+province$"
        ),
        None,
    ),
    # City — bare "City*" or "Location (City)". Narrow so it doesn't fire
    # on "city and state" (that's CURRENT_LOCATION below).
    (
        QuestionType.CURRENT_CITY,
        re.compile(
            r"^city$|^city\s*\*?$"
            r"|location\s*\(?\s*city\s*\)?"
            r"|^city\s*/\s*town$|^(?:your\s+)?city\s+of\s+residence$"
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
    # Long-form "current location" / "where do you live" / "home address".
    # Checked AFTER the city/state/zip/address specifics above so a bare
    # "City" label doesn't get captured by the broad "your address" regex.
    (
        QuestionType.CURRENT_LOCATION,
        re.compile(
            r"current\s+(?:location|city|address|residence|home\s+address)"
            r"|where\s+(?:do\s+you\s+live|are\s+you\s+based|are\s+you\s+located)"
            r"|where\s+are\s+you\s+currently"
            r"|your\s+(?:current\s+)?(?:mailing\s+)?address"
            r"|(?:home|mailing|residential)\s+address"
            r"|city\s+(?:and\s+state|,\s*state)"
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
        re.compile(r"(?:highest\s+)?degree|qualification|level\s+of\s+education"),
        None,
    ),
    (
        QuestionType.SCHOOL,
        re.compile(r"\b(?:school|university|college|institution)\b|alma\s+mater"),
        None,
    ),
    (
        QuestionType.MAJOR,
        re.compile(r"\b(?:major|field\s+of\s+study|area\s+of\s+study|concentration)\b"),
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
        re.compile(r"^gender$|what\s+is\s+your\s+gender|gender\s+identity"),
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
        re.compile(r"agree\s+to\s+(?:the\s+)?(?:terms|privacy|policy)"),
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
