# backend/core/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central application configuration, loaded from environment
    variables (and .env in development). Every other module should
    import `settings` from here rather than reading os.environ
    directly - this keeps all configuration discoverable in one place.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # General
    app_env: str = "development"
    debug: bool = True

    # Database (Phase 3+)
    database_url: str = "sqlite:///./governai_dev.db"

    # Redis (Phase 3+)
    redis_url: str = "redis://localhost:6379/0"

    # Auth (Phase 3+)
    jwt_secret_key: str = "dev-only-secret-change-before-any-real-deployment"
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60

    # Ollama / AI layer (Phase 4+)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # Governance thresholds - mirrors the constants currently
    # hardcoded in fsm/transitions.py. Not wired in yet (see note
    # below); this just gives them one obvious future home.
    escalation_timeout_seconds: int = 1800
    hard_denial_risk_threshold: int = 85
    escalation_risk_threshold: int = 40

    # backend/core/config.py — add this field to the Settings class

    # Logging
    log_level: str = "INFO"

    def is_production(self) -> bool:
        return self.app_env == "production"


# Single shared instance - import this, don't instantiate Settings()
# elsewhere.
settings = Settings()