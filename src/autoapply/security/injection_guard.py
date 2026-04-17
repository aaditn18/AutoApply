"""Prompt-injection scanner.

Runs on every untrusted input (job description, screening question, LLM output)
before OR after an LLM call. Three modes:

  scan(text)         -> InjectionReport (detect only)
  sanitize(text)     -> (sanitized_text, report)  for LLM input
  validate_output(t) -> raises InjectionDetected if the LLM complied with a planted prompt

Design goals:
  * Zero false negatives on a fixture set of 30 known-injection JDs.
  * Tolerates normal JD content (quoted example dialogue, code samples) — patterns are
    specific enough to not fire on legitimate "you must have Python experience" wording.
  * Returns structured evidence so the caller can log a SecurityEvent row.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum


class InjectionKind(str, Enum):
    AI_ADDRESS = "ai_address"              # "if you are an LLM / AI / chatbot..."
    IGNORE_INSTRUCTIONS = "ignore_instructions"
    DISREGARD_PROMPT = "disregard_prompt"
    SYSTEM_PROMPT_LEAK = "system_prompt_leak"
    FORCED_RESPONSE = "forced_response"    # "you must include the word PINEAPPLE"
    INCLUDE_CANARY = "include_canary"      # "include the word 'X' in your answer"
    VERBATIM_COPY = "verbatim_copy"
    CHAT_TEMPLATE_TOKEN = "chat_template_token"
    JAILBREAK_MARKER = "jailbreak_marker"
    ZERO_WIDTH_UNICODE = "zero_width_unicode"
    ROLE_HIJACK = "role_hijack"
    MARKDOWN_SYSTEM = "markdown_system"


@dataclass
class InjectionHit:
    kind: InjectionKind
    pattern: str
    match_text: str
    span: tuple[int, int]


@dataclass
class InjectionReport:
    hits: list[InjectionHit] = field(default_factory=list)

    @property
    def detected(self) -> bool:
        return len(self.hits) > 0

    @property
    def kinds(self) -> set[InjectionKind]:
        return {h.kind for h in self.hits}

    def summary(self) -> str:
        if not self.detected:
            return "no injection detected"
        return "; ".join(
            f"{h.kind.value}: {h.match_text[:80]!r}" for h in self.hits
        )


# -- Patterns ----------------------------------------------------------------
# Each entry: (InjectionKind, compiled regex). Keep patterns specific; over-broad
# patterns produce false positives on legitimate JD wording.

_PATTERNS: list[tuple[InjectionKind, re.Pattern[str]]] = [
    (
        InjectionKind.AI_ADDRESS,
        re.compile(
            # Two forms:
            #   (a) "if you are (an) AI/LLM/..." — direct conditional
            #   (b) "(note/attention/dear/hey/to) any AI/LLM/... reading/parsing/..."
            r"(?:"
            r"\bif\s+you(?:'re|\s+are)?\s+(?:reading\s+this\s+)?"
            r"(?:an?\s+)?(?:ai|a\.?i\.?|llm|l\.?l\.?m\.?|"
            r"language\s+model|chat\s*bot|chatbot|assistant|bot|"
            r"artificial\s+intelligence|automated\s+(?:agent|system)|"
            r"large\s+language\s+model)\b"
            r"|"
            r"\b(?:note\s+to|attention|dear|hey|hello|to)\s+(?:any\s+|all\s+)?"
            r"(?:ai|llm|language\s+model|chat\s*bot|chatbot|assistant|bot|"
            r"artificial\s+intelligence|automated\s+(?:agent|system)|"
            r"large\s+language\s+model)s?\b"
            r"|"
            r"\bany\s+(?:ai|llm|language\s+model|chat\s*bot|chatbot|assistant|"
            r"bot|artificial\s+intelligence|automated\s+(?:agent|system)|"
            r"large\s+language\s+model)s?\s+"
            r"(?:reading|parsing|processing|generating|writing|answering|"
            r"filling|completing)\b"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.IGNORE_INSTRUCTIONS,
        re.compile(
            r"\bignore\s+(?:all\s+)?(?:previous|prior|above|the\s+above|"
            r"earlier|former|preceding)\s+(?:instructions?|prompts?|directives?|rules?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.DISREGARD_PROMPT,
        re.compile(
            r"\b(?:disregard|forget|override|bypass)\s+(?:your|the|all)\s+"
            r"(?:instructions?|prompt|system|guidelines?|rules?|training)\b",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.SYSTEM_PROMPT_LEAK,
        re.compile(
            r"\b(?:reveal|show|print|output|display|tell\s+me)\s+"
            r"(?:your\s+|the\s+)?system\s*prompt\b",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.FORCED_RESPONSE,
        re.compile(
            r"\byou\s+must\s+(?:include|respond\s+with|reply\s+with|say|write|"
            r"begin\s+with|end\s+with|start\s+with|finish\s+with)\s+"
            # optional "the word/phrase/string/..." framing
            r"(?:the\s+(?:word|phrase|string|token|sentence|keyword|code\s*word)\s+)?"
            # target: quoted string OR ALL_CAPS token OR short alnum
            r"(?:"
            r"['\"`][^'\"`\n]{1,100}['\"`]"
            r"|[A-Z][A-Z0-9_]{2,40}"
            r"|[A-Za-z0-9_\- ]{1,40}"
            r")",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.INCLUDE_CANARY,
        re.compile(
            r"\b(?:include|insert|add|embed|use|place|put)\s+(?:the\s+)?"
            r"(?:word|phrase|string|token|sentence|keyword|code\s*word)\s+"
            # quoted OR ALL_CAPS unquoted token (typical injection canary)
            r"(?:['\"`][^'\"`\n]{1,60}['\"`]|[A-Z][A-Z0-9_]{2,40})",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.VERBATIM_COPY,
        re.compile(
            r"\b(?:copy|repeat|echo|reproduce)\s+(?:the\s+following|the\s+below|this\s+(?:text|word|phrase|sentence|paragraph))\b",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.CHAT_TEMPLATE_TOKEN,
        re.compile(
            r"<\s*(?:\|im_start\||\|im_end\||system|assistant|user|/?s|/?inst)\s*\|?>"
            r"|<\|endoftext\|>|<\|startoftext\|>"
            r"|\[INST\]|\[/INST\]|\[SYSTEM\]",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.JAILBREAK_MARKER,
        re.compile(
            r"\b(?:DAN\s+mode|developer\s+mode|jailbreak|sudo\s+mode|"
            r"do\s+anything\s+now|unrestricted\s+mode|evil\s+mode|"
            r"AIM\s+prompt|STAN\s+prompt)\b",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.ZERO_WIDTH_UNICODE,
        # Zero-width joiner/non-joiner/space, LTR/RTL overrides, BOM, etc.
        re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"),
    ),
    (
        InjectionKind.ROLE_HIJACK,
        re.compile(
            r"(?:^|\n)\s*(?:Role|Speaker|From)\s*[:=]\s*(?:System|Assistant|User|Human|AI)\b",
            re.IGNORECASE,
        ),
    ),
    (
        InjectionKind.MARKDOWN_SYSTEM,
        re.compile(
            r"(?:^|\n)\s*#{1,3}\s*(?:SYSTEM|NEW\s+INSTRUCTIONS?|OVERRIDE|IMPORTANT\s+OVERRIDE|ADMIN)\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
    ),
]


# -- Public API --------------------------------------------------------------


def scan(text: str) -> InjectionReport:
    """Detect injection attempts without modifying the text."""
    if not text:
        return InjectionReport()

    # Normalize for detection only (don't return the normalized form; caller
    # keeps the original so logs/screenshots match the real input). We do use
    # the original for match offsets.
    hits: list[InjectionHit] = []
    for kind, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            hits.append(
                InjectionHit(
                    kind=kind,
                    pattern=pattern.pattern,
                    match_text=m.group(0),
                    span=m.span(),
                )
            )
    return InjectionReport(hits=hits)


def sanitize(text: str) -> tuple[str, InjectionReport]:
    """Return (cleaned_text, report).

    Cleaning steps:
      * Strip zero-width / bidi-override unicode characters outright.
      * Replace each other detected span with the literal string "[REDACTED]"
        so the LLM can still see the context but not the instruction.
      * NFKC-normalize remaining text (merges lookalike unicode).
    """
    if not text:
        return text, InjectionReport()

    # 1) strip zero-width/bidi-override chars first — these never belong in
    #    legitimate text and our other patterns are easier to match without them.
    zw_pattern = _PATTERNS[[k for k, _ in _PATTERNS].index(InjectionKind.ZERO_WIDTH_UNICODE)][1]
    zw_hits = [
        InjectionHit(
            kind=InjectionKind.ZERO_WIDTH_UNICODE,
            pattern=zw_pattern.pattern,
            match_text=m.group(0),
            span=m.span(),
        )
        for m in zw_pattern.finditer(text)
    ]
    cleaned = zw_pattern.sub("", text)

    # 2) NFKC normalize to collapse lookalikes.
    cleaned = unicodedata.normalize("NFKC", cleaned)

    # 3) run the rest of the patterns on the cleaned text, redact spans.
    other_hits: list[InjectionHit] = []
    # Collect spans first so redactions don't shift offsets mid-iteration.
    spans: list[tuple[int, int, InjectionKind, str, str]] = []
    for kind, pattern in _PATTERNS:
        if kind is InjectionKind.ZERO_WIDTH_UNICODE:
            continue
        for m in pattern.finditer(cleaned):
            spans.append((m.start(), m.end(), kind, pattern.pattern, m.group(0)))

    # sort by start desc, redact in place
    spans.sort(key=lambda s: s[0], reverse=True)
    for start, end, kind, pat, matched in spans:
        cleaned = cleaned[:start] + "[REDACTED]" + cleaned[end:]
        other_hits.append(
            InjectionHit(kind=kind, pattern=pat, match_text=matched, span=(start, end))
        )

    # merge reports
    report = InjectionReport(hits=zw_hits + other_hits)
    return cleaned, report


class InjectionDetected(Exception):
    """Raised by validate_output when an LLM response appears to have complied with a planted prompt."""

    def __init__(self, report: InjectionReport):
        self.report = report
        super().__init__(report.summary())


def validate_output(text: str) -> None:
    """Run scan() on LLM output; raise if anything matched.

    Use this after generation (cover letters, essay answers) to catch cases
    where the LLM complied with a planted canary despite prompt hardening.
    """
    report = scan(text)
    if report.detected:
        raise InjectionDetected(report)


__all__ = [
    "InjectionKind",
    "InjectionHit",
    "InjectionReport",
    "InjectionDetected",
    "scan",
    "sanitize",
    "validate_output",
]
