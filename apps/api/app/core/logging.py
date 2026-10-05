"""Logging estruturado via structlog com redação obrigatória de segredos.

Princípios (CLAUDE.md §3):
    - Logs em JSON estruturado em produção (consumível por Loki/Grafana).
    - Console colorido em desenvolvimento.
    - **Toda chave sensível é mascarada como `[REDACTED]`** antes do output.
    - **Todo trecho com FORMA de segredo também** (JWT, chave da Anthropic, URL de
      webhook, `Authorization`, chave hex de 64), sob qualquer chave (86e3anx7y).
    - Correlation ID propagado via `contextvars` (setado pelo middleware HTTP).

Uso:
    >>> from app.core.logging import setup_logging, get_logger
    >>> setup_logging(get_settings())
    >>> log = get_logger(__name__)
    >>> log.info("user_logged_in", user_id="abc-123", ip="10.0.0.1")
"""

from __future__ import annotations

import base64
import binascii
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
        # URL de webhook é credencial (quem tem a URL posta no canal). `url` pega
        # `webhook_url`, `LEADS_SLACK_WEBHOOK_URL` e `database_url`; `webhook` pega
        # a chave curta. Nenhum log da aplicação usa `url` como dado neutro (86e3fr9ut).
        "webhook",
        "url",
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


# ----------------------------------------------------------------------
# Redação por FORMA do valor (86e3anx7y)
# ----------------------------------------------------------------------
#
# A decisão por nome de chave só pega segredo sob chave que se declara. Segredo
# sob chave inocente (`error=str(exc)` com a URL do webhook dentro, um JWT colado
# num `detail`) passava direto. Esta camada varre TODA string do evento e troca
# por `[REDACTED]` só o TRECHO que tem forma de segredo, para o resto da mensagem
# continuar útil. Todos os padrões são ancorados em prefixo literal ou em
# fronteira de classe (lookbehind), sem quantificador aninhado: a varredura é
# linear no tamanho da string.

#: Fronteira de token base64url: o padrão só começa onde a sequência começa, então
#: uma corrida longa de caracteres válidos tem UM ponto de partida, não um por
#: posição (sem isso, uma string de 100 KB de base64 seria varrida em O(n²)).
_B64URL_START = r"(?<![A-Za-z0-9_-])"

#: JWT (RFC 7519, serialização compacta do JWS): três segmentos base64url
#: separados por ponto. O cabeçalho é JSON que começa com `{"`, que em base64url
#: é sempre `eyJ`. Os três segmentos são exigidos não vazios: um JWT truncado
#: para log (`eyJhbGciOi...`) não é credencial utilizável e fica legível.
JWT_PATTERN = rf"{_B64URL_START}eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"

#: Chave da API da Anthropic (`ANTHROPIC_API_KEY`): prefixo literal `sk-ant-`,
#: seguido do corpo da chave (`sk-ant-api03-…`, `sk-ant-admin01-…`).
ANTHROPIC_KEY_PATTERN = r"sk-ant-[A-Za-z0-9_-]+"

#: URL de webhook: quem tem a URL posta no canal. Os destinos do sistema são o
#: incoming webhook do Slack (`ALERT_WEBHOOK_URL*` em `core/alerting.py`, cujo
#: payload `text` serve Slack e Discord, e `LEADS_SLACK_WEBHOOK_URL` em
#: `modules/leads/notifier.py`). Slack: `hooks.slack.com/services|workflows|
#: triggers/…`; Discord: `discord.com/api/webhooks/…` (com os subdomínios
#: `ptb.`/`canary.` e o domínio antigo `discordapp.com`). O trecho vai até o
#: primeiro espaço ou aspas. Os subdomínios são LITERAIS de propósito: um
#: `[a-z]+\.` opcional consumiria cada corrida de letras inteira e voltaria
#: atrás em toda posição, O(n²) (medido: 74 s para 256 KB de `x`).
WEBHOOK_URL_PATTERN = (
    r"(?:https?://)?(?:hooks\.slack\.com/"
    r"|(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/webhooks/)[^\s\"'<>]*"
)

#: Esquema `Authorization: Bearer <token>` (RFC 6750). O token tem pelo menos 8
#: caracteres do alfabeto `b64token` para "bearer" em texto corrido não casar.
BEARER_PATTERN = r"(?i:\bBearer)\s+[A-Za-z0-9._~+/-]{8,}=*"

#: Esquema `Authorization: Basic <base64(usuario:senha)>` (RFC 7617). "Basic"
#: seguido de palavra é comum em texto ("Basic setup"), então o casamento só é
#: mascarado quando o base64 decodifica para algo com `:` (ver `_mask_match`).
BASIC_PATTERN = r"(?i:\bBasic)\s+(?P<basic>[A-Za-z0-9+/]{4,}={0,2})"

#: Chave hex de 64 caracteres (256 bits): é o formato de `OMIE_ENCRYPTION_KEY`
#: (de onde a KEK local deriva), `JWT_SECRET` e `SEARCH_BLIND_INDEX_KEY`
#: (`_validate_hex_key` em `core/config.py`). Exatamente 64: nem parte de uma
#: corrida hex maior, nem 32 (UUID sem hífen).
HEX64_KEY_PATTERN = r"(?<![0-9A-Fa-f])(?P<hex64>[0-9A-Fa-f]{64})(?![0-9A-Fa-f])"

#: SHA-256 também é 64 hex, e hash de arquivo é IDENTIFICADOR, não segredo: o
#: `file_hash` da conciliação e da origem por arquivo é a chave da deduplicação
#: e o `sha256` da geração do arquivo contábil é o que o download confere. Sob
#: chave cujo nome normalizado contém um destes termos, o padrão de 64 hex NÃO
#: mascara (os outros formatos continuam valendo). É a lição do `input_tokens`
#: (comentário de `SENSITIVE_KEY_TERMS`): mascarar métrica ou identificador
#: legítimo apaga o que o suporte precisa ler.
HEX64_EXEMPT_KEY_TERMS: tuple[str, ...] = ("hash", "sha", "digest")

_SECRET_VALUE_RE = re.compile(
    "|".join(
        (
            JWT_PATTERN,
            ANTHROPIC_KEY_PATTERN,
            WEBHOOK_URL_PATTERN,
            BEARER_PATTERN,
            BASIC_PATTERN,
            HEX64_KEY_PATTERN,
        )
    )
)

#: Teto da varredura por string. Evento legítimo, traceback incluído, fica bem
#: abaixo disso; acima, o excedente vira `[REDACTED]` sem ser lido. Fail-closed
#: pelo mesmo motivo do `_MAX_REDACTION_DEPTH`: perder a cauda de um log
#: patológico é recuperável, deixar passar a cauda sem varrer não é.
MAX_VALUE_SCAN_CHARS = 256 * 1024


def _hex64_exempt(key: str | None) -> bool:
    """True quando a chave nomeia um hash (o 64 hex dela é identificador)."""
    if key is None:
        return False
    normalized = _normalize_key(key)
    return any(term in normalized for term in HEX64_EXEMPT_KEY_TERMS)


def _is_basic_credential(token: str) -> bool:
    """`Basic` só é credencial quando o base64 decodifica para `usuario:senha`."""
    try:
        decoded = base64.b64decode(token + "=" * (-len(token) % 4), validate=True)
    except (binascii.Error, ValueError):
        return False
    return b":" in decoded


def _redact_string(text: str, key: str | None) -> str:
    """Troca por `[REDACTED]` cada trecho de `text` com forma de segredo.

    `key` é a chave mais próxima que contém o valor (para a exceção do hash).
    """
    tail = ""
    if len(text) > MAX_VALUE_SCAN_CHARS:
        text, tail = text[:MAX_VALUE_SCAN_CHARS], REDACTED_VALUE
    hex64_exempt = _hex64_exempt(key)

    def _mask_match(match: re.Match[str]) -> str:
        if match.group("hex64") is not None and hex64_exempt:
            return match.group(0)
        basic = match.group("basic")
        if basic is not None and not _is_basic_credential(basic):
            # "Basic <palavra>" não é credencial, mas a palavra consumida pode
            # conter outro formato (uma chave hex): ela é varrida de novo.
            prefix = match.group(0)[: match.start("basic") - match.start()]
            return prefix + _SECRET_VALUE_RE.sub(_mask_match, basic)
        return REDACTED_VALUE

    return _SECRET_VALUE_RE.sub(_mask_match, text) + tail


def _redact_value(value: Any, depth: int, key: str | None = None) -> Any:
    """Varre dict/list/tuple aninhados redigindo segredos por chave e por valor.

    Chave sensível vira `[REDACTED]` inteira; string sob qualquer outra chave
    passa por `_redact_string`, que mascara só o trecho com forma de segredo.
    `key` é a chave mais próxima acima do valor: lista sob `file_hashes` herda a
    exceção do hash.

    Devolve **cópia** das estruturas que atravessa — nunca muta o objeto do
    chamador: quem loga `errors=exc.errors()` continua dono da lista original
    intacta (mutar in-place corromperia dado vivo da aplicação). Escalares que
    não são string voltam por referência, sem custo.
    """
    if isinstance(value, str):
        return _redact_string(value, key)
    if isinstance(value, Mapping):
        if depth >= _MAX_REDACTION_DEPTH:
            return REDACTED_VALUE
        return {
            k: (
                REDACTED_VALUE
                if isinstance(k, str) and _is_sensitive_key(k)
                else _redact_value(v, depth + 1, k if isinstance(k, str) else key)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        if depth >= _MAX_REDACTION_DEPTH:
            return REDACTED_VALUE
        items = [_redact_value(v, depth + 1, key) for v in value]
        return tuple(items) if isinstance(value, tuple) else items
    return value


def _redact_sensitive(
    _logger: Any,
    _method_name: str,
    event_dict: EventDict,
) -> EventDict:
    """Processor structlog: mascara segredos por NOME de chave e por FORMA de valor.

    Duas camadas. Por chave (`_is_sensitive_key`): o valor inteiro vira
    `[REDACTED]`. Por valor (`_redact_string`, 86e3anx7y): toda string sob as
    demais chaves, inclusive o próprio `event` e o `exception` renderizado, tem
    o trecho com forma de segredo trocado por `[REDACTED]`. A varredura é
    **recursiva** (86e2rtxcm): antes ela olhava só as chaves de topo, e
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
            event_dict[key] = _redact_value(value, 1, key)
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


def build_processors(*, is_prod: bool) -> list[Processor]:
    """Cadeia de processors do structlog, do contexto ao renderer.

    O redator (`_redact_sensitive`) roda DEPOIS de `StackInfoRenderer` e de
    `format_exc_info` (86e3anx7y). Antes ele rodava antes, e o texto da exceção
    nascia depois da varredura: um `httpx.ConnectError` do webhook trazia a URL
    inteira na mensagem e ia para o log sem ser lido. Nessa posição a camada por
    chave continua igual (os dois processors só trocam `exc_info`/`stack_info`
    por `exception`/`stack`, strings que a camada por valor varre), então um
    processor só, em vez de um segundo processor só de valor, basta.
    """
    processors: list[Processor] = [
        merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_sensitive,
    ]
    if is_prod:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    return processors


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

    structlog.configure(
        processors=build_processors(is_prod=settings.is_production),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Retorna um logger structlog identificado por `name` (use `__name__` no caller)."""
    logger = structlog.get_logger(name) if name else structlog.get_logger()
    return cast(structlog.stdlib.BoundLogger, logger)
