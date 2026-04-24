"""Configuração tipada da aplicação.

Carrega variáveis de ambiente via pydantic-settings e falha rápido no startup
se alguma obrigatória estiver ausente ou mal formatada.

NÃO logar nem expor `OMIE_ENCRYPTION_KEY`, `JWT_SECRET` ou `ANTHROPIC_API_KEY`
em lugar algum. Ver CLAUDE.md §3.
"""

from enum import Enum
from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    """Ambientes suportados."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(str, Enum):
    """Níveis de log."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class CacheBackend(str, Enum):
    """Backends suportados pelo cache L2."""

    MEMORY = "memory"
    REDIS = "redis"


class Settings(BaseSettings):
    """Configuração global da aplicação, carregada de variáveis de ambiente."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- Ambiente ----------
    ENVIRONMENT: Environment = Environment.DEVELOPMENT
    LOG_LEVEL: LogLevel = LogLevel.INFO

    # ---------- Banco ----------
    DATABASE_URL: str = Field(
        ...,
        description="URL do Postgres com driver psycopg async "
        "(ex: postgresql+psycopg://user:pass@host:5432/db)",
    )

    # ---------- Redis ----------
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    CACHE_BACKEND: CacheBackend = CacheBackend.MEMORY

    # ---------- Segurança ----------
    OMIE_ENCRYPTION_KEY: SecretStr = Field(
        ..., description="Chave AES-256 em hex (64 chars). Gere com `openssl rand -hex 32`."
    )
    JWT_SECRET: SecretStr = Field(
        ..., description="Segredo HMAC do JWT. Gere com `openssl rand -hex 32`."
    )
    JWT_ACCESS_EXPIRE_MINUTES: int = 60
    JWT_REFRESH_EXPIRE_DAYS: int = 7
    BCRYPT_COST: int = Field(default=12, ge=10, le=15)

    # ---------- Cookies ----------
    COOKIE_SECURE: bool = False
    COOKIE_DOMAIN: str | None = None
    COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"

    # ---------- CORS ----------
    FRONTEND_URL: HttpUrl = Field(default=HttpUrl("http://localhost:3000"))
    ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3000",
        description="CSV de origens permitidas no CORS",
    )

    # ---------- Integrações ----------
    ANTHROPIC_API_KEY: SecretStr = Field(default=SecretStr(""))
    ANTHROPIC_MODEL_DEFAULT: str = "claude-sonnet-4-5"
    ANTHROPIC_MODEL_FALLBACK: str = "claude-opus-4-6"

    OMIE_BASE_URL: str = "https://app.omie.com.br/api/v1"
    OMIE_TIMEOUT_SECONDS: int = 15

    # ---------- Limites ----------
    MAX_UPLOAD_SIZE_MB: int = 20
    PARSE_TIMEOUT_SECONDS: int = 60

    # ---------- Observabilidade ----------
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # ---------- Validators ----------
    @field_validator("OMIE_ENCRYPTION_KEY", "JWT_SECRET")
    @classmethod
    def _validate_hex_key(cls, v: SecretStr) -> SecretStr:
        """Garante que a chave está em hex e tem 256 bits (64 chars)."""
        value = v.get_secret_value()
        if not value or value.startswith("REPLACE_WITH"):
            raise ValueError(
                "Chave não foi configurada. Gere com `openssl rand -hex 32` e defina no .env."
            )
        if len(value) != 64:
            raise ValueError(
                f"Chave deve ter 64 caracteres hex (256 bits). Recebido: {len(value)}."
            )
        try:
            bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError("Chave deve ser hexadecimal válido.") from exc
        return v

    @property
    def allowed_origins_list(self) -> list[str]:
        """Retorna ALLOWED_ORIGINS como lista, após split por vírgula."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == Environment.PRODUCTION

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna a instância singleton de Settings.

    Usa lru_cache para garantir que o .env é lido apenas uma vez por processo.
    Em testes, limpar o cache com `get_settings.cache_clear()` ao sobrescrever vars.
    """
    return Settings()  # type: ignore[call-arg]
