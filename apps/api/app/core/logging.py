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
import re
import sys
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

import structlog
from structlog.contextvars import merge_contextvars
from structlog.types import EventDict, Processor

if TYPE_CHECKING:
    from app.core.config import Settings

# ----------------------------------------------------------------------
# Redação de segredos
# ----------------------------------------------------------------------

# Termos sensíveis (case-insensitive). Uma key é mascarada quando um destes
# termos aparece nela como SEGMENTO INTEIRO — delimitado por `_` ou pelas pontas
# da key. Assim `access_token` e `omie_app_key_encrypted` são pegos, e
# `input_tokens` (plural, contagem) NÃO é.
#
# Por que segmento e não substring: `"token" in "input_tokens"` é verdadeiro, e
# a versão anterior mascarava toda contagem de token do sistema — inclusive
# `cached_input_tokens`, que é a instrumentação do guardrail de custo da
# qualificação. Contagem de token é métrica, não segredo; medida apagada é
# guardrail que ninguém consegue auditar em produção.
SENSITIVE_KEY_TERMS: frozenset[str] = frozenset(
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
        "blind_index",
    }
)

REDACTED_VALUE = "[REDACTED]"

#: Fronteira entre palavras em camelCase/PascalCase — `accessToken` precisa
#: normalizar para `access_token`, senão viraria um segmento único e escaparia.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

#: Separadores que valem como fronteira de segmento: hífen e espaço (cabeçalhos
#: HTTP como `X-API-KEY` e `set-cookie` chegam assim).
_SEPARATORS = re.compile(r"[-\s]+")


def _normalize_key(key: str) -> str:
    """Reduz a key a snake_case para o match por segmento.

    `X-API-KEY`, `api key`, `apiKey` e `api_key` convergem todos para `api_key`.
    """
    return _SEPARATORS.sub("_", _CAMEL_BOUNDARY.sub("_", key)).lower()


def _is_sensitive_key(key: str) -> bool:
    """True quando a key contém um termo sensível como segmento inteiro.

    O truque do padding: envolver key e termo em `_` transforma "segmento
    inteiro" em substring simples. `_token_` está em `_access_token_` (segredo)
    e não está em `_input_tokens_` (métrica).
    """
    padded = f"_{_normalize_key(key)}_"
    return any(f"_{term}_" in padded for term in SENSITIVE_KEY_TERMS)


#: Teto da varredura recursiva. Evento de log legítimo é raso — chegar aqui é
#: payload patológico (ou referência circular, que sem o teto travaria o
#: processo). **Fail-closed:** o que estiver além do teto vira [REDACTED]
#: inteiro — perder debug info é recuperável, vazar segredo não (86e2rtxcm).
_MAX_REDACTION_DEPTH = 8


def _redact_value(value: Any, depth: int) -> Any:
    """Varre dict/list/tuple aninhados redigindo valores de chaves sensíveis.

    Devolve **cópia** das estruturas que atravessa — nunca muta o objeto do
    chamador: quem loga `errors=exc.errors()` continua dono da lista original
    intacta (mutar in-place corromperia dado vivo da aplicação). Escalares
    voltam por referência, sem custo.
    """
    if isinstance(value, Mapping):
        if depth >= _MAX_REDACTION_DEPTH:
            return REDACTED_VALUE
        return {
            k: (
                REDACTED_VALUE
                if isinstance(k, str) and _is_sensitive_key(k)
                else _redact_value(v, depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        if depth >= _MAX_REDACTION_DEPTH:
            return REDACTED_VALUE
        items = [_redact_value(v, depth + 1) for v in value]
        return tuple(items) if isinstance(value, tuple) else items
    return value


def _redact_sensitive(
    _logger: Any,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Processor structlog: substitui valores de chaves sensíveis por [REDACTED].

    A decisão continua por NOME de chave (`_is_sensitive_key`), mas a varredura
    é **recursiva** (86e2rtxcm): antes ela olhava só as chaves de topo, e
    qualquer segredo aninhado — `errors[0]["input"]` do Pydantic, um dict de
    credenciais dentro de um evento — atravessava ileso. O topo é mutado
    in-place (convenção structlog: o event_dict é do pipeline); os níveis
    internos são cópias (ver `_redact_value`).

    Idempotente — pode rodar múltiplas vezes sem efeito colateral.
    """
    for key, value in event_dict.items():
        if _is_sensitive_key(key):
            event_dict[key] = REDACTED_VALUE
        else:
            event_dict[key] = _redact_value(value, 1)
    return event_dict


# ----------------------------------------------------------------------
# Sanitização de erros de validação (86e2rtxcm)
# ----------------------------------------------------------------------

#: Chaves de `exc.errors()` (Pydantic v2) que são DIAGNÓSTICO, não dado do
#: cliente: `loc` diz qual campo falhou, `msg` e `type` dizem por quê. As
#: demais — `input` (o valor rejeitado, verbatim) e `ctx` (pode ecoá-lo) —
#: carregam payload e NUNCA podem chegar ao log (§3.3): o valor rejeitado de
#: um body inválido é senha errada de propósito, anotação acima do limite
#: (campo CRIPTOGRAFADO no banco, §4.1) ou credencial Omie malformada.
_VALIDATION_ERROR_SAFE_KEYS: tuple[str, ...] = ("type", "loc", "msg")


def sanitize_validation_errors(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Reduz `RequestValidationError.errors()` ao que o suporte usa.

    Allow-list, não deny-list: chave nova que o Pydantic inventar amanhã nasce
    FORA do log — o oposto de remover `input`/`ctx` e torcer para não surgir
    uma terceira via de eco do payload.
    """
    return [
        {key: error[key] for key in _VALIDATION_ERROR_SAFE_KEYS if key in error} for error in errors
    ]


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
