"""Resume-track picker.

Decides which of `swe|ml|hpc|quant` to submit for a given job. Uses title +
description, NEVER a hardcoded firm list (design decision — see plan §
"No predetermined quant firm list").

Decision ladder:
  1. Strong title keywords (unambiguous signal).
  2. Quant-in-description promotion (generic title + trading-heavy desc).
  3. Skill-overlap with each resume_track's skills.
  4. LLM tiebreaker (optional, injection-guarded).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from autoapply.profile.schema import Profile

Track = Literal["swe", "ml", "hpc", "quant"]


# -- Title keyword tables ----------------------------------------------------


_QUANT_STRONG_TITLE = (
    "quant", "quantitative", "trader", "trading", "hft",
    "market maker", "market-maker", "market making",
    "prop ", "proprietary trading", "systematic",
    "derivatives trading", "alpha researcher",
)

_HPC_STRONG_TITLE = (
    "cuda", "gpu ", "kernel engineer", "hpc",
    "performance eng", "compiler", "systems performance",
)

_ML_STRONG_TITLE = (
    "ml engineer", "machine learning", "ai engineer",
    "research engineer", "nlp", "computer vision",
    "llm ", "deep learning", "mlops",
    # Data-science titles — "data scientist" is ML-adjacent
    "data scientist", "data science",
    # Applied AI / generative AI roles
    "applied scientist", "applied ml", "generative ai",
)

_SWE_STRONG_TITLE = (
    "software engineer", "swe", "backend", "front end", "frontend",
    "full stack", "full-stack", "platform engineer",
    "infrastructure", "sre", "site reliability",
    # Broader engineering/developer titles
    "software developer", "application developer", "applications developer",
    "web developer", "web engineer", "cloud engineer", "cloud developer",
    "systems engineer", "systems developer",
    "devops", "devsecops", "dev ops",
    "data engineer",          # pipelines/infra → closer to SWE than ML
    # Generic "developer" covers roles like "Cyber Support Developer", etc.
    # Must come LAST so specific titles above are checked first.
    " developer",             # space-prefix avoids matching "front-end developer" wrong
)


# -- Description-signal weighting for quant promotion -----------------------
# Each tuple is a list of synonyms; if any synonym is in the desc, the tuple
# "fires" and contributes weight. Strong=2 (first 15 tuples), Medium=1 (rest).

_QUANT_DESC_STRONG = (
    ("alpha",),
    ("market making", "market-making"),
    ("statistical arbitrage", "stat arb"),
    ("latency arbitrage",),
    ("order book",),
    ("tick data",),
    ("fix protocol",),
    ("market microstructure",),
    ("systematic trading",),
    ("factor model",),
    ("backtest", "back-test", "backtesting"),
    ("sharpe",),
    ("pnl", "p&l"),
    ("execution algorithm", "execution algos"),
    ("low-latency trading", "low latency trading"),
)

_QUANT_DESC_MEDIUM = (
    ("derivatives",),
    ("options pricing",),
    ("volatility",),
    ("portfolio optimization",),
    ("hedging",),
    ("monte carlo",),
    ("basis points", "bps"),
    ("liquidity",),
    ("bid-ask", "bid ask"),
    ("risk-adjusted return", "risk adjusted return"),
    ("trading strategy", "trading strategies"),
    ("quantitative research",),
)

_QUANT_PROMOTE_THRESHOLD = 4


# -- Output dataclass -------------------------------------------------------


@dataclass
class TrackDecision:
    track: Track | None         # None → tie or injection; caller decides
    reason: str                 # human-readable rationale
    stage: str                  # "title" | "desc_promote" | "skills" | "llm" | "tie" | "injection"
    quant_weight: int = 0
    scores: dict[str, float] | None = None  # skill-overlap scores when relevant


# -- Scoring helpers --------------------------------------------------------


def _title_track(title: str) -> Track | None:
    """Return a track if the title contains an unambiguous keyword."""
    t = title.lower()
    if any(k in t for k in _QUANT_STRONG_TITLE):
        return "quant"
    if any(k in t for k in _HPC_STRONG_TITLE):
        return "hpc"
    if any(k in t for k in _ML_STRONG_TITLE):
        return "ml"
    if any(k in t for k in _SWE_STRONG_TITLE):
        return "swe"
    return None


def _quant_desc_weight(desc: str) -> int:
    """Sum of quant-description signals. Strong=2, medium=1."""
    d = desc.lower()
    weight = 0
    for group in _QUANT_DESC_STRONG:
        if any(g in d for g in group):
            weight += 2
    for group in _QUANT_DESC_MEDIUM:
        if any(g in d for g in group):
            weight += 1
    return weight


def _skill_overlap_scores(
    desc: str,
    profiles_by_track: dict[str, Profile],
) -> dict[str, float]:
    """For each track, fraction of that track's skills that appear in desc.

    Simple normalized match: count / len(skills). Good enough for a
    tiebreaker — the layers above handle strong signals.
    """
    d = desc.lower()
    scores: dict[str, float] = {}
    for track, profile in profiles_by_track.items():
        skills = profile.skills.all()
        if not skills:
            scores[track] = 0.0
            continue
        hits = sum(1 for s in skills if s.lower() in d)
        scores[track] = hits / len(skills)
    return scores


# -- Public entry point -----------------------------------------------------


def pick_track(
    title: str,
    description: str,
    profiles_by_track: dict[str, Profile],
    *,
    injection_detected: bool = False,
    llm_tiebreaker: "callable | None" = None,
) -> TrackDecision:
    """Decide which resume_track fits. Returns a TrackDecision (track may be
    None if ambiguous or injection-flagged — caller routes to review)."""
    if injection_detected:
        return TrackDecision(track=None, reason="injection_detected", stage="injection")

    # 1. Strong title keywords
    t = _title_track(title)
    if t is not None:
        # Even with a SWE title, upgrade to quant if description is
        # saturated with trading signals.
        if t != "quant":
            w = _quant_desc_weight(description)
            if w >= _QUANT_PROMOTE_THRESHOLD:
                return TrackDecision(
                    track="quant",
                    reason=f"title={title!r} → {t}, but quant desc-weight={w} ≥ {_QUANT_PROMOTE_THRESHOLD}",
                    stage="desc_promote",
                    quant_weight=w,
                )
        return TrackDecision(track=t, reason=f"strong title keyword", stage="title")

    # 2. Quant-in-description promotion (generic title)
    w = _quant_desc_weight(description)
    if w >= _QUANT_PROMOTE_THRESHOLD:
        return TrackDecision(
            track="quant",
            reason=f"generic title + quant desc-weight={w}",
            stage="desc_promote",
            quant_weight=w,
        )

    # 3. Skill-overlap scoring
    scores = _skill_overlap_scores(description, profiles_by_track)
    if scores:
        sorted_scores = sorted(scores.values(), reverse=True)
        margin = sorted_scores[0] - (sorted_scores[1] if len(sorted_scores) > 1 else 0.0)
        if margin >= 0.15:
            top = max(scores, key=lambda k: scores[k])
            return TrackDecision(
                track=top,   # type: ignore[arg-type]
                reason=f"skill-overlap margin {margin:.2f} ≥ 0.15",
                stage="skills",
                scores=scores,
            )

    # 4. LLM tiebreaker (optional)
    if llm_tiebreaker is not None:
        try:
            pick = llm_tiebreaker(title, description)
        except Exception as exc:  # treat any LLM failure as a tie
            return TrackDecision(
                track=None,
                reason=f"llm tiebreaker failed: {exc}",
                stage="tie",
                scores=scores,
            )
        if pick in ("swe", "ml", "hpc", "quant"):
            return TrackDecision(
                track=pick,   # type: ignore[arg-type]
                reason="llm tiebreaker",
                stage="llm",
                scores=scores,
            )
        # Any other return value (including "REVIEW" from injection) → tie
        return TrackDecision(
            track=None,
            reason=f"llm returned {pick!r}",
            stage="tie",
            scores=scores,
        )

    # 5. No llm configured → fall through to tie (caller handles)
    return TrackDecision(
        track=None,
        reason="no decisive signal; llm_tiebreaker not configured",
        stage="tie",
        scores=scores,
    )
