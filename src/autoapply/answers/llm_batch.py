"""Batch LLM resolver — one Gemini call per application.

This module is the **primary** answer source for dropdown picks and
free-response fields. The deterministic classifier + answer_bank is still
used for trivial profile-sourced fields (name, email, phone, GitHub URL,
resume upload) which don't need LLM reasoning. Everything else — and
specifically every **required** field the classifier can't answer
confidently — goes into a single batched Gemini call per application.

Why batching, not per-field:
  * Single round-trip amortizes prompt overhead across ~10–30 questions.
  * Holistic context = the LLM sees all form fields at once, so cross-field
    reasoning (e.g. "the state selected in Q4 matches the city answered
    in Q2") is possible.
  * Dramatically reduces call volume → stays under the Gemini Flash-Lite
    free-tier 1000 RPD cap even at 50 applications/day.

Why a model cascade:
  * Gemini 2.0 Flash was deprecated March 2026. The 429 ``RESOURCE_EXHAUSTED``
    errors we were seeing are not transient; they're quota-zero.
  * Preview models (3.1 Flash-Lite, 3 Flash) have tight per-day limits but
    share project quota with stable models. Trying them first preserves
    the 1000 RPD workhorse (2.5 Flash-Lite) for when we need it.
  * On any quota / rate-limit error, we switch to the next model in the
    cascade and retry. Only terminal failure (all models exhausted) bubbles
    up to the caller.

Module layout
-------------
    BatchQuestion           — input record describing one form field
    BatchAnswer             — output record with value + source + confidence
    BatchResult             — full result: answers + cascade trace + errors
    MODEL_CASCADE           — ordered tuple of model IDs to try
    resolve_batch()         — the public entry point
    _call_with_cascade()    — model-cascade + 429-fallback inner helper
    _build_prompt()         — prompt constructor (profile + bank + rules + Qs)
    _parse_response()       — structured-output JSON validator
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autoapply.rules import load_prompt

if TYPE_CHECKING:
    from autoapply.profile.schema import Profile

log = logging.getLogger(__name__)


# ─── Model cascade ──────────────────────────────────────────────────────────
#
# User-specified order: try preview / lightweight models first so the
# 2.5 Flash-Lite 1000-RPD workhorse is preserved for when everything else
# has been quota-exhausted. The 2.5 Flash (non-Lite) entry is the last
# resort — it has the strongest reasoning but only 250 RPD.
MODEL_CASCADE: tuple[str, ...] = (
    "gemini-3.1-flash-lite",   # preview, restrictive
    "gemini-2.5-flash-lite",   # 1000 RPD free tier
    "gemini-3-flash",          # preview, restrictive
    "gemini-2.5-flash",        # 250 RPD free tier, strongest reasoning
)


# Quota / rate-limit / availability errors that should trigger a cascade
# fallback to the next model. Anything else (auth, malformed request,
# server 5xx retry-worthy) surfaces to the caller unchanged.
_CASCADE_ERROR_MARKERS: tuple[str, ...] = (
    "RESOURCE_EXHAUSTED",
    "429",
    "quota",
    "Quota",
    "rate limit",
    "rate_limit",
    "rateLimit",
    "exceeded",
    "not found",   # fallback for preview-model 404s
    "NOT_FOUND",
    "permission",  # region / billing gating
    "PERMISSION_DENIED",
)


# ─── Public data structures ─────────────────────────────────────────────────


@dataclass
class BatchQuestion:
    """One form field to resolve via the LLM batch.

    Attributes:
      id:       stable key used to correlate the LLM answer back to the
                form field (typically the Playwright ``name``/``id`` attr).
      label:    the human-readable question text scraped from the DOM or
                API. Untrusted input — sanitized before embedding.
      kind:     ``"select"`` | ``"multi_select"`` | ``"text"`` | ``"textarea"``.
      required: whether the form marks the field as required. We only
                include required+unresolved fields in the batch — optional
                unresolved fields are left blank.
      options:  for select/multi_select, the list of option labels scraped
                from the DOM or API. Must be non-empty for select kinds.
    """
    id: str
    label: str
    kind: str
    required: bool = True
    options: list[str] = field(default_factory=list)


@dataclass
class BatchAnswer:
    """One resolved field returned by the LLM.

    ``value`` is the exact text to type / select. For ``select`` kinds it
    MUST be one of the option strings we passed in ``BatchQuestion.options``
    verbatim; we validate this post-parse and reject mismatches.

    ``confidence`` is the LLM's self-reported 0.0–1.0 score. We trust it
    only when ≥ 0.5; below that threshold the field goes to review.

    ``source`` is one of:
      ``"llm_reasoning"``     — LLM derived the answer from profile/bank
      ``"llm_generation"``    — LLM wrote free-text (essays, cover letters)
      ``"llm_option_match"``  — LLM matched a bank/profile value to a
                                specific dropdown option
      ``"needs_review"``      — LLM could not confidently answer
    """
    question_id: str
    value: str | None
    source: str
    confidence: float = 1.0
    reasoning: str = ""


@dataclass
class BatchResult:
    """Complete result of one LLM batch call.

    ``answers``: the resolved fields keyed by ``BatchQuestion.id``.
    ``model_used``: the first model in ``MODEL_CASCADE`` that returned
                    a usable response (useful for observability).
    ``cascade_trace``: per-attempt log — which model was tried and what
                       error (if any) caused the fallback. Serialized to
                       the DB audit log.
    ``error``: top-level failure message if ALL models were exhausted.
               When present, ``answers`` is empty.
    """
    answers: dict[str, BatchAnswer] = field(default_factory=dict)
    model_used: str = ""
    cascade_trace: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


# ─── Public entry point ─────────────────────────────────────────────────────


def resolve_batch(
    *,
    questions: list[BatchQuestion],
    profile: "Profile",
    answer_bank_yaml: str,
    track: str = "swe",
    company: str = "",
    job_title: str = "",
    job_description: str = "",
) -> BatchResult:
    """Batch-resolve form fields via a single LLM call with cascade fallback.

    Filters to required+nontrivial questions before calling — if no
    questions qualify, returns an empty result immediately (zero-cost).

    Args:
      questions: the full list of form fields. Non-required fields and
        fields whose ``kind`` is a trivial text atom (where the classifier
        would handle it better) should already be filtered out by the
        caller; this function only enforces the ``required`` filter.
      profile: parsed resume facts for the chosen track.
      answer_bank_yaml: the raw YAML contents of ``state/answer_bank.yml``.
        Passed as a string so the LLM can see both keys and values; avoids
        re-structuring the deterministic-first answer map into a prompt-
        friendly shape.
      track: resume track in use. Embedded in the prompt so the LLM picks
        the right "Why this role" framing.
      company, job_title: optional metadata, helps the LLM with "Why this
        company" and "Why this role" essay generation.

    Returns:
      :class:`BatchResult`. If ``error`` is set, ``answers`` is empty and
      the caller should route the application to review.
    """
    # Drop non-required items — they're not worth an LLM token if we can't
    # confidently fill them. The caller already did the
    # classifier-handles-this filter; all remaining items are either
    # required dropdowns or required free-response.
    filtered = [q for q in questions if q.required]
    if not filtered:
        return BatchResult(model_used="", cascade_trace=[
            {"skipped": "no required unresolved questions"}
        ])

    from autoapply.config import get_settings

    settings = get_settings()
    api_key = getattr(settings, "GEMINI_API_KEY", "") or ""
    if not api_key:
        return BatchResult(error="GEMINI_API_KEY not set")

    prompt = _build_prompt(
        questions=filtered,
        profile=profile,
        answer_bank_yaml=answer_bank_yaml,
        track=track,
        company=company,
        job_title=job_title,
        job_description=job_description,
    )

    return _call_with_cascade(prompt, api_key, filtered)


# ─── Cascade caller ─────────────────────────────────────────────────────────


def _call_with_cascade(
    prompt: str,
    api_key: str,
    questions: list[BatchQuestion],
) -> BatchResult:
    """Try each model in ``MODEL_CASCADE`` until one succeeds or all fail."""
    try:
        from google import genai  # type: ignore[import]
    except ImportError:
        return BatchResult(
            error="google-genai SDK not installed (pip install google-genai)"
        )

    # Use the stable ``v1`` API (not ``v1beta``). The 3.x preview models
    # still 404 on v1 as of April 2026 — they're only available via
    # ``v1alpha`` — but our cascade handles that and rolls forward to
    # 2.5 Flash-Lite. When 3.x graduates to v1 stable, no code change
    # is needed here.
    client = genai.Client(
        api_key=api_key,
        http_options={"api_version": "v1"},
    )
    trace: list[dict[str, Any]] = []

    for model_id in MODEL_CASCADE:
        attempt: dict[str, Any] = {"model": model_id}
        try:
            # v1 doesn't support response_mime_type in generate_content
            # config (only v1beta does). Instead, we rely on explicit
            # prompt instructions + a tolerant markdown-fence-stripping
            # JSON parser (see :func:`_parse_response`). Temperature is
            # low because we want deterministic picks, not creative
            # variations.
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config={"temperature": 0.2},
            )
            text = getattr(response, "text", "") or ""
            if not text.strip():
                attempt["error"] = "empty response"
                trace.append(attempt)
                continue

            answers = _parse_response(text, questions)
            if not answers:
                attempt["error"] = "response parsed to empty answer set"
                trace.append(attempt)
                continue

            attempt["ok"] = True
            attempt["answer_count"] = len(answers)
            trace.append(attempt)
            return BatchResult(
                answers=answers,
                model_used=model_id,
                cascade_trace=trace,
            )

        except Exception as exc:
            msg = str(exc)
            attempt["error"] = msg[:300]
            trace.append(attempt)
            if _is_cascade_error(msg):
                log.warning(
                    "llm_batch: %s returned cascade-triggering error; trying next: %s",
                    model_id, msg[:160],
                )
                continue
            # Non-cascade error (auth, malformed request, server error
            # that isn't a retry hint) — surface immediately.
            log.error("llm_batch: %s terminal error: %s", model_id, msg[:200])
            return BatchResult(
                error=f"{model_id}: {msg[:200]}",
                cascade_trace=trace,
            )

    return BatchResult(
        error="all models in cascade exhausted (rate-limited or unavailable)",
        cascade_trace=trace,
    )


def _is_cascade_error(msg: str) -> bool:
    return any(marker in msg for marker in _CASCADE_ERROR_MARKERS)


# ─── Prompt builder ─────────────────────────────────────────────────────────


_PROFILE_MAX_CHARS = 3000
_BANK_MAX_CHARS = 4000
_JD_MAX_CHARS = 4000  # JD context for essay tailoring


def _build_prompt(
    *,
    questions: list[BatchQuestion],
    profile: "Profile",
    answer_bank_yaml: str,
    track: str,
    company: str,
    job_title: str,
    job_description: str = "",
) -> str:
    """Assemble the full batch prompt.

    Structure (prompt parts in order):
      1. System role + core rules (never invents, honors profile, etc.)
      2. Profile JSON (compact, track-specific)
      3. Answer bank YAML (so the LLM can honor seeded defaults)
      4. Per-track decision rules for edge cases
      5. Injection-defense framing + the UNTRUSTED question block
      6. Output schema description (structured JSON)
    """
    from autoapply.security.injection_guard import sanitize

    # --- 1. Profile summary -------------------------------------------------
    profile_block = _profile_as_json(profile, track)[:_PROFILE_MAX_CHARS]

    # --- 2. Answer bank (trimmed) -------------------------------------------
    bank_block = (answer_bank_yaml or "").strip()[:_BANK_MAX_CHARS]

    # --- 3. Sanitize + format questions ------------------------------------
    questions_obj: list[dict[str, Any]] = []
    for q in questions:
        safe_label, _ = sanitize(q.label or "")
        item: dict[str, Any] = {
            "id": q.id,
            "label": safe_label[:400],
            "type": q.kind,
            "required": q.required,
        }
        if q.options:
            clean_opts: list[str] = []
            for o in q.options[:80]:
                so, _ = sanitize(str(o))
                clean_opts.append(so[:200])
            item["options"] = clean_opts
        questions_obj.append(item)
    questions_json = json.dumps(questions_obj, indent=2)

    # --- 4. Core rules ------------------------------------------------------
    rules = _RULES_BLOCK.format(track=track)

    # --- 5. Metadata --------------------------------------------------------
    meta_lines = [f"Track: {track}"]
    if company:
        safe_company, _ = sanitize(company)
        meta_lines.append(f"Company: {safe_company[:100]}")
    if job_title:
        safe_title, _ = sanitize(job_title)
        meta_lines.append(f"Role: {safe_title[:120]}")
    meta_block = "\n".join(meta_lines)

    # --- 6. Job description (for essay tailoring) ---------------------------
    # Sanitized + trimmed. Wrapped in <UNTRUSTED> below since JDs are
    # the #1 source of prompt-injection attempts. The LLM uses this to
    # tailor essays (why_company, why_role, strengths) to the specific
    # posting rather than recycling generic per-track templates.
    jd_header = ""
    jd_body = "(no job description available)"
    if job_description:
        safe_jd, _ = sanitize(job_description)
        jd_body = safe_jd[:_JD_MAX_CHARS]
        if company or job_title:
            jd_header = f' for "{(company or "?")} / {(job_title or "?")}"'

    return _PROMPT_TEMPLATE.format(
        rules=rules,
        profile_block=profile_block,
        bank_block=bank_block,
        meta_block=meta_block,
        jd_header=jd_header,
        jd_body=jd_body,
        questions_json=questions_json,
    )


# The rules block and prompt template live under ``prompts/`` — they're
# policy / prompt-engineering artifacts, versioned and diffable as plain
# markdown. Loaded once at module import; iteration on wording does not
# require a code change. See:
#   prompts/batch_rules.md   (the 12-rule CORE RULES block)
#   prompts/batch.md         (the overall prompt template)
_RULES_BLOCK = load_prompt("batch_rules")
_PROMPT_TEMPLATE = load_prompt("batch")


def _profile_as_json(profile: "Profile", track: str) -> str:
    """Serialize Profile to a compact JSON block for the LLM prompt.

    Includes the fields most likely to matter for form-filling:
    name/contact, education (GPA, school, degree, graduation date),
    one-line experience summaries, top YOE entries, and the track label.
    Expensive nested sub-fields (full project bullets, stack lists) are
    trimmed or excluded.
    """
    edu_list = []
    for e in profile.education[:2]:
        dr_raw = getattr(e.date_range, "raw", "")
        edu_list.append({
            "school": e.school,
            "degree": e.degree,
            "minor": e.minor or "",
            "gpa": e.gpa or "",
            "graduation": dr_raw,
        })

    exp_list = []
    for e in profile.experiences[:5]:
        dr_raw = getattr(e.date_range, "raw", "")
        exp_list.append({
            "title": e.title,
            "company": e.company,
            "dates": dr_raw,
            "stack": list(e.stack)[:8],
        })

    yoe_map = {}
    if profile.years_of_experience:
        top = sorted(
            ((k, v) for k, v in profile.years_of_experience.items() if v > 0),
            key=lambda kv: -kv[1],
        )[:15]
        yoe_map = {k: round(v, 1) for k, v in top}

    payload = {
        "full_name": profile.full_name,
        "email": profile.email,
        "phone": profile.phone,
        "linkedin_url": profile.linkedin_url,
        "github_url": profile.github_url,
        # Current location comes from the profile fields.
        "current_location": getattr(profile, "current_location", "") or "",
        "current_city": getattr(profile, "current_city", "") or "",
        "current_state": getattr(profile, "current_state", "") or "",
        "current_state_full": getattr(profile, "current_state_full", "") or "",
        "current_zip": getattr(profile, "current_zip", "") or "",
        "country": getattr(profile, "current_country", "United States") or "United States",
        # Citizenship + work-auth block.
        "citizenship_country": getattr(profile, "citizenship_country", "") or "",
        "us_citizen": getattr(profile, "us_citizen", "No") or "No",
        "work_authorization_status": (
            getattr(profile, "visa_status", "") or ""
        ),
        "work_authorized_us": getattr(profile, "work_authorized_us", "Yes") or "Yes",
        "permanent_work_authorization": (
            getattr(profile, "permanent_work_authorization", "No") or "No"
        ),
        "sponsorship_needed_now": (
            getattr(profile, "require_sponsorship_now", "No") or "No"
        ),
        "sponsorship_needed_future": (
            getattr(profile, "require_sponsorship_future", "Yes") or "Yes"
        ),
        # Military / EEO.
        "military_service": getattr(profile, "military_service", "No") or "No",
        "demo_gender": getattr(profile, "demo_gender", "") or "",
        "demo_race": getattr(profile, "demo_race", "") or "",
        "demo_hispanic_latino": getattr(profile, "demo_hispanic_latino", "") or "",
        "demo_veteran": getattr(profile, "demo_veteran", "") or "",
        "demo_disability": getattr(profile, "demo_disability", "") or "",
        "demo_pronouns": getattr(profile, "demo_pronouns", "") or "",
        # Willing-to-work states list (trimmed to a count + list for prompt brevity).
        "willing_to_work_states": getattr(
            profile, "willing_to_work_states", []
        ) or [],
        "resume_track": track,
        "education": edu_list,
        "experience": exp_list,
        "years_of_experience": yoe_map,
    }
    return json.dumps(payload, indent=2)


# ─── Response parser ────────────────────────────────────────────────────────


def _parse_response(
    raw_text: str, questions: list[BatchQuestion]
) -> dict[str, BatchAnswer]:
    """Validate + normalize the LLM's JSON output.

    Safety rules applied in order:
      1. Strip markdown fences (```json ... ```) if the model wrapped the
         JSON despite our structured-output directive.
      2. Parse as JSON; reject on failure.
      3. For each declared question id, look up its answer; missing →
         ``needs_review``.
      4. For type=select: the returned ``value`` must be an exact match
         for one of the question's options. Case-insensitive fallback is
         attempted; if still no match, marked needs_review.
      5. For type=multi_select: value must be a JSON array of option
         strings.
      6. Confidence below 0.5 → forced to needs_review (the caller routes
         the field to the human review queue rather than shipping a
         low-confidence answer).
    """
    raw = raw_text.strip()
    if raw.startswith("```"):
        # Strip markdown fences — tolerate ```json ... ``` wrappers.
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```\s*$", "", raw)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("llm_batch: response JSON parse failed: %s", exc)
        return {}

    items = payload.get("answers") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        log.warning("llm_batch: response missing 'answers' list; got %r", type(payload))
        return {}

    # Index questions for O(1) lookup.
    q_by_id: dict[str, BatchQuestion] = {q.id: q for q in questions}

    out: dict[str, BatchAnswer] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("id") or "")
        if not qid or qid not in q_by_id:
            continue
        q = q_by_id[qid]
        value = item.get("value")
        source = str(item.get("source") or "needs_review")
        try:
            confidence = float(item.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        reasoning = str(item.get("reasoning") or "")[:200]

        # Normalize value for each kind.
        if q.kind in ("select",):
            value = _validate_select_value(value, q.options)
            if value is None:
                source = "needs_review"
        elif q.kind in ("multi_select",):
            value = _validate_multi_select_value(value, q.options)
            if value is None or not value:
                source = "needs_review"
        elif value is not None:
            value = str(value)

        # Confidence threshold.
        if confidence < 0.5 and source != "needs_review":
            source = "needs_review"

        out[qid] = BatchAnswer(
            question_id=qid,
            value=value if source != "needs_review" else None,
            source=source,
            confidence=confidence,
            reasoning=reasoning,
        )

    # Fill in any questions the model omitted.
    for q in questions:
        if q.id not in out:
            out[q.id] = BatchAnswer(
                question_id=q.id,
                value=None,
                source="needs_review",
                confidence=0.0,
                reasoning="LLM omitted this question",
            )
    return out


def _validate_select_value(value: Any, options: list[str]) -> str | None:
    """Ensure a select value exactly matches one of the options.

    Tolerates case-only differences by case-insensitive match; returns
    the canonical (original-case) option. Returns ``None`` if no match.
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    for opt in options:
        if opt == v:
            return opt
    for opt in options:
        if opt.lower() == v.lower():
            return opt
    return None


def _validate_multi_select_value(
    value: Any, options: list[str]
) -> list[str] | None:
    """Ensure a multi_select value is a list of valid option strings."""
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        v = _validate_select_value(item, options)
        if v is not None:
            out.append(v)
    return out if out else None
