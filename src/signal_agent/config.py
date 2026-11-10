"""Settings come from the environment only (or ~/.config/signal-agent/env). Never from the repo."""
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

USER_ENV = Path.home() / ".config" / "signal-agent" / "env"
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(USER_ENV), ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    signal_database_url: str = "postgresql://signal:signal-local-only@127.0.0.1:55433/signal"
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    # anthropic | openrouter | auto (auto: Anthropic when its key is set, else OpenRouter)
    signal_llm_provider: str = "auto"
    # empty = the provider's defaults (llm/factory.py)
    signal_model_small: str = ""
    signal_model_large: str = ""
    signal_model_judge: str = ""
    signal_model_fallbacks: str = ""  # OpenRouter only: comma-separated models tried when the primary fails
    signal_max_requests: int | None = None  # hard cap on model requests per process (free-tier budgeting)
    signal_llm_cache: bool = True  # cache model replies on disk (data/cache/llm), so re-runs cost nothing
    signal_profile: str = "ayush"
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    # deploys mount private files (Render secret files land in /etc/secrets); empty = profiles/<name>/...
    signal_evidence_file: str = ""
    signal_denylist_file: str = ""
    signal_ui_password: str | None = None  # when set, the review UI requires HTTP basic auth (user "signal")

    @property
    def profile_dir(self) -> Path:
        return REPO_ROOT / "profiles" / self.signal_profile


def get_settings() -> Settings:
    return Settings()
