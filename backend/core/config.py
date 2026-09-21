# backend/core/config.py

from pydantic_settings import BaseSettings, SettingsConfigDict

# The built-in development secret. It is public (it is in this file), so
# api.bootstrap.build_app() refuses to start in production while it is
# still the configured value.
DEFAULT_JWT_SECRET = "dev-only-secret-change-before-any-real-deployment"


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

    # Auth (Phase 3+). The secret must be at least 32 characters.
    jwt_secret_key: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expiry_minutes: int = 60
    # Development only: the password every demo user gets. Never applied
    # when app_env is "production".
    demo_user_password: str = "Demo-Passw0rd-Change-Me"

    # Ollama / AI layer (Phase 4+)
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"

    # Governance thresholds - mirrors the constants currently
    # hardcoded in fsm/transitions.py. Not wired in yet (see note
    # below); this just gives them one obvious future home.
    escalation_timeout_seconds: int = 1800
    hard_denial_risk_threshold: int = 85
    escalation_risk_threshold: int = 40

    # Logging
    log_level: str = "INFO"

    def is_production(self) -> bool:
        return self.app_env == "production"


# Single shared instance - import this, don't instantiate Settings()
# elsewhere.
settings = Settings()
