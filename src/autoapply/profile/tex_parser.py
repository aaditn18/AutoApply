"""Parse Jake Gutierrez-template resume `.tex` files into a `Profile`.

The template uses a small set of custom macros:
  \\eduSubheading{school}{location}{degree}{dates}{minor}{gpa}
  \\resumeSubheading{title}{dates}{company}{stack}{location}
  \\resumeProjectHeading{\\textbf{name} $|$ \\emph{stack}}{dates}
  \\resumeItem{bullet text}

The parser is deterministic and tolerant of whitespace / line wraps. It does
NOT run LaTeX — it only reads args via brace-balanced extraction.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from pathlib import Path

from autoapply.profile.schema import (
    DateRange,
    Education,
    Experience,
    Profile,
    Project,
    Skills,
    Track,
)


# -- Low-level helpers ------------------------------------------------------

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
# Add full names too (January, February, ...)
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_name) if m})


def _balanced_arg(text: str, start: int) -> tuple[str, int]:
    """Read one `{...}` brace-balanced argument starting at text[start] == '{'.

    Returns (inner_content, position_after_closing_brace).
    Raises ValueError if unbalanced.
    """
    if start >= len(text) or text[start] != "{":
        raise ValueError(f"expected '{{' at position {start}, got {text[start:start+10]!r}")
    depth = 0
    i = start
    while i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    raise ValueError(f"unbalanced braces starting at {start}")


def _read_n_args(text: str, start: int, n: int) -> tuple[list[str], int]:
    """Read n consecutive brace-balanced args, skipping whitespace between them."""
    args: list[str] = []
    i = start
    for _ in range(n):
        # skip whitespace
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        arg, i = _balanced_arg(text, i)
        args.append(arg)
    return args, i


def _strip_latex(s: str) -> str:
    """Strip common LaTeX formatting for display: \\textbf, \\emph, \\underline, $...$, \\&, etc.

    Preserves the visible text. Not a full LaTeX renderer — just enough to
    produce clean strings for the DB.
    """
    # \textbf{X} / \emph{X} / \underline{X} / \textit{X} / \text{X} -> X
    for macro in ("textbf", "emph", "underline", "textit", "textsc", "text"):
        pat = re.compile(r"\\" + macro + r"\{")
        while True:
            m = pat.search(s)
            if not m:
                break
            inner, end = _balanced_arg(s, m.end() - 1)
            s = s[: m.start()] + inner + s[end:]

    # \href{url}{text} -> text
    hpat = re.compile(r"\\href\{")
    while True:
        m = hpat.search(s)
        if not m:
            break
        _url, after_url = _balanced_arg(s, m.end() - 1)
        # skip whitespace then read the second arg
        while after_url < len(s) and s[after_url] in " \t\r\n":
            after_url += 1
        if after_url < len(s) and s[after_url] == "{":
            text, end = _balanced_arg(s, after_url)
        else:
            text, end = "", after_url
        s = s[: m.start()] + text + s[end:]

    # common escapes
    s = s.replace("\\$", "$").replace("\\%", "%").replace("\\&", "&").replace("\\#", "#")
    s = s.replace("--", "–")
    # dollar-math wrappers: $...$ -> strip
    s = re.sub(r"\$([^$]*)\$", r"\1", s)
    # \vspace{...} / \\, \;
    s = re.sub(r"\\vspace\{[^}]*\}", "", s)
    s = re.sub(r"\\hspace\{[^}]*\}", "", s)
    s = s.replace("\\\\", " ").replace("\\,", " ").replace("\\;", " ")
    # \sim, \times, \sim  -> readable
    s = re.sub(r"\\sim\b", "~", s)
    s = re.sub(r"\\times\b", "×", s)
    # Any remaining simple \macro (no braces) -> drop the macro name
    s = re.sub(r"\\[a-zA-Z]+\*?", "", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


# -- Date parsing -----------------------------------------------------------

_DATE_RE = re.compile(
    r"(?P<mon>[A-Za-z]+\.?)?\s*(?P<year>\d{4})", re.IGNORECASE
)


def _parse_single_date(s: str) -> date | None:
    """Parse things like 'May 2025', 'Aug. 2023', '2024'."""
    s = s.strip()
    m = _DATE_RE.search(s)
    if not m:
        return None
    mon_raw = (m.group("mon") or "").rstrip(".").lower()
    year = int(m.group("year"))
    month = _MONTHS.get(mon_raw, 1)
    if month == 0:
        month = 1
    return date(year, month, 1)


def parse_date_range(raw: str) -> DateRange:
    """Parse 'May 2025 -- Aug 2025', 'Feb 2026 -- Present', 'CVPRW 2026', etc."""
    cleaned = raw.replace("--", "-").replace("–", "-").replace("—", "-")
    is_present = bool(re.search(r"\bpresent\b", cleaned, re.IGNORECASE))

    parts = [p.strip() for p in re.split(r"\s*-\s*", cleaned) if p.strip()]
    start = _parse_single_date(parts[0]) if parts else None
    end: date | None = None
    if len(parts) >= 2 and not re.search(r"\bpresent\b", parts[1], re.IGNORECASE):
        end = _parse_single_date(parts[1])
        # snap end to last day of month for duration math
        if end is not None:
            last = calendar.monthrange(end.year, end.month)[1]
            end = date(end.year, end.month, last)
    elif len(parts) == 1:
        end = start  # single-point "CVPRW 2026" type

    return DateRange(raw=raw.strip(), start=start, end=end, is_present=is_present)


# -- Section extraction -----------------------------------------------------

_SECTION_RE = re.compile(r"\\section\{([^}]+)\}", re.IGNORECASE)


def _strip_comments(tex: str) -> str:
    """Drop LaTeX line comments (% to end of line, respecting \\%)."""
    out_lines = []
    for line in tex.splitlines():
        # find first unescaped %
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                line = line[:i]
                break
            i += 1
        out_lines.append(line)
    return "\n".join(out_lines)


def _section_bodies(tex: str) -> dict[str, str]:
    """Return {section_title_lower: body_text_up_to_next_section_or_end_of_doc}."""
    tex = _strip_comments(tex)
    bodies: dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(tex))
    end_doc = tex.find("\\end{document}")
    if end_doc == -1:
        end_doc = len(tex)
    for i, m in enumerate(matches):
        name = m.group(1).strip().lower()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else end_doc
        bodies[name] = tex[body_start:body_end]
    return bodies


# -- Macro iterators --------------------------------------------------------


def _iter_macro(body: str, macro: str, arity: int):
    """Yield (args_list, start_pos_of_macro, end_pos_after_last_arg) for each occurrence."""
    pat = re.compile(r"\\" + macro + r"(?![A-Za-z])")
    for m in pat.finditer(body):
        try:
            args, end = _read_n_args(body, m.end(), arity)
        except ValueError:
            continue
        yield args, m.start(), end


def _extract_bullets_between(body: str, after: int, before: int) -> list[str]:
    """Collect all \\resumeItem{...} between two offsets."""
    bullets: list[str] = []
    snippet = body[after:before]
    pat = re.compile(r"\\resumeItem(?![A-Za-z])")
    for m in pat.finditer(snippet):
        try:
            args, _ = _read_n_args(snippet, m.end(), 1)
        except ValueError:
            continue
        bullets.append(_strip_latex(args[0]))
    return bullets


# -- Header (name / email / links) -----------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\d{3}[-.\s]?\d{3}[-.\s]?\d{4}")
_LINKEDIN_RE = re.compile(r"https?://(?:www\.)?linkedin\.com/in/[^\s}]+", re.IGNORECASE)
_GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/[^\s}]+", re.IGNORECASE)
_NAME_RE = re.compile(r"\\Huge\s+([A-Z][A-Za-z\-']+(?:\s+[A-Z][A-Za-z\-']+)+)")


def _parse_header(tex: str) -> dict[str, str]:
    h: dict[str, str] = {}
    # take the \begin{center}...\end{center} block if present
    m = re.search(r"\\begin\{center\}(.*?)\\end\{center\}", tex, re.DOTALL)
    block = m.group(1) if m else tex[:3000]

    name_m = _NAME_RE.search(block)
    h["full_name"] = name_m.group(1).strip() if name_m else ""

    email_m = _EMAIL_RE.search(block)
    h["email"] = email_m.group(0) if email_m else ""
    phone_m = _PHONE_RE.search(block)
    h["phone"] = phone_m.group(0) if phone_m else ""
    li_m = _LINKEDIN_RE.search(block)
    h["linkedin_url"] = li_m.group(0).rstrip("}") if li_m else ""
    gh_m = _GITHUB_RE.search(block)
    h["github_url"] = gh_m.group(0).rstrip("}") if gh_m else ""
    return h


# -- Section parsers --------------------------------------------------------


def _parse_education(body: str) -> list[Education]:
    eds: list[Education] = []
    for args, start, end in _iter_macro(body, "eduSubheading", 6):
        school, location, degree, dates, minor, gpa = (_strip_latex(a) for a in args)
        # Coursework: pick up any `Courses:` line after this subheading
        coursework: list[str] = []
        tail = body[end : end + 800]
        m = re.search(r"Courses?\s*:\s*([^\\]+)", tail)
        if m:
            coursework = [c.strip() for c in re.split(r",\s*", m.group(1)) if c.strip()]
        eds.append(
            Education(
                school=school,
                location=location,
                degree=degree,
                date_range=parse_date_range(dates),
                minor=minor,
                gpa=_extract_gpa(gpa),
                coursework=coursework,
            )
        )
    return eds


def _extract_gpa(raw: str) -> str:
    m = re.search(r"(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?)", raw)
    return m.group(1).replace(" ", "") if m else raw


def _parse_stack_string(s: str) -> list[str]:
    """Split a comma-delimited skill stack into clean tokens."""
    s = _strip_latex(s)
    parts = [p.strip() for p in re.split(r"[,;]", s) if p.strip()]
    return parts


def _parse_experiences(body: str) -> list[Experience]:
    exps: list[Experience] = []
    macro_positions = list(_iter_macro(body, "resumeSubheading", 5))
    for idx, (args, start, end) in enumerate(macro_positions):
        title, dates, company, stack, location = (_strip_latex(a) for a in args)
        # Bullets live between this heading and the next (or end of section)
        next_start = (
            macro_positions[idx + 1][1]
            if idx + 1 < len(macro_positions)
            else len(body)
        )
        bullets = _extract_bullets_between(body, end, next_start)
        exps.append(
            Experience(
                title=title,
                company=company,
                location=location,
                stack=_parse_stack_string(stack),
                date_range=parse_date_range(dates),
                bullets=bullets,
            )
        )
    return exps


_PROJECT_NAME_STACK_RE = re.compile(
    r"\\textbf\{(?P<name>[^{}]+)\}[^\\]*(?:\$\|\$|\|)[^\\]*\\emph\{(?P<stack>[^{}]+)\}",
    re.IGNORECASE,
)


def _parse_projects(body: str) -> list[Project]:
    projs: list[Project] = []
    macro_positions = list(_iter_macro(body, "resumeProjectHeading", 2))
    for idx, (args, start, end) in enumerate(macro_positions):
        header, dates = args
        nm = _PROJECT_NAME_STACK_RE.search(header)
        if nm:
            name = _strip_latex(nm.group("name"))
            stack_raw = nm.group("stack")
        else:
            # Fallback: take text before the first '|' as name
            parts = re.split(r"\$\|\$|\|", header, maxsplit=1)
            name = _strip_latex(parts[0])
            stack_raw = parts[1] if len(parts) > 1 else ""

        next_start = (
            macro_positions[idx + 1][1]
            if idx + 1 < len(macro_positions)
            else len(body)
        )
        bullets = _extract_bullets_between(body, end, next_start)
        projs.append(
            Project(
                name=name,
                stack=_parse_stack_string(stack_raw),
                date_range=parse_date_range(dates),
                bullets=bullets,
            )
        )
    return projs


_SKILL_CAT_RE = re.compile(
    r"\\textbf\{([^}]+)\}\s*\{?\s*:\s*([^\\]+?)\}?\s*(?=\\\\|\\textbf\{|\\end\{|\})",
    re.IGNORECASE | re.DOTALL,
)


def _parse_skills(body: str) -> Skills:
    skills = Skills()
    for m in _SKILL_CAT_RE.finditer(body):
        category_raw = _strip_latex(m.group(1)).strip().lower()
        items = _parse_stack_string(m.group(2))
        if "language" in category_raw:
            skills.languages = items
        elif any(k in category_raw for k in ("library", "libraries", "framework")):
            skills.libraries = items
        elif "tool" in category_raw:
            skills.tools = items
        else:
            skills.by_category[category_raw] = items
    return skills


# -- YOE computation --------------------------------------------------------


def _union_months(intervals: list[tuple[date, date]]) -> int:
    """Merge overlapping intervals and sum total months across the union."""
    if not intervals:
        return 0
    intervals = sorted(intervals, key=lambda x: x[0])
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ms, me = merged[-1]
        if s <= me:
            merged[-1] = (ms, max(me, e))
        else:
            merged.append((s, e))
    total = 0
    for s, e in merged:
        total += max(0, (e.year - s.year) * 12 + (e.month - s.month))
    return total


def compute_yoe(
    experiences: list[Experience],
    today: date | None = None,
) -> dict[str, float]:
    """Per-skill years of experience, computed as union of experience intervals
    that list the skill in their stack. Result rounded to 1 decimal place."""
    today = today or date.today()
    per_skill: dict[str, list[tuple[date, date]]] = {}
    for exp in experiences:
        if exp.date_range.start is None:
            continue
        end = exp.date_range.end if exp.date_range.end is not None else today
        for skill in exp.stack:
            key = skill.strip()
            per_skill.setdefault(key, []).append((exp.date_range.start, end))
    return {
        skill: round(_union_months(intervals) / 12.0, 1)
        for skill, intervals in per_skill.items()
    }


# -- Public entrypoint ------------------------------------------------------


def parse_tex(tex: str, track: Track) -> Profile:
    """Parse a full .tex file into a `Profile` for the given resume track."""
    tex = _strip_comments(tex)
    bodies = _section_bodies(tex)

    header = _parse_header(tex)

    edu_body = bodies.get("education", "")
    exp_body = bodies.get("experience", "")
    proj_body = bodies.get("projects", "")
    skills_body = bodies.get("technical skills", "")

    experiences = _parse_experiences(exp_body)
    profile = Profile(
        track=track,
        **header,
        education=_parse_education(edu_body),
        experiences=experiences,
        projects=_parse_projects(proj_body),
        skills=_parse_skills(skills_body),
        years_of_experience=compute_yoe(experiences),
    )
    return profile


def parse_tex_file(path: Path, track: Track) -> Profile:
    return parse_tex(path.read_text(encoding="utf-8"), track)
