"""Camada 1 — verificação semântica via Claude (S19 BACK 12.1).

Para cada par `(descricao_extrato, fornecedor_omie, categoria_omie)`, o
modelo decide `ok | suspeita | incoerente`. Lotes de 50 pares por chamada
para amortizar prompt + tool definition no cache (CLAUDE.md §7 / PLANO §6.2).

Princípios de segurança (CLAUDE.md §3):
    - **Nada de plaintext em log.** Loga só contadores, model, tokens.
    - Os `motivos` retornados pela IA são tratados como texto sensível —
      o caller (`service.qualify_session`) cifra antes de persistir.

Erros:
    - Anthropic 5xx persistente → propaga `AnthropicTimeoutError`. Caller
      no orquestrador converte em "qualification_failed" e segue (não
      derruba a sessão).
    - Tool input mal-formado → ignora aquele item específico (log warning),
      sem derrubar o lote inteiro.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger
from app.integrations.anthropic.client import AnthropicClient
from app.modules.reconciliations.qualification.schemas import (
    QualificationPair,
    SemanticResult,
    TokenUsage,
)

log = get_logger(__name__)

# Lote de 50 pares por chamada. Empírico: payload do tool input com 50
# pares fica < 6 KB; resposta < 4 KB. Folga confortável dentro do contexto
# de 200k tokens da Sonnet 4.5. Caso aumente muito a descrição, ajustar.
SEMANTIC_BATCH_SIZE = 50

# Limite do `motivo` por par — alinha com a coluna `context_encrypted`
# do `reconciliation_anomalies` (Text, sem limite duro, mas mantemos
# o texto curto pra ficar legível na UI e barato pra criptografar).
_MAX_MOTIVO_CHARS = 200

QUALIFY_TOOL_NAME = "report_qualification"

_QUALIFY_TOOL: dict[str, Any] = {
    "name": QUALIFY_TOOL_NAME,
    "description": (
        "Reporta o veredito de qualificação para cada par "
        "(descrição_extrato, fornecedor_omie, categoria_omie). "
        "Use status='ok' quando a descrição do extrato é coerente com a "
        "classificação Omie; 'suspeita' quando há ambiguidade razoável; "
        "'incoerente' quando categoria/fornecedor claramente não "
        "correspondem ao que o extrato descreve."
    ),
    "cache_control": {"type": "ephemeral"},
    "input_schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "description": "Lista de vereditos, um por par_id recebido.",
                "items": {
                    "type": "object",
                    "properties": {
                        "pair_id": {
                            "type": "string",
                            "description": "Identificador do par recebido na lista de entrada.",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["ok", "suspeita", "incoerente"],
                            "description": (
                                "ok = coerente; suspeita = ambíguo; incoerente = "
                                "claramente divergente."
                            ),
                        },
                        "motivo": {
                            "type": "string",
                            "description": (
                                "Justificativa curta (< 200 chars) em PT-BR. "
                                "Para status='ok', pode ser uma palavra ('coerente')."
                            ),
                        },
                    },
                    "required": ["pair_id", "status", "motivo"],
                },
            },
        },
        "required": ["results"],
    },
}


_SYSTEM_PROMPT = """\
Você é um auditor de classificações contábeis brasileiras. Recebe pares \
formados por (descrição do extrato bancário, fornecedor cadastrado no Omie, \
categoria contábil do Omie, valor com sinal). Sua tarefa é decidir, par a \
par, se a classificação Omie é COERENTE com o que a descrição do extrato \
indica.

Regras de classificação:

1. **ok**: a descrição do extrato bate com o fornecedor e/ou a categoria \
do Omie de forma plausível, mesmo que abreviada ou em caixa alta. Ex: \
"PAG PIX MOINHO PRADO" + fornecedor "Moinho Prado Ltda" + categoria \
"Material de Construção" → ok.

2. **suspeita**: há uma ambiguidade razoável. A classificação Omie é \
plausível mas não é a única interpretação razoável da descrição. Ex: \
descrição "TRANSF RECEBIDA JOÃO" + fornecedor "João Silva" + categoria \
"Vendas" — pode ser venda, pode ser empréstimo pessoal — marcar suspeita.

3. **incoerente**: a classificação Omie diverge claramente do que a \
descrição indica. Ex: "TARIFA BANCÁRIA" classificada como "Pagamento de \
Cartão"; "PIX RECEBIDO" classificada como "Despesas com IOF". Marque \
**incoerente** apenas quando a divergência é evidente.

Casos especiais:

- **Dado faltante**: se `fornecedor` E `categoria` vierem nulos, marque \
**ok** com motivo "dado insuficiente para análise" — não invente conflito.
- **Descrição genérica**: "PAGAMENTO", "TARIFA", "TED RECEBIDO" sem \
detalhamento → marque **suspeita** apenas se a categoria estiver \
sintaticamente fora do esperado; caso contrário **ok**.
- **Valor**: o sinal indica natureza (negativo=saída, positivo=entrada). \
Categoria de receita em valor negativo (ou vice-versa) → **incoerente**.

Formato de resposta:

- Você DEVE chamar a tool `report_qualification` com um array `results`.
- DEVE incluir um item por `pair_id` recebido (mesmo que ok).
- `motivo` em PT-BR, até 200 caracteres, descrevendo brevemente o porquê \
da decisão. Para `ok` rotineiros, o motivo pode ser "coerente".
- NÃO escreva texto livre fora da tool call.
"""


async def analyze_pairs(
    pairs: list[QualificationPair],
    *,
    anthropic_client: AnthropicClient,
) -> tuple[list[SemanticResult], TokenUsage, int]:
    """Roda Camada 1 em lotes de até `SEMANTIC_BATCH_SIZE` pares.

    Args:
        pairs: lista completa de pares conciliados (já decriptados).
            Lista vazia → retorna ([], TokenUsage(), 0) sem chamar Anthropic.
        anthropic_client: cliente já configurado (`AnthropicClient` do
            `app.integrations.anthropic.client`). Caller decide se passa
            um real (worker) ou um fake (testes via dependency_overrides).

    Returns:
        Tupla `(results, tokens, calls)`:
            - `results`: vereditos por par. Pares omitidos pela IA OU com
              validação falhando NÃO aparecem (o caller trata como "ok").
            - `tokens`: agregado dos `usage` retornados pela Anthropic.
            - `calls`: número de chamadas (= len(pairs) // 50 + 1).

    Raises:
        AnthropicAuthError / AnthropicTimeoutError / AnthropicParseError:
            propagados quando todo o lote falha. Caller decide se descarta
            a Camada 1 ou aborta a sessão. Falha individual de parsing de
            um item NÃO levanta — só não emite resultado pra aquele item.
    """
    if not pairs:
        return [], TokenUsage(), 0

    results: list[SemanticResult] = []
    tokens = TokenUsage()
    calls = 0

    for start in range(0, len(pairs), SEMANTIC_BATCH_SIZE):
        batch = pairs[start : start + SEMANTIC_BATCH_SIZE]
        batch_results, batch_tokens = await _analyze_batch(batch, anthropic_client=anthropic_client)
        results.extend(batch_results)
        tokens = TokenUsage(
            input_tokens=tokens.input_tokens + batch_tokens.input_tokens,
            output_tokens=tokens.output_tokens + batch_tokens.output_tokens,
            cached_input_tokens=tokens.cached_input_tokens + batch_tokens.cached_input_tokens,
        )
        calls += 1

    return results, tokens, calls


async def _analyze_batch(
    batch: list[QualificationPair],
    *,
    anthropic_client: AnthropicClient,
) -> tuple[list[SemanticResult], TokenUsage]:
    """Chama o Claude para UM lote (≤ 50 pares) via tool use estruturado.

    Reusa o `_invoke`/`_get_client` privados do `AnthropicClient` indo
    direto na API pública `messages.create` — o `AnthropicClient` atual
    só expõe `extract_movements`. Para a qualificação precisamos de uma
    chamada custom; portanto montamos system+tool aqui e chamamos o SDK
    via `anthropic_client._get_client()._injected_client or AsyncAnthropic`.

    O acesso ao client interno é justificável: é o ponto de extensão
    natural pra uma 2ª feature que reusa autenticação + retry policy
    sem duplicar setup. Refatoração futura: extrair um método público
    `call_tool` no `AnthropicClient`.
    """
    # Defensivo: o cliente real é construído lazily. Em testes injetamos
    # via construtor — esse atalho expõe o mesmo SDK. Acesso a `_*` é
    # justificável: é ponto de extensão natural pra uma 2ª feature que
    # reusa autenticação + retry policy sem duplicar setup. Refator futuro:
    # extrair método público `call_tool` no `AnthropicClient`.
    sdk_client = anthropic_client._get_client()

    user_payload = _build_user_payload(batch)
    system_blocks = [
        {
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    message = await sdk_client.messages.create(
        model=anthropic_client._model,
        max_tokens=4096,
        system=system_blocks,
        tools=[_QUALIFY_TOOL],
        tool_choice={"type": "tool", "name": QUALIFY_TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Avalie a qualificação de cada par a seguir e responda "
                            "via tool report_qualification."
                        ),
                    },
                    {
                        "type": "text",
                        "text": json.dumps(user_payload, ensure_ascii=False),
                    },
                ],
            }
        ],
    )

    tokens = _extract_tokens(message)
    raw_items = _extract_tool_results(message)
    parsed = _parse_results(raw_items, expected_pair_ids={p.pair_id for p in batch})

    log.info(
        "qualification_semantic_batch_done",
        batch_size=len(batch),
        emitted=len(parsed),
        input_tokens=tokens.input_tokens,
        output_tokens=tokens.output_tokens,
        cached_input_tokens=tokens.cached_input_tokens,
    )
    return parsed, tokens


def _build_user_payload(batch: list[QualificationPair]) -> list[dict[str, Any]]:
    """Serializa o lote num formato JSON neutro (sem dataclass leak)."""
    return [
        {
            "pair_id": p.pair_id,
            "descricao_extrato": p.description,
            "fornecedor_omie": p.supplier,
            "categoria_omie": p.category,
            "valor": str(p.amount),
        }
        for p in batch
    ]


def _extract_tokens(message: Any) -> TokenUsage:
    """Lê `message.usage` defensivamente — Anthropic SDK pode ou não populá-lo."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cached_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
    )


def _extract_tool_results(message: Any) -> list[dict[str, Any]]:
    """Acha o bloco `tool_use` com `name=QUALIFY_TOOL_NAME` e devolve `results`."""
    for block in getattr(message, "content", []) or []:
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == QUALIFY_TOOL_NAME
        ):
            raw_input: Any = getattr(block, "input", None)
            if isinstance(raw_input, dict):
                results = raw_input.get("results")
                if isinstance(results, list):
                    return [r for r in results if isinstance(r, dict)]
    log.warning("qualification_semantic_tool_missing")
    return []


def _parse_results(
    raw_items: list[dict[str, Any]],
    *,
    expected_pair_ids: set[str],
) -> list[SemanticResult]:
    """Valida cada item do array `results` e descarta os inválidos.

    Critérios:
        - `pair_id` precisa estar no conjunto esperado (proteção contra
          alucinação de IDs).
        - `status` precisa ser um dos 3 literais.
        - `motivo` é truncado em `_MAX_MOTIVO_CHARS` (defesa em
          profundidade — o tool description já pede curto).

    Itens inválidos viram warning log sem stack trace e o caller os trata
    como "ok" (não flagar).
    """
    out: list[SemanticResult] = []
    seen: set[str] = set()
    for item in raw_items:
        pair_id = item.get("pair_id")
        status = item.get("status")
        motivo = item.get("motivo")
        if not isinstance(pair_id, str) or pair_id not in expected_pair_ids:
            log.warning("qualification_semantic_unknown_pair_id")
            continue
        if pair_id in seen:
            log.warning("qualification_semantic_duplicate_pair_id")
            continue
        if status not in ("ok", "suspeita", "incoerente"):
            log.warning("qualification_semantic_invalid_status")
            continue
        if not isinstance(motivo, str):
            motivo = ""
        seen.add(pair_id)
        out.append(
            SemanticResult(
                pair_id=pair_id,
                status=status,
                motivo=motivo[:_MAX_MOTIVO_CHARS].strip(),
            )
        )
    return out
