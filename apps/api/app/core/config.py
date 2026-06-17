"""Configuração tipada da aplicação.

Carrega variáveis de ambiente via pydantic-settings e falha rápido no startup
se alguma obrigatória estiver ausente ou mal formatada.

NÃO logar nem expor `OMIE_ENCRYPTION_KEY`, `JWT_SECRET` ou `ANTHROPIC_API_KEY`
em lugar algum. Ver CLAUDE.md §3.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Ambientes suportados."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Níveis de log."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


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

    # ---------- Segurança ----------
    OMIE_ENCRYPTION_KEY: SecretStr = Field(
        ..., description="Chave AES-256 em hex (64 chars). Gere com `openssl rand -hex 32`."
    )
    JWT_SECRET: SecretStr = Field(
        ..., description="Segredo HMAC do JWT. Gere com `openssl rand -hex 32`."
    )
    # Chave HMAC do blind index de busca em `description` (S16). NÃO reusar
    # OMIE_ENCRYPTION_KEY — separação de domínios: comprometer um não dá
    # vantagem no outro. Mesmo formato (32 bytes hex) por ergonomia operacional.
    SEARCH_BLIND_INDEX_KEY: SecretStr = Field(
        ...,
        description=(
            "Chave HMAC-SHA256 do blind index de search (64 chars hex). "
            "Gere com `openssl rand -hex 32`. NÃO reusar OMIE_ENCRYPTION_KEY."
        ),
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
    # CSV de hosts aceitos no header `Host` (TrustedHostMiddleware, P0-005).
    # Em prod, settar com o(s) FQDN(s) público(s) — protege contra host header
    # injection (cache poisoning, password reset URL spoofing). Default inclui
    # `test` (httpx AsyncClient default) e `testserver` (Starlette TestClient).
    ALLOWED_HOSTS: str = Field(
        default="localhost,127.0.0.1,test,testserver",
        description="CSV de hostnames aceitos no header Host. Em prod, settar com FQDN público.",
    )

    # ---------- Integrações ----------
    ANTHROPIC_API_KEY: SecretStr = Field(default=SecretStr(""))
    ANTHROPIC_MODEL_DEFAULT: str = "claude-sonnet-4-5"
    ANTHROPIC_MODEL_FALLBACK: str = "claude-opus-4-6"
    # Timeout total para o parsing IA (S9). Subido de 60s → 150s: extratos reais
    # grandes (ex.: 143 transações) levam ~75s na extração via Claude e estouravam
    # o default antigo, devolvendo 504. Continua parametrizável para testes — em
    # pytest cai-se para 1s com `monkeypatch` sem mexer no código.
    # ACOPLAMENTO: o proxy do BFF (Next `experimental.proxyTimeout` em
    # apps/web/next.config.mjs) precisa ficar ACIMA deste valor (160s). Se o BFF
    # cortar antes, o usuário vê um 500 genérico mesmo com o backend respondendo.
    ANTHROPIC_TIMEOUT_SECONDS: float = 150.0

    # MOCK exclusivo de demo/gravação: quando True, `ParseService` retorna um
    # payload fixo (extrato fictício da Padaria Pão Quente) sem chamar a
    # Anthropic. NÃO usar em CI/staging/prod — a flag existe só pra desbloquear
    # demos quando a conta da Anthropic está sem crédito.
    MOCK_PARSE: bool = False
    # Atraso simulado (s) do parsing mockado, pra que a UI de "Processando
    # arquivo…" seja realista no vídeo. Ignorado quando MOCK_PARSE=False.
    MOCK_PARSE_DELAY_SECONDS: float = 5.0

    OMIE_BASE_URL: str = "https://app.omie.com.br/api/v1"
    OMIE_TIMEOUT_SECONDS: int = 15
    # Timeout específico do "Testar conexão" (S6 §3.3): mais agressivo que o
    # default — usuário está na UI esperando feedback rápido.
    OMIE_TEST_CONNECTION_TIMEOUT_SECONDS: int = 10
    # Timeout específico de `ListarExtrato` (auditoria A-3): o endpoint não
    # tem paginação documentada — clientes com muitos lançamentos no período
    # podem devolver respostas grandes. 15s default é apertado pra
    # transferência + parse. O processamento em background pode esperar mais
    # (não há usuário na frente da request).
    OMIE_TIMEOUT_EXTRATO_SECONDS: int = 60

    # ---------- Limites ----------
    MAX_UPLOAD_SIZE_MB: int = 20
    PARSE_TIMEOUT_SECONDS: int = 60
    # Tempo máximo do processamento assíncrono de uma conciliação (busca Omie +
    # matching + qualificação), rodando via FastAPI BackgroundTasks. Substitui o
    # antigo `WorkerSettings.job_timeout=900` do ARQ: sem um teto, uma task em
    # background poderia segurar uma conexão do pool indefinidamente. Ao estourar,
    # `run_reconciliation` marca a sessão como `error` (mesma mensagem de timeout).
    # O cron `mark_stuck_sessions_as_error` (25min) segue como rede de segurança.
    RECONCILIATION_TIMEOUT_SECONDS: float = 900.0

    # ---------- Qualificação (S19) ----------
    # Ativa a etapa de qualificação semântica/histórica/outlier no pipeline
    # (BACK 12.1). Default `True`. Setar `False` para desligar rapidamente
    # se a Anthropic ficar fora ou se houver picos de custo — o matching
    # base segue funcionando sem essa camada.
    QUALIFICATION_ENABLED: bool = Field(
        default=True,
        description="Ativa análise de qualificação no pipeline de conciliação (S19).",
    )

    # ---------- Observabilidade ----------
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    # ---------- Validators ----------
    @field_validator("OMIE_ENCRYPTION_KEY", "JWT_SECRET", "SEARCH_BLIND_INDEX_KEY")
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

    @model_validator(mode="after")
    def _enforce_secure_cookie_in_prod(self) -> Self:
        """Em staging/production, exige COOKIE_SECURE=True (P0-001).

        Default `False` existe pra dev local em HTTP. Quando o deploy esquece
        a env var em staging/prod, JWT vaza em qualquer hop não-TLS. Falha
        rápido no startup em vez de servir cookies inseguros.
        """
        if (
            self.ENVIRONMENT in (Environment.STAGING, Environment.PRODUCTION)
            and not self.COOKIE_SECURE
        ):
            raise ValueError(
                f"COOKIE_SECURE deve ser True em ENVIRONMENT={self.ENVIRONMENT.value}. "
                "Setar `COOKIE_SECURE=true` no .env do deploy."
            )
        return self

    @property
    def allowed_origins_list(self) -> list[str]:
        """Retorna ALLOWED_ORIGINS como lista, após split por vírgula."""
        return [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]

    @property
    def allowed_hosts_list(self) -> list[str]:
        """Retorna ALLOWED_HOSTS como lista, após split por vírgula."""
        return [host.strip() for host in self.ALLOWED_HOSTS.split(",") if host.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == Environment.PRODUCTION

    @property
    def is_staging(self) -> bool:
        return self.ENVIRONMENT == Environment.STAGING

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retorna a instância singleton de Settings.

    Usa lru_cache para garantir que o .env é lido apenas uma vez por processo.
    Em testes, limpar o cache com `get_settings.cache_clear()` ao sobrescrever vars.
    """
    return Settings()
