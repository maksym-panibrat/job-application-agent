from pydantic import PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn
    google_api_key: SecretStr = SecretStr("")
    llm_generation_model: str = "gemini-2.5-pro"
    llm_matching_model: str = "gemini-2.5-flash"
    llm_resume_extraction_model: str = "gemini-2.5-flash"
    match_score_threshold: float = 0.65
    environment: str = "development"
    google_oauth_client_id: SecretStr | None = None
    google_oauth_client_secret: SecretStr | None = None
    jwt_secret: SecretStr = SecretStr("dev-secret")
    cron_shared_secret: SecretStr = SecretStr("dev-cron-secret")
    search_auto_pause_days: int = 7
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "job-application-agent"
    job_stale_after_days: int = 21
    log_level: str = "INFO"
    feedback_webhook_url: SecretStr | None = None
    feedback_webhook_timeout_seconds: float = 3.0
    queue_depth_emit_interval_s: int = 60
    batch_match_enabled: bool = False
    batch_match_dry_run: bool = True
    batch_match_provider: str = "fake"
    batch_match_prompt_version: str = "batch-match-v1"
    batch_match_max_apps_per_request: int = 10
    batch_match_max_request_chars: int = 60000
    batch_match_poll_interval_seconds: int = 60
    batch_match_max_items_per_batch: int = 100
    batch_match_candidate_pool_multiplier: int = 3
    batch_match_manual_sync_max_items: int = 50
    batch_match_cron_max_items: int = 100

    cors_allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]
    # Absolute base URL Google sees as redirect_uri host. Cloud Run forwards HTTP to
    # the container, so request-derived URLs would arrive as http:// — Google rejects
    # those. Setting this explicitly avoids depending on proxy-header forwarding.
    public_base_url: str | None = None

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment == "production":
            if self.jwt_secret.get_secret_value() == "dev-secret":
                raise ValueError("jwt_secret must be set in production")
            if self.cron_shared_secret.get_secret_value() == "dev-cron-secret":
                raise ValueError("cron_shared_secret must be set in production")
            if not self.google_oauth_client_id or not self.google_oauth_client_secret:
                raise ValueError("Google OAuth credentials required in production")
            if not self.public_base_url:
                raise ValueError("public_base_url must be set in production")
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
