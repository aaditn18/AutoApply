"""Applicator base class + shared helpers.

Each concrete applicator (greenhouse_apply, lever_apply) subclasses
`Applicator` and implements `fetch_form`, `build_payload`, and
`submit`. The orchestrator (cli/apply) calls `apply(job)`, which:

1. Fetches the form metadata.
2. Resolves every field via `standard_fields.resolve_all`.
3. If any required fields are unresolved OR any field is
   `requires_review`/`requires_llm` → returns `ApplyResult(outcome='review')`
   so the caller can route to the GitHub Issues queue.
4. Otherwise builds the payload and submits (unless `dry_run=True`, in
   which case the payload is dumped to `state/dry_runs/<id>.json`).

`dry_run` is True by default — callers must explicitly pass
`dry_run=False` for a real submit. This mirrors `Settings.DRY_RUN`.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoapply.answers.bank import AnswerBank
from autoapply.profile.schema import Profile
from autoapply.tracker.models import Job

from autoapply.execute.audit import log_resolution_audit
from autoapply.execute.review_flags import build_review_payload
from autoapply.execute.standard_fields import (
    FieldSpec,
    ResolvedField,
    UnresolvedField,
    resolve_all,
    resolve_all_batched,
)


log = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    """Outcome of a single apply call."""

    outcome: str  # "ok" | "failed" | "captcha" | "review" | "dry_run"
    error_code: str = ""
    error_message: str = ""
    answers: dict[str, str] = field(default_factory=dict)
    resolved: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    cover_letter_text: str = ""
    review_reasons: list[str] = field(default_factory=list)
    # Structured per-field flag records. Persisted to the review_flags table
    # by the caller so we can mine "questions that blocked us" over time.
    # Each entry: field_name, field_label, field_kind, required, options,
    #             reason, question_type, attempted_value
    review_flags: list[dict[str, Any]] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Applicator(ABC):
    """Base class for per-ATS applicators."""

    name: str = "base"

    def __init__(
        self,
        *,
        profile: Profile,
        bank: AnswerBank,
        track: str,
        resume_path: str | None = None,
        cover_letter_text: str | None = None,
        dry_runs_dir: Path | None = None,
    ):
        self.profile = profile
        self.bank = bank
        self.track = track
        self.resume_path = resume_path
        self.cover_letter_text = cover_letter_text
        self.dry_runs_dir = dry_runs_dir

    # ---- Subclass hooks -------------------------------------------------

    @abstractmethod
    def fetch_form(self, job: Job) -> list[FieldSpec]:
        """Return the form spec for the given job."""

    @abstractmethod
    def build_payload(self, job: Job, resolved: list[ResolvedField]) -> dict[str, Any]:
        """Build the body we'd POST to the ATS submit endpoint."""

    @abstractmethod
    def submit(self, job: Job, payload: dict[str, Any]) -> ApplyResult:
        """Really POST the payload. Called only when dry_run=False."""

    # ---- Public entry point --------------------------------------------

    def apply(self, job: Job, *, dry_run: bool = True) -> ApplyResult:
        """Resolve form, decide auto vs review, then submit (or dump)."""
        try:
            specs = self.fetch_form(job)
        except Exception as exc:  # pragma: no cover — network-heavy path
            log.exception("fetch_form failed for job %s", job.canonical_key)
            return ApplyResult(
                outcome="failed", error_code="fetch_form", error_message=str(exc)
            )

        # Use the batched resolver: one Gemini call per application
        # (cascading through the free-tier model list on rate-limit
        # errors) handles every required dropdown and every required
        # free-response that the classifier + bank couldn't resolve.
        # Non-required unresolved fields are left blank by design.
        resolved, unresolved, batch_audit = resolve_all_batched(
            specs,
            profile=self.profile,
            bank=self.bank,
            track=self.track,
            cover_letter_text=self.cover_letter_text,
            resume_path=self.resume_path,
            company=job.company or "",
            job_title=job.title or "",
            # Job description is passed to the batch LLM so essay answers
            # (why_company, why_role, strengths/weaknesses, freeform
            # textareas) can cite concrete details from the posting
            # rather than recycling per-track templates. Sanitized +
            # wrapped in <UNTRUSTED> inside the prompt — see llm_batch.
            job_description=job.description or "",
        )

        # Human-readable audit line — which Qs were sent to the LLM, which
        # model answered, how many answers came back. Persisted on the
        # ApplyResult so the daily digest can show it.
        if batch_audit.get("batch_asked"):
            log.info(
                "batch-llm: asked=%d  model=%s  answered=%d  error=%s",
                len(batch_audit.get("batch_asked", [])),
                batch_audit.get("model_used") or "<none>",
                batch_audit.get("answer_count", 0),
                batch_audit.get("error") or "—",
            )

        # Per-field audit: for every resolved field, log (label, value, source).
        # Grouped so the terminal output is scannable at a glance. Lives
        # in :mod:`.audit` so it can be re-used outside the Applicator.
        log_resolution_audit(resolved, unresolved)

        # Build review-queue payload: tags for the issue title plus the
        # structured per-field flags the GH-Issues renderer uses. Logic
        # extracted to :mod:`.review_flags` so the "does this need
        # review?" decision is independently testable.
        review_reasons, review_flags = build_review_payload(
            specs, resolved, unresolved,
        )

        answers = {r.name: r.value for r in resolved if r.value}

        if review_reasons:
            return ApplyResult(
                outcome="review",
                answers=answers,
                resolved=[_resolved_to_dict(r) for r in resolved],
                unresolved=[{"name": u.name, "label": u.label, "reason": u.reason} for u in unresolved],
                cover_letter_text=self.cover_letter_text or "",
                review_reasons=review_reasons,
                review_flags=review_flags,
            )

        payload = self.build_payload(job, resolved)

        if dry_run:
            artifact_path = self._dump_dry_run(job, payload, resolved)
            arts: dict[str, Any] = {"batch_audit": batch_audit}
            if artifact_path:
                arts["payload_json"] = str(artifact_path)
            return ApplyResult(
                outcome="dry_run",
                answers=answers,
                resolved=[_resolved_to_dict(r) for r in resolved],
                artifacts=arts,
                cover_letter_text=self.cover_letter_text or "",
            )

        try:
            result = self.submit(job, payload)
            # Attach the batch audit trail so downstream callers / the DB
            # ``applications.artifacts`` column retains which model answered
            # which questions, and we can audit the mix of
            # code-answered vs LLM-answered fields per application.
            if "batch_audit" not in result.artifacts:
                result.artifacts["batch_audit"] = batch_audit
            return result
        except Exception as exc:  # pragma: no cover — network-heavy path
            log.exception("submit failed for job %s", job.canonical_key)
            return ApplyResult(
                outcome="failed",
                error_code="submit",
                error_message=str(exc),
                artifacts={"batch_audit": batch_audit},
            )

    # ---- Helpers --------------------------------------------------------

    def _log_resolution_audit(
        self,
        resolved: list[ResolvedField],
        unresolved: list[UnresolvedField],
    ) -> None:
        """Back-compat delegator. New code calls :func:`.audit.log_resolution_audit` directly."""
        log_resolution_audit(resolved, unresolved)

    def _dump_dry_run(
        self, job: Job, payload: dict[str, Any], resolved: list[ResolvedField]
    ) -> Path | None:
        if self.dry_runs_dir is None:
            return None
        self.dry_runs_dir.mkdir(parents=True, exist_ok=True)
        key = job.canonical_key or f"{self.name}-{job.id or 'x'}"
        path = self.dry_runs_dir / f"{key}.json"
        body = {
            "ts": _now_iso(),
            "applicator": self.name,
            "track": self.track,
            "job": {
                "canonical_key": job.canonical_key,
                "source": job.source,
                "source_id": job.source_id,
                "board_token": job.board_token,
                "url": job.url,
                "title": job.title,
                "company": job.company,
                "location": job.location,
            },
            "resume_path": self.resume_path,
            "resolved": [_resolved_to_dict(r) for r in resolved],
            "payload": payload,
        }
        path.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
        return path


def _resolved_to_dict(r: ResolvedField) -> dict[str, Any]:
    d = asdict(r)
    # Replace enum with its string value so JSON roundtrips cleanly.
    qt = d.get("question_type")
    d["question_type"] = qt.value if qt and hasattr(qt, "value") else qt
    return d
