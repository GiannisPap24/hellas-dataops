"""Explicit, side-effect-free loading of application configuration."""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Validated process settings; construction does not read a dotenv file.

    Constructor arguments override environment variables. No settings instance
    is created at import time. Database and provider settings belong to future
    milestones and are intentionally absent from this contract.
    """

    model_config = SettingsConfigDict(
        env_prefix="HELLAS_",
        env_file=None,
        case_sensitive=False,
        extra="forbid",
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


def load_settings() -> Settings:
    """Read current process settings without caching or configuring logging."""
    return Settings()
