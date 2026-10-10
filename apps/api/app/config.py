import ipaddress
import re
from functools import lru_cache
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    app_env: str = "development"
    database_url: str = Field(min_length=1)
    redis_url: str = Field(min_length=1)
    temporal_address: str = Field(min_length=1)
    temporal_namespace: str = Field(min_length=1)
    temporal_task_queue: str = Field(min_length=1)
    web_origin: str = Field(min_length=1)
    session_secret: SecretStr = Field(min_length=16)
    encryption_key_ref: str = Field(min_length=1)
    integration_encryption_key: SecretStr = SecretStr("")
    integration_keyring: SecretStr = SecretStr("")
    integration_active_key_version: int = Field(default=1, ge=1)
    integration_provider_mode: str = "real"
    integration_fake_rate_limit_first: bool = False
    llm_provider_mode: str = "fake"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = ""
    openai_input_usd_per_million: int = Field(default=0, ge=0)
    openai_output_usd_per_million: int = Field(default=0, ge=0)
    otel_exporter_otlp_endpoint: str = ""
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    api_public_url: str = "http://localhost:8000"
    http_connector_enabled: bool = False
    http_connector_origin: str = ""
    http_connector_test_private_host: str = ""
    http_connector_test_ca_file: str = ""

    @model_validator(mode="after")
    def production_auth_config(self) -> "Settings":
        if self.llm_provider_mode not in {"fake", "openai"} or (
            self.llm_provider_mode == "fake" and self.app_env not in {"development", "test"}
        ):
            raise ValueError("Fake LLM provider requires a local or test environment")
        if self.llm_provider_mode == "openai" and (
            not self.openai_api_key.get_secret_value()
            or not self.openai_model
            or not self.openai_input_usd_per_million
            or not self.openai_output_usd_per_million
        ):
            raise ValueError("OpenAI provider requires model, key and pricing configuration")
        if self.integration_provider_mode not in {"real", "fake"} or (
            self.integration_provider_mode == "fake" and self.app_env not in {"development", "test"}
        ):
            raise ValueError("Fake integration providers require a local or test environment")
        if self.integration_fake_rate_limit_first and self.integration_provider_mode != "fake":
            raise ValueError("Fake rate limiting requires a fake provider")
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
        if self.http_connector_enabled:
            try:
                origin = urlsplit(self.http_connector_origin)
                host = origin.hostname or ""
                port = origin.port if origin.port is not None else 443
            except ValueError:
                raise ValueError("HTTP connector origin is invalid") from None
            try:
                ipaddress.ip_address(host)
                is_ip = True
            except ValueError:
                is_ip = False
            if (
                origin.scheme != "https"
                or not origin.netloc
                or origin.username is not None
                or origin.password is not None
                or origin.path not in {"", "/"}
                or origin.query
                or origin.fragment
                or is_ip
                or port < 1
                or not re.fullmatch(r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*", host)
                or host.lower() in {"localhost", "localhost.localdomain"}
                or host.lower().endswith((".local", ".internal", ".localhost"))
                or (self.app_env != "test" and ("." not in host or port != 443))
            ):
                raise ValueError("HTTP connector requires a named HTTPS origin with no URL extras")
        if (
            self.http_connector_test_private_host or self.http_connector_test_ca_file
        ) and self.app_env != "test":
            raise ValueError("HTTP connector test overrides require APP_ENV=test")
        if self.http_connector_test_private_host and (
            not self.http_connector_enabled or self.http_connector_test_private_host != host
        ):
            raise ValueError("HTTP connector test host must equal the configured origin")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
