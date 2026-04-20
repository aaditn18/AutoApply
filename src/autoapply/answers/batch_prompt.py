"""Prompt construction for the batch resolver.

Split from ``llm_batch.py`` — this module owns the prompt's shape and
budget, not the Gemini call mechanics. Prompt text itself lives in
``prompts/batch.md`` and ``prompts/batch_rules.md`` (loaded via
:func:`autoapply.rules.load_prompt`); this module assembles the
placeholder values:

  * ``profile_block``   — compact JSON of the candidate profile,
                          trimmed to ``_PROFILE_MAX_CHARS``.
  * ``bank_block``      — raw ``answer_bank.yml`` text, trimmed to
                          ``_BANK_MAX_CHARS``.
  * ``rules``           — the 12-rule CORE RULES block, formatted
                          with the current ``{track}``.
  * ``meta_block``      — track / company / role header.
  * ``jd_header``       — "for <company> / <role>" string (or empty).
  * ``jd_body``         — sanitized JD text, trimmed to ``_JD_MAX_CHARS``.
  * ``questions_json``  — sanitized question list as JSON.

Every untrusted input (job description, company, role, question
labels, option strings) goes through
:func:`autoapply.security.injection_guard.sanitize` before it lands in
the prompt. See :mod:`autoapply.security.injection_guard`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from autoapply.rules import load_prompt


if TYPE_CHECKING:
    from autoapply.answers.llm_batch import BatchQuestion
    from autoapply.profile.schema import Profile


# Prompt-body budgets (characters, not tokens). Generous enough for a
# real candidate profile + typical ATS form + 4000-char JD.
_PROFILE_MAX_CHARS = 3000
_BANK_MAX_CHARS = 4000
_JD_MAX_CHARS = 4000  # JD context for essay tailoring


# Loaded once at import — iteration on wording is a markdown edit, not
# a code change. See prompts/ for the files.
_RULES_BLOCK = load_prompt("batch_rules")
_PROMPT_TEMPLATE = load_prompt("batch")


def build_prompt(
    *,
    questions: list["BatchQuestion"],
    profile: "Profile",
    answer_bank_yaml: str,
    track: str,
    company: str,
    job_title: str,
    job_description: str = "",
) -> str:
    """Assemble the full batch prompt.

    Structure (in order):
      1. System role + core rules (never invents, honors profile, etc.)
      2. Profile JSON (compact, track-specific)
      3. Answer bank YAML (so the LLM can honor seeded defaults)
      4. Per-track decision rules for edge cases (from ``prompts/batch_rules.md``)
      5. Injection-defense framing + the UNTRUSTED question block
      6. Output schema description (structured JSON)
    """
    from autoapply.security.injection_guard import sanitize

    # 1. Profile summary.
    profile_block = profile_as_json(profile, track)[:_PROFILE_MAX_CHARS]

    # 2. Answer bank (trimmed).
    bank_block = (answer_bank_yaml or "").strip()[:_BANK_MAX_CHARS]

    # 3. Sanitize + format questions.
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

    # 4. Core rules, formatted with the current track.
    rules = _RULES_BLOCK.format(track=track)

    # 5. Metadata.
    meta_lines = [f"Track: {track}"]
    if company:
        safe_company, _ = sanitize(company)
        meta_lines.append(f"Company: {safe_company[:100]}")
    if job_title:
        safe_title, _ = sanitize(job_title)
        meta_lines.append(f"Role: {safe_title[:120]}")
    meta_block = "\n".join(meta_lines)

    # 6. Job description (for essay tailoring). Sanitized + trimmed.
    # Wrapped in <UNTRUSTED> in the template since JDs are the #1
    # source of prompt-injection attempts.
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


def profile_as_json(profile: "Profile", track: str) -> str:
    """Serialize Profile to a compact JSON block for the LLM prompt.

    Includes the fields most likely to matter for form-filling:
    name / contact, education (GPA, school, degree, graduation date),
    one-line experience summaries, top YOE entries, EEO / citizenship /
    work-auth, willing-to-work states. Expensive nested sub-fields
    (full project bullets, stack lists) are trimmed or excluded.
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
        # Current location.
        "current_location": getattr(profile, "current_location", "") or "",
        "current_city": getattr(profile, "current_city", "") or "",
        "current_state": getattr(profile, "current_state", "") or "",
        "current_state_full": getattr(profile, "current_state_full", "") or "",
        "current_zip": getattr(profile, "current_zip", "") or "",
        "country": getattr(profile, "current_country", "United States") or "United States",
        # Citizenship + work-auth.
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
        # Willing-to-work states list.
        "willing_to_work_states": getattr(
            profile, "willing_to_work_states", []
        ) or [],
        "resume_track": track,
        "education": edu_list,
        "experience": exp_list,
        "years_of_experience": yoe_map,
    }
    return json.dumps(payload, indent=2)
