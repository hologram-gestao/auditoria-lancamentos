"""Teto de tokens de saída por modelo + validação na subida (BACK 02.1).

Motivação: `ADL_PARSE_MAX_OUTPUT_TOKENS` é configurável. Se alguém configurar
um valor ACIMA do que o modelo em uso aceita, a Anthropic devolveria um HTTP
400 em runtime — silencioso em produção, no meio de uma conciliação. Em vez
disso, validamos na subida (fail-fast no `lifespan`): configuração inválida →
o serviço **não inicia**.

Fonte do cap por modelo: a **Models API** da Anthropic expõe `max_tokens` (o
teto de saída) em `client.models.retrieve(<model>).max_tokens`. Mantemos uma
tabela estática dos modelos que o produto usa (sincronizada com esse
`max_tokens` / doc oficial) para que a validação seja determinística e
offline — CI e `pnpm dev` sem `ANTHROPIC_API_KEY` continuam subindo. Para um
modelo fora da tabela, tentamos a Models API ao vivo (best-effort) como
fallback; se ainda assim não der para determinar o cap, logamos e NÃO
bloqueamos a subida (não travar o serviço por indisponibilidade da Anthropic).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.config import Settings

log = get_logger(__name__)

# Cap de SAÍDA (`max_tokens` da Models API) dos modelos que o produto usa.
# `claude-sonnet-4-5` (ANTHROPIC_MODEL_DEFAULT) → 64.000 (doc oficial do
# modelo). Atualizar esta tabela ao trocar de modelo — é a mesma informação
# que a Models API expõe em `.max_tokens`.
KNOWN_MODEL_OUTPUT_CAPS: dict[str, int] = {
    "claude-sonnet-4-5": 64_000,
}


class ParseOutputConfigError(RuntimeError):
    """Configuração de `ADL_PARSE_MAX_OUTPUT_TOKENS` inválida na subida.

    Herda de `RuntimeError` (não de `AppError`) de propósito: é falha de
    startup, não erro HTTP. Levantada dentro do `lifespan` → o app não sobe.
    """


async def _fetch_output_cap_from_api(model: str, settings: Settings) -> int | None:
    """Consulta a Models API para o `max_tokens` (cap de saída) do modelo.

    Best-effort: exige `ANTHROPIC_API_KEY` e rede. Qualquer falha (sem chave,
    timeout, modelo desconhecido, campo ausente) devolve `None` — o caller cai
    no fallback. Import local para não arrastar o SDK quando a tabela estática
    já resolve.
    """
    api_key = settings.ANTHROPIC_API_KEY.get_secret_value()
    if not api_key:
        return None
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key, timeout=settings.ANTHROPIC_TIMEOUT_SECONDS)
        info = await client.models.retrieve(model)
        cap = getattr(info, "max_tokens", None)
        return int(cap) if cap is not None else None
    except Exception as exc:  # best-effort: nunca bloquear a subida por isso
        log.warning(
            "model_output_cap_lookup_failed",
            model=model,
            error=type(exc).__name__,
        )
        return None


async def resolve_model_output_cap(model: str, settings: Settings) -> int | None:
    """Resolve o cap de saída do modelo: tabela estática → Models API ao vivo.

    Devolve `None` só quando o modelo não está na tabela E a Models API não
    respondeu — nesse caso o caller não bloqueia a subida.
    """
    static_cap = KNOWN_MODEL_OUTPUT_CAPS.get(model)
    if static_cap is not None:
        return static_cap
    return await _fetch_output_cap_from_api(model, settings)


async def validate_parse_output_config(settings: Settings) -> None:
    """Fail-fast na subida: teto configurado não pode exceder o cap do modelo.

    Chamada no `lifespan`. Se `ADL_PARSE_MAX_OUTPUT_TOKENS` > cap de saída do
    modelo em uso, levanta `ParseOutputConfigError` e o serviço NÃO inicia.
    Se o cap não puder ser determinado (modelo fora da tabela + Models API
    indisponível), loga um aviso e segue — não travar o serviço por
    indisponibilidade da Anthropic.
    """
    configured = settings.ADL_PARSE_MAX_OUTPUT_TOKENS
    model = settings.ANTHROPIC_MODEL_DEFAULT
    cap = await resolve_model_output_cap(model, settings)

    if cap is None:
        log.warning(
            "parse_output_cap_unverified",
            model=model,
            configured=configured,
            reason="cap desconhecido (modelo fora da tabela e Models API indisponível)",
        )
        return

    if configured > cap:
        raise ParseOutputConfigError(
            f"ADL_PARSE_MAX_OUTPUT_TOKENS={configured} excede o teto de saída do "
            f"modelo {model} (max_tokens={cap}). Reduza o valor no ambiente — o "
            "serviço não inicia com um teto que a Anthropic recusaria em runtime."
        )

    log.info(
        "parse_output_cap_ok",
        model=model,
        configured=configured,
        model_cap=cap,
    )
