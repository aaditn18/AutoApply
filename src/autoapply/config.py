"""Runtime configuration — all values come from environment variables or .env file."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Central settings object. Constructed once in main entrypoints."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # --- Paths ---
    repo_root: Path = REPO_ROOT
    state_dir: Path = REPO_ROOT / "state"
    resumes_dir: Path = REPO_ROOT / "resumes"
    prompts_dir: Path = REPO_ROOT / "prompts"
    dry_runs_dir: Path = REPO_ROOT / "state" / "dry_runs"

    # --- LLM (Gemini only) ---
    GEMINI_API_KEY: str = ""

    # --- GitHub ---
    SUBMODULE_PAT: str = ""
    GH_ISSUES_PAT: str = ""
    GH_REPO_OWNER: str = "aaditn18"
    GH_REPO_NAME: str = "AutoApply"

    # --- Secrets / Sessions ---
    AES_KEY_B64: str = ""
    YC_STORAGE_STATE_AES: str = ""
    HANDSHAKE_STORAGE_STATE_AES: str = ""

    # hCaptcha accessibility bypass token (free — register once at
    # https://accounts.hcaptcha.com/accessibility then copy the
    # `hc_accessibility` cookie value here).  When set, Playwright
    # injects the cookie before navigating to any Lever apply page,
    # which causes hCaptcha to pass silently without a user challenge.
    HCAPTCHA_ACCESSIBILITY_TOKEN: str = ""

    # Third-party captcha solver (paid).  When CAPTCHA_SOLVER is set to a
    # supported provider AND CAPTCHA_SOLVER_API_KEY is populated, AutoApply
    # will submit any blocking hCaptcha (or reCAPTCHA v2) challenge it
    # encounters to the solver's API, wait for a token, inject the token
    # into the page, and retry the submit.
    #   CAPTCHA_SOLVER = "" | "anticaptcha" | "capsolver" | "2captcha" |
    #                    "2captcha_coords" | "capmonster"
    # Typical cost: ~$1–$3 per 1000 hCaptcha solves.
    # Notes:
    #   "anticaptcha"     — most reliable; no account-level gating.
    #   "2captcha"        — SMART ROUTING: detects captcha type (hCaptcha
    #                       image-grid, reCAPTCHA v2, Turnstile) and calls
    #                       the corresponding task type on 2Captcha's JSON
    #                       API v2 (api.2captcha.com/createTask). hCaptcha
    #                       routes to GridTask — 2Captcha dropped hCaptcha
    #                       token solving from their current API entirely.
    #   "2captcha_coords" — legacy alias forcing the Grid path; debug-only.
    # Register at https://anti-captcha.com, https://www.capsolver.com,
    # https://2captcha.com, or https://capmonster.cloud.
    CAPTCHA_SOLVER: str = ""
    CAPTCHA_SOLVER_API_KEY: str = ""
    # Maximum seconds to wait for the solver to return a token.
    CAPTCHA_SOLVER_TIMEOUT: int = 180

    # --- Email verification (Greenhouse OTP) ---
    # Some Greenhouse tenants send a one-time code to the applicant's email
    # to confirm identity before the application is accepted.  When these
    # credentials are set AutoApply fetches the code automatically via IMAP
    # and completes the verification without human intervention.
    #
    # For aaditnilay18@gmail.com:
    #   IMAP_SERVER   = imap.gmail.com
    #   IMAP_EMAIL    = aaditnilay18@gmail.com
    #   IMAP_PASSWORD = <16-char Google App Password>
    #                   (Google Account → Security → App Passwords → "Mail")
    IMAP_SERVER: str = "imap.gmail.com"
    IMAP_PORT: int = 993
    IMAP_EMAIL: str = ""
    IMAP_PASSWORD: str = ""
    # How long (seconds) to wait for the verification email to arrive.
    IMAP_CODE_TIMEOUT: int = 90

    # --- Operational ---
    DRY_RUN: bool = True
    # When True (default), the ingest command only pulls from `test_safe`
    # companies. Set to False only when you're confident the pipeline works
    # and ready to ingest quant funds / top AI labs / elite HPC targets.
    TEST_SAFE_ONLY: bool = True
    MAX_APPLICATIONS_PER_RUN: int = 5
    MAX_APPLICATIONS_PER_DAY: int = 60
    MAX_REVIEWS_PER_DAY: int = 20
    MIN_FINAL_RANK: float = 0.5
    LOG_LEVEL: str = "INFO"

    # --- Per-run caps ---
    INGEST_PAGE_LIMIT: int = 50
    APPLY_DELAY_SECONDS_MIN: int = 30
    APPLY_DELAY_SECONDS_MAX: int = 120

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.state_dir}/jobs.sqlite"

    @property
    def profile_json_path(self) -> Path:
        return self.state_dir / "profile.json"

    @property
    def answer_bank_path(self) -> Path:
        return self.state_dir / "answer_bank.yml"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Lazy singleton — most callers should use this."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
