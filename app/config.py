"""Configuration, read from environment variables (or a local .env file).

No address or password is written in the code. Passwords are `SecretStr`:
they never appear in logs or error messages by accident.
"""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "incidents"
    postgres_user: str = "incidents"
    postgres_password: SecretStr  # required: no default on purpose

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: SecretStr | None = None

    # Maximum submissions per IP address in a one-minute window.
    rate_limit_per_minute: int = Field(default=5, ge=1)
    # Number of reports kept in the Redis "recent" list.
    recent_limit: int = Field(default=10, ge=1, le=100)


@lru_cache
def get_settings() -> Settings:
    return Settings()
