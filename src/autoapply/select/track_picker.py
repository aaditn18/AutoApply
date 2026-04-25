"""Resume-track picker.

Decides which of `swe|ml|hpc|quant` to submit for a given job. Uses title +
description, NEVER a hardcoded firm list (design decision — see plan §
"No predetermined quant firm list").

Decision ladder:
  1. Strong title keywords (unambiguous signal).
  2. Quant-in-description promotion (generic title + trading-heavy desc).
  3. Skill-overlap with each resume_track's skills.
  3.5 Semantic title-vs-archetype similarity (interim; see log.md
      "Deferred TODO — refine track selection" for the proper plan).
  4. LLM tiebreaker (optional, injection-guarded).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

from autoapply.profile.schema import Profile
from autoapply.rules import load_rules

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


# -- Semantic title-vs-archetype fallback (step 3.5) -----------------------


# Token-split on whitespace + non-alphanumerics except "+" and "#"
# (keep "C++" / "C#" as single tokens-ish; "c++" collapses to "c").
_TOKEN_SPLIT = re.compile(r"[^a-z0-9+#]+")


def _tokenize_title(s: str, stopwords: frozenset[str]) -> frozenset[str]:
    """Lowercase, split on non-word, drop stopwords + single-char noise."""
    if not s:
        return frozenset()
    lowered = s.lower()
    tokens = {t for t in _TOKEN_SPLIT.split(lowered) if t}
    # Drop 1-char tokens (initials, noise) and configured stopwords.
    return frozenset(
        t for t in tokens
        if len(t) > 1 and t not in stopwords
    )


def _cosine(a: frozenset[str], b: frozenset[str]) -> float:
    """Binary bag-of-words cosine. |A ∩ B| / sqrt(|A| * |B|)."""
    if not a or not b:
        return 0.0
    return len(a & b) / math.sqrt(len(a) * len(b))


def _semantic_title_track(title: str) -> tuple[Track | None, dict[str, float]]:
    """Match title tokens against per-track archetype phrases.

    Returns (track, per_track_scores). Returns None for the track when
    neither (a) the top score exceeds ``min_score`` nor (b) the winning
    track's lead over the runner-up exceeds ``min_margin`` — in which
    case the caller falls through to the LLM tiebreaker / tie path.

    This is a bag-of-words cosine on tokenized noun phrases. The
    similarity measure is deliberately crude — good enough for the
    interim case where the strong-keyword lists haven't caught
    unusual titles like "Autonomy Engineer - Deep Learning" or
    "Wireless Systems Performance Engineer". See the deferred TODO
    in log.md for the proper embedding-based replacement.
    """
    rules = load_rules("track_archetypes")
    stopwords = frozenset(str(s).lower() for s in rules.get("stopwords", []))
    min_score = float(rules.get("min_score", 0.4))
    min_margin = float(rules.get("min_margin", 0.08))

    title_tokens = _tokenize_title(title, stopwords)
    if not title_tokens:
        return None, {}

    scores: dict[str, float] = {}
    for track in ("swe", "ml", "hpc", "quant"):
        archetypes = rules.get(track) or []
        best = 0.0
        for phrase in archetypes:
            phrase_tokens = _tokenize_title(str(phrase), stopwords)
            sim = _cosine(title_tokens, phrase_tokens)
            if sim > best:
                best = sim
        scores[track] = best

    if not scores:
        return None, scores
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_track, top_score = ranked[0]
    runner_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_score < min_score:
        return None, scores
    if (top_score - runner_score) < min_margin:
        return None, scores
    return top_track, scores  # type: ignore[return-value]


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

    # 3.5 Semantic title-vs-archetype fallback (interim — see log.md
    # "Deferred TODO — refine track selection"). Fires ONLY when the
    # strong-title-keyword and skill-overlap stages abstained, so it
    # never overrides their decisions.
    semantic_track, semantic_scores = _semantic_title_track(title)
    if semantic_track is not None:
        return TrackDecision(
            track=semantic_track,
            reason=(
                f"semantic title-archetype cosine "
                f"(top={semantic_scores[semantic_track]:.2f}, "
                f"scores={ {k: round(v, 2) for k, v in semantic_scores.items()} })"
            ),
            stage="semantic_title",
            scores=semantic_scores,
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
