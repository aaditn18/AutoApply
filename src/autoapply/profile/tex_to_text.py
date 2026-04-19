"""Convert a Jake Gutierrez resume ``.tex`` file to plain-text for
form fields that ask for a pasted resume.

Some Greenhouse / Lever tenants render a ``resume_text`` textarea
alongside the file-upload input. Leaving it blank used to be fine
(most SPAs no longer render it), but some tenants still require a
non-empty value. Rather than having the LLM *regenerate* the resume
from scratch (expensive, and the output never matches the actual
PDF), we extract plain text directly from the same ``.tex`` source
the PDFs were compiled from — guaranteed-identical content.

Approach
--------
1. Strip LaTeX preamble (anything before ``\\begin{document}``).
2. Strip ``\\end{document}`` and trailing content.
3. Remove LaTeX commands but preserve arguments that hold text:
     * ``\\section{NAME}`` → ``NAME`` with section styling.
     * ``\\item`` → ``- ``.
     * ``\\textbf{text}`` / ``\\textit{text}`` → ``text``.
     * ``\\href{url}{text}`` → ``text`` (drop URL).
     * All other ``\\cmd{arg}`` → ``arg`` when the command is known
       to wrap text; else dropped entirely.
4. Collapse whitespace + blank lines.

The result is a readable plain-text rendering — not a pixel-perfect
reproduction of the PDF, but close enough for an ATS textarea.
Cached in-process via ``functools.lru_cache`` so repeated apply-to
calls in one session only parse each ``.tex`` once.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path


log = logging.getLogger(__name__)


# LaTeX commands whose first {...} argument is plain text to keep.
# Everything else is dropped (the whole ``\cmd{...}`` sequence).
_TEXT_WRAPPERS: frozenset[str] = frozenset({
    "textbf", "textit", "emph", "underline", "texttt",
    "section", "subsection", "subsubsection",
    "resumeSubheading", "resumeProjectHeading",
    "resumeItem", "item",
    "href",   # special-cased below: takes (url, text), keep text
    "url",    # special-cased: keep the URL itself as text
})

# LaTeX commands that should be fully dropped (including their args).
_DROP_COMMANDS: frozenset[str] = frozenset({
    "documentclass", "usepackage", "geometry", "pagestyle",
    "setlength", "setmainfont", "renewcommand", "newcommand",
    "titleformat", "titlespacing", "hypersetup",
    "fancyhdr", "urlstyle", "raggedbottom", "raggedright",
    "input", "include",
    "maketitle", "noindent", "vspace", "hspace",
    "newpage", "pagebreak", "clearpage",
    "resumeSubHeadingListStart", "resumeSubHeadingListEnd",
    "resumeItemListStart", "resumeItemListEnd",
    "small", "large", "normalsize", "scshape",
    "rule", "hfill", "centering", "begin", "end",
    "tabular", "tabularx", "multicolumn", "cline", "hline",
})


def _strip_preamble_and_envelope(tex: str) -> str:
    """Remove everything before ``\\begin{document}`` and after
    ``\\end{document}``."""
    m = re.search(r"\\begin\{document\}", tex)
    if m:
        tex = tex[m.end():]
    m = re.search(r"\\end\{document\}", tex)
    if m:
        tex = tex[: m.start()]
    return tex


def _strip_comments(tex: str) -> str:
    """Strip ``%`` comments — but not escaped ``\\%``."""
    out_lines: list[str] = []
    for line in tex.splitlines():
        # Find unescaped % and cut from there.
        i = 0
        while i < len(line):
            if line[i] == "%" and (i == 0 or line[i - 1] != "\\"):
                line = line[:i]
                break
            i += 1
        out_lines.append(line)
    return "\n".join(out_lines)


def _replace_commands(tex: str) -> str:
    """Replace LaTeX commands with plain text equivalents."""
    # \href{url}{text} → text (special handling — 2 args).
    tex = re.sub(
        r"\\href\{[^{}]*\}\{([^{}]*)\}", r"\1", tex,
    )
    # \url{u} → u
    tex = re.sub(r"\\url\{([^{}]*)\}", r"\1", tex,
    )
    # \item{X} or \item X → bullet + X
    tex = re.sub(r"\\item\s*\{([^{}]*)\}", r"- \1", tex)
    tex = re.sub(r"\\item\b\s*", "- ", tex)
    # \resumeSubheading{title}{date}{company}{location} → "title, company (date, location)"
    # Simpler: replace with just the first arg (title) for readability.
    tex = re.sub(
        r"\\resumeSubheading"
        r"\{([^{}]*)\}\{([^{}]*)\}\{([^{}]*)\}\{([^{}]*)\}",
        r"\n\1  |  \3  |  \2  |  \4\n",
        tex,
    )
    tex = re.sub(
        r"\\resumeProjectHeading"
        r"\{([^{}]*)\}\{([^{}]*)\}",
        r"\n\1  |  \2\n",
        tex,
    )
    tex = re.sub(
        r"\\resumeItem\{([^{}]*)\}", r"- \1", tex,
    )
    # \section{TITLE} → TITLE\n-----
    def _section(m: re.Match[str]) -> str:
        title = m.group(1).strip()
        return f"\n\n{title.upper()}\n{'-' * len(title)}\n"
    tex = re.sub(r"\\section\{([^{}]*)\}", _section, tex)
    tex = re.sub(r"\\subsection\{([^{}]*)\}", r"\n\1\n", tex)
    # Drop environment delimiters cleanly — they shouldn't leak through.
    tex = re.sub(r"\\begin\{[^{}]*\}", "", tex)
    tex = re.sub(r"\\end\{[^{}]*\}", "", tex)
    # Drop all known structural commands (no args to preserve).
    drop_cmds = "|".join(re.escape(c) for c in _DROP_COMMANDS)
    # With a brace argument.
    tex = re.sub(rf"\\(?:{drop_cmds})\b[*]?\s*\{{[^{{}}]*\}}", "", tex)
    # Without a brace argument (just the command token).
    tex = re.sub(rf"\\(?:{drop_cmds})\b[*]?", "", tex)
    # \textbf{X}, \textit{X}, \emph{X}, \underline{X}, \texttt{X} → X
    for cmd in ("textbf", "textit", "emph", "underline", "texttt", "textsc"):
        tex = re.sub(rf"\\{cmd}\{{([^{{}}]*)\}}", r"\1", tex)
    # Any remaining \cmd{ARG} → ARG (conservative — drops the command,
    # keeps the braced argument text).
    tex = re.sub(r"\\[a-zA-Z]+\*?\s*\{([^{}]*)\}", r"\1", tex)
    # Any remaining bare commands (\cmd) → dropped.
    tex = re.sub(r"\\[a-zA-Z]+\*?", "", tex)
    # Backslash-escaped chars: \& → &, \% → %, \$ → $, \_ → _, \# → #.
    tex = re.sub(r"\\([&%$_#])", r"\1", tex)
    # ``\\`` (LaTeX line break) → newline.
    tex = tex.replace("\\\\", "\n")
    # Math mode: drop $...$ wrappers (keep the content).
    tex = re.sub(r"\$([^$]*)\$", r"\1", tex)
    # Drop leftover standalone ``|`` separators around now-empty math.
    tex = re.sub(r"(?<!\w) \| (?!\w)", " ", tex)
    # ``mailto:addr`` → ``addr`` (we already stripped \href wrappers
    # but the linked text sometimes carries the scheme prefix).
    tex = re.sub(r"mailto:\s*", "", tex)
    # URLs that got mashed into adjacent display text — inject a space.
    tex = re.sub(r"(https?://[^\s]+)(?=[A-Z])", r"\1 ", tex)
    # Leftover single braces — drop (they're control-flow artifacts).
    tex = tex.replace("{", "").replace("}", "")
    return tex


def _collapse_whitespace(text: str) -> str:
    """Normalize whitespace: collapse runs of spaces, drop repeated
    blank lines, trim per-line."""
    lines = [ln.rstrip() for ln in text.splitlines()]
    # Collapse intra-line runs of whitespace (but preserve single leading indent).
    lines = [re.sub(r"[ \t]{2,}", " ", ln) for ln in lines]
    # Collapse 3+ blank lines → 1.
    out: list[str] = []
    blank_count = 0
    for ln in lines:
        if ln.strip():
            blank_count = 0
            out.append(ln.strip() if not ln.startswith("- ") else ln)
        else:
            blank_count += 1
            if blank_count <= 1:
                out.append("")
    # Trim leading + trailing blanks.
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out)


def tex_to_plaintext(tex: str) -> str:
    """Convert a ``.tex`` resume source to plain text."""
    tex = _strip_preamble_and_envelope(tex)
    tex = _strip_comments(tex)
    tex = _replace_commands(tex)
    tex = _collapse_whitespace(tex)
    return tex


@lru_cache(maxsize=8)
def resume_plaintext_for_track(resumes_dir_str: str, track: str) -> str:
    """Read + convert the ``.tex`` file for a track. Cached.

    ``resumes_dir_str`` is a string (not Path) so ``lru_cache`` can hash it.
    """
    path = Path(resumes_dir_str) / f"aadit_nilay_resume_{track}.tex"
    if not path.exists():
        log.debug("tex_to_plaintext: %s not found", path)
        return ""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        log.debug("tex_to_plaintext: read failed for %s: %s", path, exc)
        return ""
    return tex_to_plaintext(raw)
