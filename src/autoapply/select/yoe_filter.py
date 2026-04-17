"""YOE (years of experience) hard filter.

Extracts the *minimum* stated years-of-experience requirement from a job
description and determines whether a new-grad applicant is eligible.

Design goals:
  * Never reject on ambiguity — if we can't find a clear requirement, keep.
  * Only reject on EXPLICIT minimum signals (3+, at least 3, minimum 3, 3-5 range).
  * Treat "preferred / nice-to-have" mentions as non-requirements.
  * Avoid false positives from company-age or team-tenure sentences.

Contract:
  extract_min_yoe(text) -> int | None   # None means "not stated / ambiguous"
  is_yoe_eligible(text) -> bool         # False only when min > MAX_NEW_GRAD_YOE
"""

from __future__ import annotations

import re

# Maximum YOE Aadit qualifies as (intern + research, new grad May 2026).
# Jobs that explicitly require more than this are rejected with `rejected_by_yoe`.
MAX_NEW_GRAD_YOE: int = 2


# ── Soft-requirement markers ────────────────────────────────────────────────
# If one of these words appears within _SOFT_WINDOW chars BEFORE a YOE match,
# the match is treated as a preference — not a hard requirement — and skipped.

_SOFT_WINDOW: int = 80
_SOFT_RE = re.compile(
    r"\b(?:"
    r"preferred?|ideally?|nice[\s\-]?to[\s\-]?have|a\s+plus|plus\s+if|bonus\s+if|"
    r"optionally?|desired|would\s+be\s+great|wish[\s\-]?list|love\s+to\s+see|"
    r"consider\s+a\s+plus|if\s+you\s+(?:also\s+)?have|although\s+not\s+required|"
    r"not\s+required"
    r")\b",
    re.IGNORECASE,
)
# Sentence boundaries — if one of these separators appears between a soft-marker
# and the YOE match, the soft marker belongs to a different sentence and should
# not suppress the hard-requirement match.
_SENTENCE_SEP_RE = re.compile(r"(?:[.!?;]\s|\n)", re.IGNORECASE)


# ── Strong patterns (fire regardless of surrounding context) ─────────────────
# "Strong" means the phrasing itself implies a hard minimum.
# Each pattern captures the minimum year count in group(1).

_STRONG: list[re.Pattern[str]] = [
    # "3+ years of experience" / "5+ years expertise" / "5+ years working"
    re.compile(
        r"\b([1-9]\d?)\+\s*years?\s+"
        r"(?:of\s+)?(?:\w+\s+){0,3}"
        r"(?:experience|expertise|background|working|work\s+experience|exp)\b",
        re.IGNORECASE,
    ),
    # "minimum (of) 4 years" / "at least 3 years" / "no less than 5 years"
    re.compile(
        r"\b(?:minimum\s+(?:of\s+)?|at\s+least\s+|no\s+less\s+than\s+)"
        r"([1-9]\d?)\s*\+?\s*years?\b",
        re.IGNORECASE,
    ),
    # "N or more years"
    re.compile(
        r"\b([1-9]\d?)\s+or\s+more\s+years?\b",
        re.IGNORECASE,
    ),
    # Range "3-5 years of experience" / "3–7 years experience"
    # Take the lower bound as the minimum.
    re.compile(
        r"\b([1-9]\d?)\s*[-\u2013\u2014]\s*[1-9]\d?\s+years?"
        r"\s+(?:of\s+)?(?:\w+\s+){0,2}experience\b",
        re.IGNORECASE,
    ),
    # "N years of experience required/needed/minimum" — has its own embedded
    # requirement word, no need for pre-context check.
    re.compile(
        r"\b([1-9]\d?)\s+years?\s+of\s+experience\s+"
        r"(?:required|needed|minimum|is\s+required)\b",
        re.IGNORECASE,
    ),
    # "N years experience required" (no "of") — same, embedded requirement.
    re.compile(
        r"\b([1-9]\d?)\s+years?\s+experience\s+"
        r"(?:required|needed|minimum)\b",
        re.IGNORECASE,
    ),
]


# ── Context-dependent patterns (require a nearby requirement word) ────────────
# These patterns are plausible but not self-sufficient — we only fire them
# when a requirement word appears within _REQ_CONTEXT_WINDOW chars before.

_CONTEXTUAL: list[re.Pattern[str]] = [
    # "N years of professional/relevant/industry/work experience"
    re.compile(
        r"\b([1-9]\d?)\s+years?\s+of\s+"
        r"(?:professional|relevant|industry|hands[\-]?on|direct|work|related|"
        r"progressive|demonstrated)\s+experience\b",
        re.IGNORECASE,
    ),
]

# Requirement-context words: if one of these appears within
# _REQ_CONTEXT_WINDOW chars BEFORE a contextual match, we accept the match.
_REQ_CONTEXT_RE = re.compile(
    r"\b(?:"
    r"require[sd]?|must\s+have|must\s+possess|"
    r"you\s+(?:have|bring|will\s+have|need\s+to\s+have|should\s+have)|"
    r"qualifications?|requirements?|what\s+you(?:'ll|\s+will)?\s+(?:bring|need|have)|"
    r"we\s+(?:need|require|expect|are\s+looking\s+for)|"
    r"the\s+(?:role|position)\s+requires?|candidates?\s+(?:must|should)\s+have|"
    r"successful\s+candidate"
    r")\b",
    re.IGNORECASE,
)
_REQ_CONTEXT_WINDOW: int = 300


# ── Internal helpers ─────────────────────────────────────────────────────────


def _has_soft_marker(text: str, match_start: int) -> bool:
    """Return True if a soft-requirement word precedes this match in the same sentence.

    A soft marker from a *previous* sentence (separated by . ! ? ; or newline)
    does not count — only markers within the same sentence segment do.
    """
    window = text[max(0, match_start - _SOFT_WINDOW): match_start]
    # If there's a sentence boundary inside the window, only look at the text
    # AFTER the last boundary (i.e., within the same sentence as the match).
    seps = list(_SENTENCE_SEP_RE.finditer(window))
    if seps:
        last_sep = seps[-1]
        window = window[last_sep.end():]
    return bool(_SOFT_RE.search(window))


def _safe_group1_int(m: re.Match[str]) -> int | None:
    """Return int(group(1)) if valid, else None."""
    try:
        return int(m.group(1))
    except (IndexError, ValueError):
        return None


# ── Public API ────────────────────────────────────────────────────────────────


def extract_min_yoe(text: str | None) -> int | None:
    """Extract the minimum years-of-experience explicitly stated as a requirement.

    Returns:
        The minimum as int (e.g., 3 for "3+ years"), or None if not stated /
        ambiguous.  Never returns 0.  Caps at 30 to avoid garbage matches.
    """
    if not text:
        return None

    # Collapse whitespace to simplify span arithmetic.
    text = " ".join(text.split())

    best: int | None = None

    # --- Phase 1: strong-signal patterns ---
    for pat in _STRONG:
        for m in pat.finditer(text):
            if _has_soft_marker(text, m.start()):
                continue
            y = _safe_group1_int(m)
            if y is None or not (1 <= y <= 30):
                continue
            if best is None or y < best:
                best = y

    if best is not None:
        return best

    # --- Phase 2: context-dependent patterns ---
    for pat in _CONTEXTUAL:
        for m in pat.finditer(text):
            if _has_soft_marker(text, m.start()):
                continue
            # Require a requirement-context word before the match.
            window = text[max(0, m.start() - _REQ_CONTEXT_WINDOW): m.start()]
            if not _REQ_CONTEXT_RE.search(window):
                continue
            y = _safe_group1_int(m)
            if y is None or not (1 <= y <= 30):
                continue
            if best is None or y < best:
                best = y

    return best


def is_yoe_eligible(text: str | None) -> bool:
    """Return True if the job's YOE requirement is within new-grad range.

    Returns True (keep) when:
      - No explicit YOE requirement stated (None → never reject on ambiguity)
      - Stated minimum ≤ MAX_NEW_GRAD_YOE (2 years)

    Returns False (reject) when:
      - Stated minimum > MAX_NEW_GRAD_YOE
    """
    min_yoe = extract_min_yoe(text)
    if min_yoe is None:
        return True
    return min_yoe <= MAX_NEW_GRAD_YOE
