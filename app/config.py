"""Environment-based configuration (12-factor style).

All settings come from environment variables (or a local .env file in dev).
No secrets are ever hard-coded — see .env.example for the expected variables.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Overridden per environment: SQLite for quick local runs,
    # PostgreSQL in Docker/CI/production-style deployments.
    database_url: str = "sqlite:///./pharmacy.db"

    log_level: str = "INFO"

    # Injected by the deployment (Docker label / CI tag) so /health can
    # report the exact running version — useful for rollback evidence.
    app_version: str = "dev"

    # Seed synthetic catalogue data on first start (idempotent).
    seed_on_start: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
