from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn
    anthropic_api_key: SecretStr
    adzuna_app_id: str = ""
    adzuna_api_key: SecretStr = SecretStr("")
    claude_model: str = "claude-sonnet-4-6"
    claude_matching_model: str = "claude-haiku-4-5-20251001"
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
    search_auto_pause_days: int = 7
    sentry_dsn: SecretStr | None = None
    langchain_tracing_v2: bool = False
    langchain_api_key: SecretStr | None = None
    langchain_project: str = "job-application-agent"
    job_stale_after_days: int = 14
    adzuna_max_queries_per_sync: int = 3
    adzuna_max_pages_per_sync: int = 1
    adzuna_cache_ttl_hours: int = 24
    tavily_api_key: SecretStr | None = None
    log_level: str = "INFO"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
