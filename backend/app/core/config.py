from functools import lru_cache
from pathlib import Path
from re import fullmatch
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = BACKEND_ROOT / "data"
DEFAULT_SECRET = "development-only-change-this-secret"


class Settings(BaseSettings):
    app_name: str = "MDE-NG API"
    app_env: Literal["dev", "prod"] = "dev"
    database_url: str = ""
    intake_provider: Literal["local", "azure-openai"] = "local"
    azure_openai_endpoint: str = ""
    azure_openai_deployment: str = Field(
        default="",
        validation_alias=AliasChoices("AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_CHAT_DEPLOYMENT"),
    )
    azure_openai_api_key: SecretStr | None = None
    node_binary: str = ""
    jwt_secret: str = DEFAULT_SECRET
    jwt_issuer: str = ""
    jwt_audience: str = "mde-ng"
    auth_database_path: Path = DATA_ROOT / "mde-ng-accounts.sqlite3"
    session_minutes: int = Field(default=480, ge=1)
    frontend_origin: str = "http://localhost:5173"
    auth_required: bool | None = None

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore", populate_by_name=True,
    )

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_mode(cls, value: str) -> str:
        return {"development": "dev", "production": "prod"}.get(value, value)

    @model_validator(mode="after")
    def configure_storage(self):
        if self.intake_provider == "azure-openai":
            endpoint = urlsplit(self.azure_openai_endpoint)
            allowed = (".openai.azure.com", ".cognitiveservices.azure.com", ".services.ai.azure.com")
            base_path = endpoint.path.rstrip("/")
            project_path = (
                endpoint.hostname and endpoint.hostname.endswith(".services.ai.azure.com")
                and fullmatch(r"/api/projects/[A-Za-z0-9_-]+/openai/v1", base_path)
            )
            if (not endpoint.hostname or not endpoint.hostname.endswith(allowed)
                    or endpoint.scheme != "https" or endpoint.username or endpoint.password
                    or endpoint.port not in (None, 443) or (base_path not in ("", "/openai/v1") and not project_path)
                    or endpoint.query or endpoint.fragment):
                raise ValueError("Set AZURE_OPENAI_ENDPOINT to an approved Azure OpenAI HTTPS resource URL.")
            if not self.azure_openai_deployment.strip() or not self.azure_openai_api_key or not self.azure_openai_api_key.get_secret_value().strip():
                raise ValueError("Azure OpenAI Intake requires a deployment and API key.")
            if self.app_env != "dev":
                raise ValueError("The Azure OpenAI Intake adapter is enabled for synthetic development only.")
        if self.app_env == "dev":
            self.database_url = self.database_url or f"sqlite:///{DATA_ROOT / 'mde-ng-dev.sqlite3'}"
            if self.database_url.split(":", 1)[0].split("+", 1)[0] != "sqlite":
                raise ValueError("Development mode requires a SQLite DATABASE_URL.")
            self.auth_required = True
        else:
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("Prod mode requires an explicit PostgreSQL DATABASE_URL.")
            if self.auth_required is False:
                raise ValueError("Prod mode requires authentication.")
            if not self.jwt_issuer or self.jwt_secret == DEFAULT_SECRET or len(self.jwt_secret) < 32:
                raise ValueError("Prod mode requires JWT_ISSUER and a dedicated JWT_SECRET.")
            self.auth_required = True
            self.database_url = self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
