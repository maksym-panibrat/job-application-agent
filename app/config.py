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
    max_matches_displayed: int = 20
    job_sync_interval_hours: int = 24
    environment: str = "development"
    auth_enabled: bool = False
    google_oauth_client_id: SecretStr | None = None
    google_oauth_client_secret: SecretStr | None = None
    github_oauth_client_id: SecretStr | None = None
    github_oauth_client_secret: SecretStr | None = None
    jwt_secret: SecretStr = SecretStr("dev-secret")
    cron_shared_secret: SecretStr = SecretStr("dev-cron-secret")
    search_auto_pause_days: int = 7
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "job-application-agent"
    job_stale_after_days: int = 14
    log_level: str = "INFO"
    matching_max_concurrency: int = 2
    matching_jobs_per_batch: int = 20

    cors_allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment == "production":
            if self.jwt_secret.get_secret_value() == "dev-secret":
                raise ValueError("jwt_secret must be set in production")
            if self.cron_shared_secret.get_secret_value() == "dev-cron-secret":
                raise ValueError("cron_shared_secret must be set in production")
            if self.auth_enabled:
                if not self.google_oauth_client_id or not self.google_oauth_client_secret:
                    raise ValueError("Google OAuth credentials required when AUTH_ENABLED=true")
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
