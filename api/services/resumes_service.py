"""List per-track resume PDFs from ``resumes/``.

The resume submodule contains 4 tracks (swe / ml / hpc / quant) plus
an optional "all" + a "new" variant. Each track may have a .tex
source, .pdf compiled output, and .txt extraction.

UI uses this for the Resume Manager page (Phase 4) — read-only
listing + size + last-modified. Edits stay in the submodule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from api.schemas import ResumeOut


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESUMES_DIR = PROJECT_ROOT / "resumes"

# Per-track filename pattern: ``aadit_nilay_resume_<track>.{pdf,tex,txt}``.
_TRACKS = ("swe", "ml", "hpc", "quant")


def _stat_or_none(p: Path) -> datetime | None:
    if not p.exists():
        return None
    return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)


def list_resumes() -> list[ResumeOut]:
    out: list[ResumeOut] = []
    for track in _TRACKS:
        pdf = RESUMES_DIR / f"aadit_nilay_resume_{track}.pdf"
        tex = RESUMES_DIR / f"aadit_nilay_resume_{track}.tex"
        txt = RESUMES_DIR / f"aadit_nilay_resume_{track}.txt"
        out.append(
            ResumeOut(
                track=track,
                pdf_path=str(pdf),
                pdf_size=pdf.stat().st_size if pdf.exists() else 0,
                tex_present=tex.exists(),
                txt_present=txt.exists(),
                last_modified=_stat_or_none(pdf) or _stat_or_none(tex),
            )
        )
    return out
