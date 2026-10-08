from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
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
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    api_public_url: str = "http://localhost:8000"

    @model_validator(mode="after")
    def production_auth_config(self) -> "Settings":
        if self.app_env == "production" and (
            not self.oidc_issuer.startswith("https://")
            or not self.api_public_url.startswith("https://")
            or not self.web_origin.startswith("https://")
            or not self.oidc_client_id
            or not self.oidc_client_secret.get_secret_value()
            or self.session_secret.get_secret_value().startswith("local-only-")
        ):
            raise ValueError(
                "Production requires HTTPS OIDC, public origins, and a unique session secret"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
