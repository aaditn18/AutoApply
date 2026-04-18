"""LLM / template fallback for novel, unclassified form fields.

Called by resolve_field() when the rule-based classifier cannot identify a
question_type for a text / textarea / select field.

Resolution order:
  1. Gemini 2.0 Flash  — when GEMINI_API_KEY is set (free tier: 1500 RPD).
  2. Template heuristics — deterministic patterns for the most common
     unclassified question shapes (pronouns, total YOE, elevator-pitch,
     strengths/weaknesses, start-date, salary, etc.).
  3. None — caller falls through to UnresolvedField / empty-string.

Answers are marked source="llm_answer" in ResolvedField so audit logs
can distinguish them from bank / profile answers.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoapply.execute.standard_fields import FieldSpec
    from autoapply.profile.schema import Profile

log = logging.getLogger(__name__)

# Compact profile fed to the model — keeps token cost low.
_MAX_PROFILE_CHARS = 2000


def _build_profile_summary(profile: "Profile", track: str) -> str:
    """Serialize the key Profile facts into a short plain-text block."""
    lines: list[str] = [
        f"Name: {profile.full_name}",
        f"Email: {profile.email}",
        f"Phone: {profile.phone}",
        f"Location: College Park, MD (University of Maryland)",
        f"LinkedIn: {profile.linkedin_url}",
        f"GitHub: {profile.github_url}",
        f"Resume track: {track}",
    ]

    if profile.education:
        edu = profile.education[0]
        grad = getattr(edu.date_range, "raw", "May 2026")
        lines.append(
            f"Education: {edu.degree} @ {edu.school}, GPA {edu.gpa}, {grad}"
        )
        if edu.minor:
            lines[-1] += f", minor in {edu.minor}"

    if profile.experiences:
        lines.append("Experience:")
        for exp in profile.experiences[:4]:
            raw_dr = getattr(exp.date_range, "raw", "")
            lines.append(f"  - {exp.title} @ {exp.company} ({raw_dr})")
            for b in exp.bullets[:2]:
                lines.append(f"    * {b[:120]}")

    if profile.projects:
        lines.append("Projects:")
        for proj in profile.projects[:3]:
            lines.append(f"  - {proj.name}: {' '.join(proj.bullets[:1])[:100]}")

    # Skills — flatten all categories.
    all_skills: list[str] = []
    for cat_skills in profile.skills.by_category.values():
        all_skills.extend(cat_skills)
    if not all_skills:
        all_skills = (
            profile.skills.languages
            + profile.skills.libraries
            + profile.skills.tools
        )
    if all_skills:
        lines.append(f"Key skills: {', '.join(all_skills[:20])}")

    # Top YOE entries (non-trivial ones).
    if profile.years_of_experience:
        top_yoe = sorted(
            ((k, v) for k, v in profile.years_of_experience.items() if v >= 0.5),
            key=lambda kv: -kv[1],
        )[:8]
        if top_yoe:
            yoe_str = ", ".join(f"{k} {v:.1f}y" for k, v in top_yoe)
            lines.append(f"Years of experience: {yoe_str}")

    return "\n".join(lines)[:_MAX_PROFILE_CHARS]


def draft_field_answer(
    *,
    label: str,
    spec: "FieldSpec",
    profile: "Profile",
    track: str,
) -> str | None:
    """Generate an answer for an unclassified form field.

    Returns the answer string, or None if no confident answer can be produced.
    The label is sanitized before embedding in any LLM prompt.
    """
    from autoapply.config import get_settings
    from autoapply.security.injection_guard import sanitize

    settings = get_settings()

    # Sanitize the label — ATS form labels are untrusted input.
    # sanitize() returns (cleaned_str, InjectionReport); unpack accordingly.
    safe_label, _scan_report = sanitize(label)

    # Build the options hint for select / multi-select fields.
    options_hint = ""
    if spec.options:
        opts_str = ", ".join(f'"{o}"' for o in spec.options)
        options_hint = (
            f'\nAvailable options: [{opts_str}]'
            f'\nYou MUST output exactly one of these options verbatim.'
        )

    profile_summary = _build_profile_summary(profile, track)

    prompt = (
        "You are filling in a job application form on behalf of Aadit Nilay.\n"
        "Answer the form field described below based ONLY on the profile provided.\n"
        "Do NOT invent facts, dates, companies, or skills not in the profile.\n\n"
        "PROFILE:\n"
        f"{profile_summary}\n\n"
        "FORM FIELD:\n"
        f'  Label: "{safe_label}"\n'
        f"  Type: {spec.kind}\n"
        f"  Required: {spec.required}"
        f"{options_hint}\n\n"
        "INSTRUCTIONS:\n"
        "- text / textarea: 1–3 concise, factual sentences drawn from the profile.\n"
        "- select / multi_select: output exactly one option from the list above.\n"
        "- If the profile provides no relevant information, output exactly: UNKNOWN\n\n"
        "Answer:"
    )

    # ── 1. Gemini Flash ───────────────────────────────────────────────────
    if settings.GEMINI_API_KEY:
        try:
            answer = _call_gemini(prompt, settings.GEMINI_API_KEY)
            if answer:
                answer = answer.strip()
                if answer.upper() != "UNKNOWN" and answer:
                    log.info(
                        "llm_fallback: Gemini answered label=%r (track=%s)",
                        label[:60],
                        track,
                    )
                    return answer
        except Exception as exc:
            log.debug("llm_fallback: Gemini failed for label=%r: %s", label[:60], exc)

    # ── 2. Template heuristics ────────────────────────────────────────────
    answer = _template_fallback(safe_label, spec, profile, track)
    if answer:
        log.info(
            "llm_fallback: template answered label=%r (track=%s)", label[:60], track
        )
        return answer

    return None


# ── Gemini helper ────────────────────────────────────────────────────────────


def _call_gemini(prompt: str, api_key: str) -> str | None:
    """Call Gemini 2.0 Flash and return the raw response text."""
    try:
        from google import genai  # type: ignore[import]
    except ImportError:
        log.debug("google-genai not installed; skipping Gemini llm_fallback")
        return None

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    return getattr(response, "text", None)


# ── Template heuristics ──────────────────────────────────────────────────────


def _template_fallback(
    label: str,
    spec: "FieldSpec",
    profile: "Profile",
    track: str,
) -> str | None:
    """Deterministic answers for common unclassified question shapes."""
    lo = label.lower()

    # ── Pronouns ──────────────────────────────────────────────────────────
    if "pronoun" in lo:
        if spec.options:
            for opt in spec.options:
                if "he" in opt.lower():
                    return opt
        return "He/Him"

    # ── Preferred name (separate from pronouns) ───────────────────────────
    if "preferred name" in lo:
        return profile.full_name.split()[0]  # first name

    # ── Onsite / in-person willingness ────────────────────────────────────
    if any(
        w in lo
        for w in (
            "willing to work onsite", "work from our office", "work in-person",
            "designated as on-site", "in-office", "work on site",
            "work in our office", "work at our", "onsite role",
            "work from the office", "office-based",
        )
    ):
        if spec.options:
            for opt in spec.options:
                if "yes" in opt.lower():
                    return opt
        return "Yes"

    # ── Address sub-fields ────────────────────────────────────────────────
    # Full address
    if any(
        w in lo
        for w in ("full address", "complete address", "current address", "mailing address")
    ) and "line" not in lo and "street" not in lo:
        return "8150 Baltimore Ave, Apt. 308-C, College Park, MD 20740"

    # Street address / address line 1
    if any(
        w in lo
        for w in ("street address", "address line 1", "address 1", "address line1",
                  "address (line 1)", "line 1")
    ):
        return "8150 Baltimore Ave"

    # Address line 2 / apt / unit / suite
    if any(
        w in lo
        for w in ("address line 2", "address 2", "address line2", "address (line 2)",
                  "line 2", "apt.", "apt #", "apt number", "unit #",
                  "unit number", "suite")
    ) or (lo.strip() in ("apt", "unit", "suite")):
        return "Apt. 308-C"

    # City
    if lo.strip() in ("city", "city/town") or "city of residence" in lo or "city where" in lo:
        return "College Park"

    # State
    if lo.strip() in ("state", "state/province", "state/region") or "state of residence" in lo:
        if spec.options:
            for opt in spec.options:
                if opt.lower() in ("md", "maryland"):
                    return opt
        return "MD"

    # Zip / postal code
    if any(w in lo for w in ("zip code", "postal code", "zip")):
        return "20740"

    # ── Total years of experience (generic) ───────────────────────────────
    if "year" in lo and "experience" in lo:
        if profile.years_of_experience:
            total = sum(profile.years_of_experience.values())
            return str(min(int(total), 3))  # cap at 3 for new-grad framing
        return "2"

    # ── Self-introduction / summary ───────────────────────────────────────
    if any(
        w in lo
        for w in ("about yourself", "tell us about you", "introduce yourself", "summary")
    ):
        gpa = profile.education[0].gpa if profile.education else "3.975"
        school = profile.education[0].school if profile.education else "University of Maryland"
        return (
            f"I'm a CS + Mathematics student at {school} (GPA {gpa}, May 2026) "
            f"with backend internships at PayPal and Sociable AI and ML systems "
            f"research at UMD's PSSG lab."
        )

    # ── Strengths ─────────────────────────────────────────────────────────
    if any(w in lo for w in ("strength", "best quality", "excel", "what makes you")):
        if track == "quant":
            return (
                "Strong mathematical foundation paired with systems engineering "
                "depth — I've built C++ Monte Carlo simulations and CUDA signal "
                "kernels from scratch."
            )
        if track in ("hpc", "ml"):
            return (
                "Bridging research and production: I move fluidly from CUDA kernel "
                "profiling to LLM-pipeline deployment, keeping both correctness and "
                "latency in mind."
            )
        return (
            "End-to-end ownership — I take problems from design through deployment, "
            "as demonstrated by my PayPal work optimizing a $1.5B/day transaction pipeline."
        )

    # ── Weaknesses ────────────────────────────────────────────────────────
    if any(w in lo for w in ("weakness", "area for improvement", "challenge for you")):
        return (
            "I occasionally over-engineer early solutions; I've learned to prototype "
            "quickly first and invest in abstraction only once the design is validated."
        )

    # ── Why this company (requires_llm in the bank, should not reach here) ─
    if any(w in lo for w in ("why this company", "why us", "why our company")):
        return f"I'm excited about the mission and technical challenges at {track}."

    # ── Start date ────────────────────────────────────────────────────────
    if any(w in lo for w in ("start date", "when can you start", "available to start")):
        return "May 2026"

    # ── Salary / compensation ─────────────────────────────────────────────
    if any(w in lo for w in ("salary", "compensation", "pay expectation", "rate")):
        return "Market rate for new-grad SWE/ML; open to discussing the full package."

    # ── "N/A if not applicable" / "If other" detail fields ────────────────
    # MUST come before ITAR handler because some follow-up ITAR textarea fields
    # also contain "itar" in their label.
    # Many compliance forms have a follow-up free-text box for "other" cases
    # with the instruction to put N/A if not applicable. We detect these and
    # return N/A so required-field validation passes without LLM involvement.
    if any(w in lo for w in ("n/a if not applicable", "answer n/a", "if not applicable",
                              "if other, please", "otherwise enter n/a",
                              "enter n/a if", "type n/a")):
        return "N/A"

    # ── Citizenship / eligibility ─────────────────────────────────────────
    if any(w in lo for w in ("us citizen", "citizen of the", "eligible to work",
                              "legally authorized", "authorized to work")):
        if spec.options:
            for opt in spec.options:
                if "yes" in opt.lower():
                    return opt
        return "Yes"

    # ── ITAR / US Person eligibility (defense/aerospace forms) ───────────
    # These questions ask whether the applicant is a US Person for export
    # control purposes.  We always assert citizenship / US person status.
    if any(w in lo for w in ("itar", "us person", "export control", "u.s. person",
                              "lawfully admitted", "permanent resident",
                              "lawful permanent", "export regulation")):
        if spec.options:
            # Prefer "U.S. citizen" or "citizen" option.
            for opt in spec.options:
                if any(x in opt.lower() for x in ("citizen", "national")):
                    return opt
            # Fallback: first non-blank option (usually the most common/neutral).
            for opt in spec.options:
                if opt.strip():
                    return opt
        return "Yes"

    # ── Visa / immigration sponsorship ────────────────────────────────────
    if any(w in lo for w in ("sponsorship", "visa sponsor", "require sponsor",
                              "need.*sponsor", "immigration.*support",
                              "immigration.*authorization", "need visa",
                              "work.*authorization.*support")):
        if spec.options:
            for opt in spec.options:
                if opt.lower() == "no":
                    return opt
        return "No"

    # ── Referral / how did you hear ───────────────────────────────────────
    if any(w in lo for w in ("how did you hear", "referral", "how did you find")):
        return "Online job board"

    # ── Diversity / demographic free-text ─────────────────────────────────
    if any(
        w in lo
        for w in ("race", "ethnicity", "gender identity", "disability", "veteran")
    ):
        if spec.options:
            for opt in spec.options:
                if any(
                    d in opt.lower()
                    for d in ("decline", "prefer not", "not wish", "not disclose")
                ):
                    return opt
        return "Decline to self-identify"

    # ── Select fields: pick the best option if all else fails ─────────────
    if spec.options:
        # Prefer "Yes" for affirmative-sounding labels.
        if any(
            w in lo
            for w in ("eligible", "authorized", "willing", "open to", "able to")
        ):
            for opt in spec.options:
                if opt.lower() == "yes":
                    return opt
        # Prefer "No" for negative-sounding labels.
        if any(
            w in lo for w in ("require sponsor", "need visa", "currently employed")
        ):
            for opt in spec.options:
                if opt.lower() == "no":
                    return opt
        # Last resort for select: return the first non-blank option.
        for opt in spec.options:
            if opt.strip():
                return opt

    return None
