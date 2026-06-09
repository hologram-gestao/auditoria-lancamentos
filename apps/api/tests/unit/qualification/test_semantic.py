"""Testes unitários da Camada 1 (semântica) — S19 BACK 12.1.

Mocka a Anthropic via injeção do `AnthropicClient` para validar:
    - Parsing do tool output (status válido → SemanticResult).
    - Mapping de status → severity.
    - Batching de 50 pares (lote único + múltiplos lotes).
    - Pares com pair_id desconhecido / status inválido são descartados.
    - Token usage extraído corretamente do `message.usage`.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import SecretStr

from app.integrations.anthropic.client import AnthropicClient
from app.modules.reconciliations.qualification.schemas import QualificationPair
from app.modules.reconciliations.qualification.semantic import (
    _MAX_OUTPUT_TOKENS,
    QUALIFY_TOOL_NAME,
    SEMANTIC_BATCH_SIZE,
    analyze_pairs,
)

# ----------------------------------------------------------------------
# Fakes (espelham anthropic.types sem importar o SDK real)
# ----------------------------------------------------------------------


class _ToolUseBlock:
    def __init__(self, *, name: str, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.id = "toolu_qualify_test"
        self.input = payload


class _Usage:
    def __init__(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens


class _Message:
    def __init__(
        self,
        *,
        blocks: list[Any],
        usage: _Usage | None = None,
    ) -> None:
        self.content = blocks
        self.usage = usage


class _FakeMessages:
    def __init__(self, *, side_effect: list[Any]) -> None:
        self._queue = list(side_effect)
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._queue:
            raise RuntimeError("Fake esgotou — teste enfileirou poucos resultados.")
        value = self._queue.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


class _FakeAnthropic:
    def __init__(self, *, side_effect: list[Any]) -> None:
        self.messages = _FakeMessages(side_effect=side_effect)


def _pair(
    *, supplier: str | None = "Padaria X", category: str | None = "Despesas"
) -> QualificationPair:
    fid = uuid4()
    return QualificationPair(
        pair_id=str(fid),
        file_entry_id=fid,
        omie_lancamento_id=10_000,
        description="PAGAMENTO PIX",
        supplier=supplier,
        category=category,
        amount=Decimal("-150.00"),
    )


def _build_client(*, side_effect: list[Any]) -> AnthropicClient:
    """Constrói AnthropicClient com fake SDK injetado."""
    return AnthropicClient(
        api_key=SecretStr("fake"),
        model="claude-sonnet-4-5",
        timeout=10.0,
        anthropic_client=_FakeAnthropic(side_effect=side_effect),
    )


def _tool_response(pair_results: list[dict[str, Any]], *, usage: _Usage | None = None) -> _Message:
    return _Message(
        blocks=[_ToolUseBlock(name=QUALIFY_TOOL_NAME, payload={"results": pair_results})],
        usage=usage,
    )


# ----------------------------------------------------------------------
# Casos
# ----------------------------------------------------------------------


async def test_analyze_pairs_empty_returns_empty() -> None:
    client = _build_client(side_effect=[])
    results, tokens, calls = await analyze_pairs([], anthropic_client=client)
    assert results == []
    assert tokens.input_tokens == 0
    assert calls == 0


async def test_analyze_pairs_single_batch_parses_tool_output() -> None:
    p1, p2 = _pair(), _pair()
    response = _tool_response(
        [
            {"pair_id": p1.pair_id, "status": "ok", "motivo": "coerente"},
            {
                "pair_id": p2.pair_id,
                "status": "incoerente",
                "motivo": "tarifa marcada como receita",
            },
        ],
        usage=_Usage(input_tokens=900, output_tokens=120, cache_read_input_tokens=850),
    )
    client = _build_client(side_effect=[response])
    results, tokens, calls = await analyze_pairs([p1, p2], anthropic_client=client)
    assert calls == 1
    assert len(results) == 2
    by_id = {r.pair_id: r for r in results}
    assert by_id[p1.pair_id].status == "ok"
    assert by_id[p2.pair_id].status == "incoerente"
    assert tokens.input_tokens == 900
    assert tokens.output_tokens == 120
    assert tokens.cached_input_tokens == 850


async def test_analyze_pairs_batches_at_50() -> None:
    # 75 pares = 2 lotes (50 + 25)
    pairs = [_pair() for _ in range(75)]
    response_batch1 = _tool_response(
        [{"pair_id": p.pair_id, "status": "ok", "motivo": "ok"} for p in pairs[:50]],
        usage=_Usage(input_tokens=1500, output_tokens=200),
    )
    response_batch2 = _tool_response(
        [{"pair_id": p.pair_id, "status": "ok", "motivo": "ok"} for p in pairs[50:]],
        usage=_Usage(input_tokens=800, output_tokens=100),
    )
    client = _build_client(side_effect=[response_batch1, response_batch2])
    results, tokens, calls = await analyze_pairs(pairs, anthropic_client=client)
    assert calls == 2
    assert len(results) == 75
    # Tokens agregados.
    assert tokens.input_tokens == 2300
    assert tokens.output_tokens == 300
    # Confere que o batch size respeitado.
    assert SEMANTIC_BATCH_SIZE == 50


async def test_analyze_pairs_drops_unknown_pair_id() -> None:
    p1 = _pair()
    response = _tool_response(
        [
            {"pair_id": p1.pair_id, "status": "suspeita", "motivo": "ambiguo"},
            {"pair_id": "id-alucinado-fora-da-entrada", "status": "ok", "motivo": "ok"},
        ]
    )
    client = _build_client(side_effect=[response])
    results, _tokens, _calls = await analyze_pairs([p1], anthropic_client=client)
    assert len(results) == 1
    assert results[0].pair_id == p1.pair_id
    assert results[0].status == "suspeita"


async def test_analyze_pairs_drops_invalid_status() -> None:
    p1 = _pair()
    response = _tool_response([{"pair_id": p1.pair_id, "status": "INVALIDO", "motivo": "??"}])
    client = _build_client(side_effect=[response])
    results, _tokens, _calls = await analyze_pairs([p1], anthropic_client=client)
    assert results == []


async def test_analyze_pairs_missing_tool_use_returns_empty_for_batch() -> None:
    # Modelo respondeu free-text sem tool — caller trata todos como "ok"
    # (ausência = não flagar). Não levanta.
    p1 = _pair()
    response = _Message(blocks=[], usage=_Usage())
    client = _build_client(side_effect=[response])
    results, _tokens, calls = await analyze_pairs([p1], anthropic_client=client)
    assert results == []
    assert calls == 1


async def test_analyze_pairs_truncates_long_motivo() -> None:
    p1 = _pair()
    very_long = "x" * 500
    response = _tool_response([{"pair_id": p1.pair_id, "status": "suspeita", "motivo": very_long}])
    client = _build_client(side_effect=[response])
    results, _tokens, _calls = await analyze_pairs([p1], anthropic_client=client)
    assert len(results) == 1
    assert len(results[0].motivo) <= 200


async def test_analyze_pairs_uses_max_output_tokens_8192() -> None:
    # Regressão (prod 09/06/2026): max_tokens=4096 truncava o tool_use em lotes
    # de 50 vereditos, devolvendo `results` vazio — 50 pares viravam "ok" sem
    # análise (falso-negativo de auditoria). O teto agora é 8192.
    p1 = _pair()
    fake = _FakeAnthropic(
        side_effect=[_tool_response([{"pair_id": p1.pair_id, "status": "ok", "motivo": "ok"}])]
    )
    client = AnthropicClient(
        api_key=SecretStr("fake"),
        model="claude-sonnet-4-5",
        timeout=10.0,
        anthropic_client=fake,
    )
    await analyze_pairs([p1], anthropic_client=client)
    assert fake.messages.calls[0]["max_tokens"] == _MAX_OUTPUT_TOKENS
    assert _MAX_OUTPUT_TOKENS == 8192
