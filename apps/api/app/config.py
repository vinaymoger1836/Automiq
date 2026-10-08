from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = Field(min_length=1)
    redis_url: str = Field(min_length=1)
    temporal_address: str = Field(min_length=1)
    temporal_namespace: str = Field(min_length=1)
    temporal_task_queue: str = Field(min_length=1)
    web_origin: str = Field(min_length=1)
    session_secret: SecretStr = Field(min_length=16)
    encryption_key_ref: str = Field(min_length=1)
    otel_exporter_otlp_endpoint: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
