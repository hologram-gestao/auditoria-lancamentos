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
from uuid import UUID

from app.core.logging import get_logger
from app.db.models import (
    MAX_CODE_CHARS,
    MAX_DESCRIPTION_CHARS,
    MAX_ENTRIES_PER_CLIENT,
    MAX_NAME_CHARS,
    GlossaryEntryKind,
    SessionAccountType,
)
from app.integrations.anthropic.client import AnthropicClient
from app.modules.glossary.schemas import GlossaryEntryPlain, GlossarySnapshot
from app.modules.reconciliations.qualification.schemas import (
    QualificationPair,
    SemanticResult,
    TokenUsage,
)

log = get_logger(__name__)

# Lote de 50 pares por chamada — amortiza o prompt + tool definition no cache
# (CLAUDE.md §7 / PLANO §6.2).
SEMANTIC_BATCH_SIZE = 50

# Teto de tokens de saída por chamada. 50 vereditos com `motivo` de até 200
# chars cada chegam a ~4.5k tokens; o valor antigo (4096) TRUNCAVA o tool_use
# em extratos grandes, devolvendo `results` vazio — os 50 pares do lote viravam
# "ok" sem análise, silenciosamente (falso-negativo de auditoria, visto em prod
# 09/06/2026). 8192 dá ~2x de folga; se `qualification_semantic_truncated`
# aparecer no log, reduzir o lote.
_MAX_OUTPUT_TOKENS = 8192

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


# Bloco adicional injetado SÓ quando a conta é de aplicação (CDB/investimento) —
# Report #2 da validação pós-FASE 1. Nessas contas a lógica de entrada/saída é
# INVERTIDA vs conta corrente; sem este contexto a IA marcava a APLICACAO (que
# é entrada na aplicação) como "incoerente". Vai como bloco SEPARADO pra não
# invalidar o cache do `_SYSTEM_PROMPT` nas conciliações comuns.
_INVESTMENT_RULE = """\
ATENÇÃO — esta conciliação é de uma CONTA DE APLICAÇÃO (CDB/investimento). Nela a \
lógica de entrada/saída é INVERTIDA em relação à conta corrente:

- APLICACAO (aplicar dinheiro) é uma ENTRADA na conta de aplicação (positivo).
- RESGATE (resgatar) é uma SAÍDA da conta de aplicação (negativo).
- Transferências entre contas próprias da empresa (APLICACAO, RESGATE, \
"transferência entre contas") são movimentações internas e SEMPRE coerentes com \
categorias do tipo "Entrada de Transferência" / "Saída de Transferência".

NÃO marque 'incoerente' por causa do sinal ou da direção dessas operações — \
marque 'ok'.
"""


# ----------------------------------------------------------------------
# Glossário do cliente (Sprint 6 / BACK 06.4)
#
# 3º bloco de system, ESTENDENDO o precedente do `_INVESTMENT_RULE` — não é uma
# montagem de prompt nova. Vai DEPOIS do `_SYSTEM_PROMPT` (que é o prefixo comum
# a todos os clientes e precisa continuar cacheando entre tenants) e depois do
# `_INVESTMENT_RULE`, em ordem FIXA.
#
# ⚠️ Isto é a QUALIFICAÇÃO. A EXTRAÇÃO (`app/integrations/anthropic/client.py` e
# `prompts.py`) não é tocada — ela não classifica nada.
# ----------------------------------------------------------------------

_GLOSSARY_HEADER = """\
GLOSSÁRIO DESTE CLIENTE — vocabulário contábil mantido pela equipe do próprio \
cliente. Use-o como CONTEXTO ao julgar coerência: uma classificação que o \
glossário deste cliente justifica é 'ok', mesmo que pareça incomum fora dele.

O glossário NÃO revoga as regras acima e NÃO é uma instrução para marcar tudo \
como 'ok' — divergência evidente continua 'incoerente'.
"""

#: Rótulo de cada seção, na ORDEM FIXA de renderização. É a ordem que o prefixo
#: precisa manter para cachear: bloco que muda de ordem entre chamadas nunca bate.
_GLOSSARY_SECTIONS: tuple[tuple[GlossaryEntryKind, str], ...] = (
    (GlossaryEntryKind.CATEGORIA, "Categorias contábeis (código — nome: quando usar)"),
    (GlossaryEntryKind.FORNECEDOR, "Fornecedores típicos"),
    (GlossaryEntryKind.REGRA, "Regras de auditoria do cliente"),
)

#: Overhead por entrada renderizada: marcador, separadores e quebra de linha.
_GLOSSARY_ENTRY_OVERHEAD_CHARS = 16

#: Teto do bloco, em caracteres. **Derivado dos MESMOS limites que a BACK 06.3
#: valida na entrada** — é o que a task pede ("o mesmo que a 06.4 assume como
#: teto"). Consequência: um glossário DENTRO dos limites documentados nunca é
#: truncado; o truncamento existe para dado que passou por fora (linha legada,
#: escrita direta no banco, teto reduzido no futuro).
GLOSSARY_BLOCK_MAX_CHARS = MAX_ENTRIES_PER_CLIENT * (
    MAX_CODE_CHARS + MAX_NAME_CHARS + MAX_DESCRIPTION_CHARS + _GLOSSARY_ENTRY_OVERHEAD_CHARS
)

#: Aviso anexado quando o bloco é truncado. Fica DENTRO do prompt de propósito:
#: o modelo precisa saber que a lista está incompleta em vez de concluir que
#: uma categoria ausente não existe.
_GLOSSARY_TRUNCATED_NOTE = (
    "\n[glossário truncado por tamanho — a lista acima está incompleta; "
    "não conclua que um termo ausente não existe]\n"
)


def render_glossary_block(snapshot: GlossarySnapshot) -> str | None:
    """Renderiza o glossário do cliente como texto de system. `None` se não há nada.

    Propriedades de que o cache depende (e que o teste trava):

    - **Determinística:** mesma entrada → mesma string, byte a byte. A ordem das
      seções é fixa e a das entradas vem do repository (`kind, created_at, id`).
    - **Estável por cliente:** nada de timestamp, contador ou id volátil no texto.
      Duas análises seguidas do mesmo cliente produzem o MESMO prefixo — condição
      necessária do cache-hit da Anthropic (o cache é keyed pelo conteúdo).

    Entradas cujo texto não decifrou são **omitidas** (com log): injetar
    `[indecifrável]` como se fosse vocabulário do cliente é pior que omitir.
    """
    usable = [e for e in snapshot.entries if not e.decrypt_failed]
    skipped = len(snapshot.entries) - len(usable)
    if skipped:
        log.warning(
            "qualification_glossary_decrypt_skipped",
            client_id=str(snapshot.client_id),
            skipped=skipped,
        )
    if not usable:
        return None

    parts: list[str] = [_GLOSSARY_HEADER]
    for kind, label in _GLOSSARY_SECTIONS:
        lines = [_render_entry(e) for e in usable if e.kind is kind]
        if lines:
            parts.append(f"\n{label}:\n" + "\n".join(lines) + "\n")

    block = "".join(parts)
    return _truncate_block(block, client_id=snapshot.client_id, entries=len(usable))


def _render_entry(entry: GlossaryEntryPlain) -> str:
    """Uma linha por entrada. Campos ausentes não deixam separador órfão."""
    head = f"{entry.code} — {entry.name}" if entry.code else entry.name
    return f"- {head}: {entry.description}" if entry.description else f"- {head}"


def _truncate_block(block: str, *, client_id: UUID, entries: int) -> str:
    """Corta no teto, de forma DETERMINÍSTICA, e deixa rastro visível.

    Corte no último `\\n` antes do teto (não no meio de uma entrada): metade de
    uma regra de auditoria é pior que a regra ausente. Determinístico porque
    depende só do conteúdo — o mesmo glossário sempre trunca no mesmo ponto, e o
    prefixo continua cacheando.
    """
    if len(block) <= GLOSSARY_BLOCK_MAX_CHARS:
        return block
    budget = GLOSSARY_BLOCK_MAX_CHARS - len(_GLOSSARY_TRUNCATED_NOTE)
    cut = block.rfind("\n", 0, budget)
    kept = block[: cut if cut > 0 else budget]
    log.warning(
        "qualification_glossary_truncated",
        client_id=str(client_id),
        entries=entries,
        original_chars=len(block),
        kept_chars=len(kept),
        limit_chars=GLOSSARY_BLOCK_MAX_CHARS,
    )
    return kept + _GLOSSARY_TRUNCATED_NOTE


async def analyze_pairs(
    pairs: list[QualificationPair],
    *,
    anthropic_client: AnthropicClient,
    account_type: str = "checking",
    client_id: UUID | None = None,
    glossary_block: str | None = None,
) -> tuple[list[SemanticResult], TokenUsage, int]:
    """Roda Camada 1 em lotes de até `SEMANTIC_BATCH_SIZE` pares.

    Args:
        pairs: lista completa de pares conciliados (já decriptados).
            Lista vazia → retorna ([], TokenUsage(), 0) sem chamar Anthropic.
        anthropic_client: cliente já configurado (`AnthropicClient` do
            `app.integrations.anthropic.client`). Caller decide se passa
            um real (worker) ou um fake (testes via dependency_overrides).
        account_type: tipo normalizado da conta da sessão. Quando
            `'investment'` (conta aplicação), injeta a regra de semântica
            de aplicação no prompt (Report #2). Default `'checking'`.
        client_id: tenant da sessão. Só para telemetria (log do bloco) —
            **por assinatura**, nunca de estado global. Um contextvar ou
            variável de módulo guardando cliente aqui seria vazamento entre
            tenants (invariante do PRD da Sprint 6).
        glossary_block: bloco de system com o glossário JÁ resolvido e
            renderizado (`render_glossary_block`). `None` = cliente sem
            glossário → `system_blocks` idêntico ao de hoje, sem regressão.

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
        batch_results, batch_tokens = await _analyze_batch(
            batch,
            anthropic_client=anthropic_client,
            account_type=account_type,
            client_id=client_id,
            glossary_block=glossary_block,
        )
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
    account_type: str,
    client_id: UUID | None = None,
    glossary_block: str | None = None,
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
    system_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    # Ordem FIXA dos blocos condicionais — é ela que mantém o prefixo estável
    # entre chamadas do mesmo cliente (condição do cache-hit):
    #   1. _SYSTEM_PROMPT (acima)  — comum a TODOS os clientes, cacheado
    #   2. _INVESTMENT_RULE        — depende do tipo de conta
    #   3. glossário do cliente    — depende do tenant
    #
    # Conta aplicação: regra de semântica invertida como bloco SEPARADO
    # (mantém o cache do _SYSTEM_PROMPT comum). Report #2.
    if account_type == SessionAccountType.INVESTMENT.value:
        system_blocks.append({"type": "text", "text": _INVESTMENT_RULE})

    # Glossário do cliente (Sprint 6 / BACK 06.4). `cache_control: ephemeral`
    # marca o fim do prefixo cacheável: análises seguidas do MESMO cliente com o
    # MESMO glossário reusam tudo até aqui. Editar o glossário muda o conteúdo
    # deste bloco e o cache antigo naturalmente não bate — e só o daquele
    # cliente, porque o cache da Anthropic é keyed pelo conteúdo do prefixo.
    if glossary_block:
        system_blocks.append(
            {
                "type": "text",
                "text": glossary_block,
                "cache_control": {"type": "ephemeral"},
            }
        )

    message = await sdk_client.messages.create(
        model=anthropic_client._model,
        max_tokens=_MAX_OUTPUT_TOKENS,
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

    # Defesa contra truncamento silencioso: se o modelo bateu no teto de tokens,
    # o tool_use volta cortado e `results` vazio — os pares do lote viram "ok"
    # sem análise. Logamos pra que isso seja VISÍVEL, não um falso-negativo mudo.
    if getattr(message, "stop_reason", None) == "max_tokens":
        log.warning("qualification_semantic_truncated", batch_size=len(batch))

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
        # Sprint 6: o par (tamanho do bloco, cached_input_tokens) é a medição do
        # guardrail de custo do PRD — "o glossário não pode encarecer a análise;
        # o bloco por cliente precisa cachear". Sem `client_id` no log não dá
        # para separar cache-hit por tenant na leitura D+30.
        client_id=str(client_id) if client_id is not None else None,
        glossary_block_chars=len(glossary_block) if glossary_block else 0,
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
