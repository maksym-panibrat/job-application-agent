from pydantic import PostgresDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn
    google_api_key: SecretStr = SecretStr("")
    llm_generation_model: str = "gemini-2.5-pro"
    llm_matching_model: str = "gemini-2.5-flash"
    llm_resume_extraction_model: str = "gemini-2.5-flash"
    adzuna_app_id: str = ""
    adzuna_api_key: SecretStr = SecretStr("")
    match_score_threshold: float = 0.65
    max_matches_displayed: int = 20
    job_sync_interval_hours: int = 24
    environment: str = "development"
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
    adzuna_max_queries_per_sync: int = 3
    adzuna_max_pages_per_sync: int = 1
    adzuna_cache_ttl_hours: int = 24
    adzuna_search_distance_km: int = 50
    adzuna_category: str = "it-jobs"
    tavily_api_key: SecretStr | None = None
    log_level: str = "INFO"
    jsearch_api_key: SecretStr = SecretStr("")
    jsearch_max_results_per_query: int = 10
    matching_max_concurrency: int = 2
    matching_jobs_per_batch: int = 20
    remotive_enabled: bool = True
    remoteok_enabled: bool = True
    arbeitnow_enabled: bool = True
    greenhouse_board_enabled: bool = True
    remotive_max_results: int = 50
    arbeitnow_max_pages_per_sync: int = 2
    remoteok_user_agent: str = (
        "job-application-agent/1.0 (+https://github.com/panibrat/job-application-agent)"
    )

    cors_allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment == "production":
            if self.jwt_secret.get_secret_value() == "dev-secret":
                raise ValueError("jwt_secret must be set in production")
            if self.cron_shared_secret.get_secret_value() == "dev-cron-secret":
                raise ValueError("cron_shared_secret must be set in production")
            if not self.google_oauth_client_id or not self.google_oauth_client_secret:
                raise ValueError("Google OAuth credentials required in production")
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
