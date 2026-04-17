"""Build `state/profile.json` from the resume submodule.

Runs offline — parses the 4 Jake Gutierrez .tex files (swe/ml/hpc/quant) and
writes one JSON document keyed by track. Called from the CLI and from
`.github/workflows/nightly.yml` when the submodule SHA bumps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from autoapply.config import Settings
from autoapply.profile.schema import Profile, Track
from autoapply.profile.tex_parser import parse_tex_file


# Tracks we actively maintain (excludes "all" and "new" which are draft resumes).
ACTIVE_TRACKS: tuple[Track, ...] = ("swe", "ml", "hpc", "quant")


def resume_path(resumes_dir: Path, track: Track) -> Path:
    return resumes_dir / f"aadit_nilay_resume_{track}.tex"


def build_profiles(settings: Settings, tracks: tuple[Track, ...] = ACTIVE_TRACKS) -> dict[str, Profile]:
    """Parse each track's .tex and return {track: Profile}."""
    assert all(t in get_args(Track) for t in tracks)
    profiles: dict[str, Profile] = {}
    for track in tracks:
        path = resume_path(settings.resumes_dir, track)
        if not path.exists():
            raise FileNotFoundError(
                f"resume .tex not found for track={track}: {path} "
                f"(is the `resumes/` submodule initialized?)"
            )
        profiles[track] = parse_tex_file(path, track)
    return profiles


def write_profiles(profiles: dict[str, Profile], out_path: Path) -> None:
    """Serialize {track: Profile} to a single JSON file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {track: p.model_dump(mode="json") for track, p in profiles.items()}
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def load_profiles(path: Path) -> dict[str, Profile]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {track: Profile.model_validate(body) for track, body in data.items()}


def run(settings: Settings) -> Path:
    """CLI helper — build, write, return the output path."""
    profiles = build_profiles(settings)
    out = settings.profile_json_path
    write_profiles(profiles, out)
    return out
