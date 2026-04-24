"""Logging estruturado via structlog com redação obrigatória de segredos.

Princípios (CLAUDE.md §3):
    - Logs em JSON estruturado em produção (consumível por Loki/Grafana).
    - Console colorido em desenvolvimento.
    - **Toda chave sensível é mascarada como `[REDACTED]`** antes do output.
    - Correlation ID propagado via `contextvars` (setado pelo middleware HTTP).

Uso:
    >>> from app.core.logging import setup_logging, get_logger
    >>> setup_logging(get_settings())
    >>> log = get_logger(__name__)
    >>> log.info("user_logged_in", user_id="abc-123", ip="10.0.0.1")
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, cast

import structlog
from structlog.contextvars import merge_contextvars
from structlog.types import EventDict, Processor

if TYPE_CHECKING:
    from app.core.config import Settings

# ----------------------------------------------------------------------
# Redação de segredos
# ----------------------------------------------------------------------

# Substring matching (case-insensitive). Qualquer key que CONTENHA uma destas
# substrings tem o valor substituído por [REDACTED]. Substring para pegar
# variações como `omie_app_key_encrypted`, `x-api-key`, `set-cookie`, etc.
SENSITIVE_SUBSTRINGS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "jwt",
        "api_key",
        "apikey",
        "app_key",
        "app_secret",
        "secret",
        "authorization",
        "cookie",
        "encryption_key",
    }
)

REDACTED_VALUE = "[REDACTED]"


def _redact_sensitive(
    _logger: Any,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Processor structlog: substitui valores de chaves sensíveis por [REDACTED].

    Idempotente — pode rodar múltiplas vezes sem efeito colateral.
    """
    for key in event_dict:
        # Normaliza separadores (hífen, espaço) para underscore — match consistente
        # entre keys como `api_key`, `api-key`, `api key`, `X-API-KEY`, etc.
        key_normalized = key.lower().replace("-", "_").replace(" ", "_")
        if any(sub in key_normalized for sub in SENSITIVE_SUBSTRINGS):
            event_dict[key] = REDACTED_VALUE
    return event_dict


# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------


def setup_logging(settings: Settings) -> None:
    """Configura logging stdlib + structlog para a aplicação inteira.

    Idempotente — pode ser chamada múltiplas vezes (útil em testes).
    """
    log_level_name = settings.LOG_LEVEL.value.upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    # Reconfigura logging stdlib para ir para stdout em formato simples
    # (uvicorn, sqlalchemy, alembic etc. usam stdlib).
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    is_prod = settings.is_production

    processors: list[Processor] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_sensitive,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if is_prod:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Retorna um logger structlog identificado por `name` (use `__name__` no caller)."""
    logger = structlog.get_logger(name) if name else structlog.get_logger()
    return cast(structlog.stdlib.BoundLogger, logger)
