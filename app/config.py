"""Application configuration."""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "Support Engine"
    app_env: str = "development"
    debug: bool = True
    secret_key: str = "change-me-in-production"

    # Database
    database_url: str = "postgresql+asyncpg://user:password@localhost:5432/support"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    redis_host: str = "localhost"
    redis_port: int = 6379

    # Rate limiting
    rate_limit_per_minute: int = 30
    rate_limit_per_hour: int = 200
    rate_limit_per_day: int = 2000

    # LLM
    llm_provider: str = "anthropic"  # openai, anthropic, local
    llm_model: str = "claude-3-5-sonnet-20241022"
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None

    # Embeddings
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # Guardrails
    guardrails_enabled: bool = True
    max_tokens: int = 1024
    temperature: float = 0.7

    # CORS
    allowed_origins: str = "http://localhost:3000"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


settings = get_settings()