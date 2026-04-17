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
