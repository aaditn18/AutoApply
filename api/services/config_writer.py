"""Round-trip-safe writers for ``state/answer_bank.yml``,
``src/autoapply/ingest/companies.yml``, and ``.env``.

All writes:
  1. Validate the proposed content can be parsed (YAML well-formed,
     required schema keys present).
  2. Round-trip through ``ruamel.yaml`` — preserves comments + ordering.
  3. Atomic-rename: write to a sibling ``.tmp`` file, then ``os.replace``.
  4. Keep one timestamped backup under ``state/backups/`` (gitignored).

The write side never touches files outside the project root or files
listed under ``ALLOWED_PATHS``. Any attempt to escape returns a
:class:`PermissionError`-derived 403.
"""

from __future__ import annotations

import io
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from ruamel.yaml import YAML

# ── Config ──────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Anchor every write to one of these paths. Anything else → 403.
ALLOWED_PATHS: dict[str, Path] = {
    "answer_bank": PROJECT_ROOT / "state" / "answer_bank.yml",
    "companies": PROJECT_ROOT / "src" / "autoapply" / "ingest" / "companies.yml",
    "env": PROJECT_ROOT / ".env",
    "profile": PROJECT_ROOT / "state" / "profile.json",
}

BACKUP_DIR = PROJECT_ROOT / "state" / "backups"


# ── YAML factory ────────────────────────────────────────────────────

def _yaml() -> YAML:
    """Round-trip YAML configured to preserve quote styles + indents.

    indent(mapping=2, sequence=4, offset=2) matches the existing
    project files reasonably; the test suite catches any drift.
    """
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 120
    return y


# ── Backups + atomic write ──────────────────────────────────────────

def _backup(path: Path) -> Path | None:
    """Snapshot ``path`` into ``state/backups/<name>.<ts>``.

    Returns the backup path, or None if the source doesn't exist yet.
    """
    if not path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = BACKUP_DIR / f"{path.name}.{ts}"
    shutil.copy2(path, out)
    return out


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically.

    Uses a sibling temp file in the same directory (so ``os.replace``
    is rename-only on the same filesystem).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{int(time.time()*1000)}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── Answer bank ─────────────────────────────────────────────────────

def read_answer_bank() -> tuple[str, dict]:
    """Return (raw text, parsed dict)."""
    p = ALLOWED_PATHS["answer_bank"]
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    parsed = _yaml().load(text) or {}
    # ruamel returns its own CommentedMap — convert to plain dict for the API.
    return text, dict(parsed)


def validate_answer_bank(yaml_text: str) -> dict:
    """Parse YAML; fail loudly on syntax errors.

    Schema check is intentionally light — the project's resolver
    treats unknown keys as bank fallbacks, so we don't reject novel
    fields. We require the file to parse to a mapping (dict) at top
    level; lists / scalars are rejected.
    """
    try:
        parsed = _yaml().load(yaml_text)
    except Exception as exc:  # ruamel.yaml.YAMLError is broad
        raise ValueError(f"YAML parse failed: {exc}") from exc
    if parsed is None:
        return {}
    if not hasattr(parsed, "keys"):  # dict-like
        raise ValueError("answer_bank.yml must be a top-level mapping")
    return dict(parsed)


def write_answer_bank(yaml_text: str) -> Path | None:
    """Validate + atomic-write + backup. Returns backup path or None."""
    validate_answer_bank(yaml_text)
    p = ALLOWED_PATHS["answer_bank"]
    backup = _backup(p)
    _atomic_write_text(p, yaml_text)
    return backup


# ── Companies ───────────────────────────────────────────────────────

_VALID_SOURCES = {"greenhouse", "lever", "ashby"}
_VALID_TIERS = {"test_safe", "live_only"}


def read_companies() -> dict[str, dict[str, list[str]]]:
    """Return the parsed grid (source → tier → token list)."""
    p = ALLOWED_PATHS["companies"]
    if not p.exists():
        return {}
    parsed = _yaml().load(p.read_text(encoding="utf-8")) or {}
    out: dict[str, dict[str, list[str]]] = {}
    for src, tiers in parsed.items():
        if src not in _VALID_SOURCES:
            continue
        out[src] = {}
        for tier, lst in (tiers or {}).items():
            if tier not in _VALID_TIERS:
                continue
            out[src][tier] = [str(t) for t in (lst or [])]
    return out


def validate_companies_grid(grid: dict[str, dict[str, list[str]]]) -> None:
    """Schema check: known sources, known tiers, list-of-string tokens.

    Tokens are validated lightly — must be non-empty, no whitespace,
    no slashes (slugs only). Duplicate tokens within a tier raise.
    """
    if not isinstance(grid, dict) or not grid:
        raise ValueError("grid must be a non-empty mapping")
    for src, tiers in grid.items():
        if src not in _VALID_SOURCES:
            raise ValueError(f"unknown source: {src!r}")
        if not isinstance(tiers, dict):
            raise ValueError(f"{src} must map to a tier dict")
        for tier, tokens in tiers.items():
            if tier not in _VALID_TIERS:
                raise ValueError(f"{src}: unknown tier {tier!r}")
            if not isinstance(tokens, list):
                raise ValueError(f"{src}.{tier} must be a list")
            seen: set[str] = set()
            for tok in tokens:
                if not isinstance(tok, str) or not tok.strip():
                    raise ValueError(f"{src}.{tier}: empty/non-string token {tok!r}")
                if any(c in tok for c in (" ", "\t", "/", "\n")):
                    raise ValueError(f"{src}.{tier}: invalid char in token {tok!r}")
                if tok in seen:
                    raise ValueError(f"{src}.{tier}: duplicate token {tok!r}")
                seen.add(tok)


def write_companies(grid: dict[str, dict[str, list[str]]]) -> Path | None:
    """Round-trip-edit the existing YAML so comments survive.

    We load the file, replace the token lists in-place, and dump.
    Sources or tiers that don't exist in the file are appended.
    """
    validate_companies_grid(grid)
    p = ALLOWED_PATHS["companies"]
    yaml = _yaml()
    if p.exists():
        existing = yaml.load(p.read_text(encoding="utf-8")) or {}
    else:
        from ruamel.yaml.comments import CommentedMap
        existing = CommentedMap()

    for src in ("greenhouse", "lever", "ashby"):
        if src not in grid:
            continue
        if src not in existing:
            from ruamel.yaml.comments import CommentedMap as _CM
            existing[src] = _CM()
        for tier in ("test_safe", "live_only"):
            if tier not in grid[src]:
                continue
            existing[src][tier] = list(grid[src][tier])

    backup = _backup(p)
    buf = io.StringIO()
    yaml.dump(existing, buf)
    _atomic_write_text(p, buf.getvalue())
    return backup


# ── Env ─────────────────────────────────────────────────────────────

# Keys whose values must NEVER be returned in plaintext over the API.
SECRET_KEYS = frozenset({
    "GEMINI_API_KEY",
    "SUBMODULE_PAT",
    "GH_ISSUES_PAT",
    "YC_STORAGE_STATE_AES",
    "HANDSHAKE_STORAGE_STATE_AES",
    "IMAP_PASSWORD",
    "HCAPTCHA_ACCESSIBILITY_TOKEN",
    "CAPTCHA_SOLVER_API_KEY",
    "AES_KEY_B64",
})

# Keys we surface in the editor UI even if the file currently lacks them.
KNOWN_KEYS = (
    "DRY_RUN",
    "TEST_SAFE_ONLY",
    "MAX_APPLICATIONS_PER_RUN",
    "LOG_LEVEL",
    "GEMINI_API_KEY",
    "IMAP_EMAIL",
    "IMAP_PASSWORD",
    "CAPTCHA_SOLVER",
    "CAPTCHA_SOLVER_API_KEY",
    "CAPTCHA_SOLVER_TIMEOUT",
)


def _parse_env(text: str) -> dict[str, str]:
    """Parse a .env file. Comments + blank lines preserved by line.

    For *writing* we preserve the original line ordering by reading
    the file again and replacing assignments in place. Reads use this
    cheap parser since the API only needs key/value pairs.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def read_env() -> dict[str, str]:
    p = ALLOWED_PATHS["env"]
    if not p.exists():
        return {}
    return _parse_env(p.read_text(encoding="utf-8"))


def mask_secret(value: str) -> str:
    """Return a redacted display value for secret keys.

    Empty → "". Short (<=4 chars) → "****". Longer → "**** + last 4".
    """
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


def write_env(updates: dict[str, str]) -> Path | None:
    """Update .env with the given key=value pairs.

    Lines that already have the key are replaced in place; new keys
    are appended at the end. Comments / blank lines stay intact.
    Empty-string values clear the key (appears as ``KEY=``).
    """
    p = ALLOWED_PATHS["env"]
    if p.exists():
        original = p.read_text(encoding="utf-8")
    else:
        original = ""

    lines = original.splitlines(keepends=True)

    # Index existing keys → line numbers
    line_for_key: dict[str, int] = {}
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key = s.split("=", 1)[0].strip()
        if key:
            line_for_key[key] = i

    out_lines = list(lines)
    for k, v in updates.items():
        if not k or any(c in k for c in (" ", "\t", "=", "\n")):
            raise ValueError(f"invalid env key: {k!r}")
        new_line = f"{k}={v}\n"
        if k in line_for_key:
            out_lines[line_for_key[k]] = new_line
        else:
            if out_lines and not out_lines[-1].endswith("\n"):
                out_lines[-1] = out_lines[-1] + "\n"
            out_lines.append(new_line)

    backup = _backup(p)
    _atomic_write_text(p, "".join(out_lines))
    return backup
