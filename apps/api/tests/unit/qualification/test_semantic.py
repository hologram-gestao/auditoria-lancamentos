"""Testes unitários da Camada 1 (semântica) — S19 BACK 12.1.

Mocka a Anthropic via injeção do `AnthropicClient` para validar:
    - Parsing do tool output (status válido → SemanticResult).
    - Mapping de status → severity.
    - Batching de 50 pares (lote único + múltiplos lotes).
    - Pares com pair_id desconhecido / status inválido são descartados.
    - Token usage extraído corretamente do `message.usage`.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

import httpx
import pytest
import respx
from anthropic import APIStatusError
from pydantic import SecretStr
from structlog.testing import capture_logs

from app.core.alerting import ALERT_ACTIONS, AlertCode
from app.core.config import Settings
from app.core.exceptions import AnthropicCreditError, AnthropicParseError, AnthropicRefusalError
from app.integrations.anthropic.client import AnthropicClient
from app.modules.reconciliations.qualification.schemas import QualificationPair
from app.modules.reconciliations.qualification.semantic import (
    _MAX_OUTPUT_TOKENS,
    QUALIFY_TOOL_NAME,
    SEMANTIC_BATCH_SIZE,
    analyze_pairs,
)
from app.modules.reconciliations.qualification.service import _run_semantic

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


class _StopDetails:
    def __init__(self, category: str | None, explanation: str) -> None:
        self.type = "refusal"
        self.category = category
        self.explanation = explanation


class _Message:
    def __init__(
        self,
        *,
        blocks: list[Any],
        usage: _Usage | None = None,
        stop_reason: str | None = None,
        stop_details: _StopDetails | None = None,
    ) -> None:
        self.content = blocks
        self.usage = usage
        self.stop_reason = stop_reason
        self.stop_details = stop_details


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
        model="claude-sonnet-5-5",
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


async def test_analyze_pairs_sem_tool_tenta_de_novo_e_levanta() -> None:
    # 86e3wg8et: com `tool_choice` auto a tool pode não vir. Devolver `[]` faria
    # o lote passar sem veredito como se tivesse sido analisado (falso negativo
    # mudo): depois de UMA nova tentativa, levanta. O caller (`_run_semantic`)
    # derruba a Camada 1 pelo caminho de falha que já existe.
    p1 = _pair()
    response = _Message(blocks=[], usage=_Usage(), stop_reason="end_turn")
    fake = _FakeAnthropic(side_effect=[response, response])
    client = AnthropicClient(
        api_key=SecretStr("fake"), model="claude-sonnet-5-5", timeout=10.0, anthropic_client=fake
    )

    with capture_logs() as logs, pytest.raises(AnthropicParseError, match="Modelo não chamou"):
        await analyze_pairs([p1], anthropic_client=client)

    assert len(fake.messages.calls) == 2
    events = [e["event"] for e in logs]
    assert events.count("anthropic_tool_not_called") == 2
    assert "qualification_semantic_tool_missing" in events


async def test_analyze_pairs_sem_tool_na_primeira_e_tool_na_segunda() -> None:
    p1 = _pair()
    fake = _FakeAnthropic(
        side_effect=[
            _Message(
                blocks=[],
                stop_reason="end_turn",
                usage=_Usage(input_tokens=1000, output_tokens=30, cache_read_input_tokens=900),
            ),
            _tool_response(
                [{"pair_id": p1.pair_id, "status": "incoerente", "motivo": "x"}],
                usage=_Usage(input_tokens=1000, output_tokens=200, cache_read_input_tokens=900),
            ),
        ]
    )
    client = AnthropicClient(
        api_key=SecretStr("fake"), model="claude-sonnet-5-5", timeout=10.0, anthropic_client=fake
    )

    results, tokens, calls = await analyze_pairs([p1], anthropic_client=client)

    assert [r.status for r in results] == ["incoerente"]
    assert len(fake.messages.calls) == 2
    assert fake.messages.calls[0] == fake.messages.calls[1]
    assert calls == 1
    # A resposta sem a tool também foi cobrada: os tokens somam as duas.
    assert (tokens.input_tokens, tokens.output_tokens, tokens.cached_input_tokens) == (
        2000,
        230,
        1800,
    )


async def test_analyze_pairs_truncado_sem_results_levanta_sem_nova_tentativa() -> None:
    p1 = _pair()
    truncated = _Message(
        blocks=[_ToolUseBlock(name=QUALIFY_TOOL_NAME, payload={})], stop_reason="max_tokens"
    )
    fake = _FakeAnthropic(side_effect=[truncated])
    client = AnthropicClient(
        api_key=SecretStr("fake"), model="claude-sonnet-5-5", timeout=10.0, anthropic_client=fake
    )

    with capture_logs() as logs, pytest.raises(AnthropicParseError):
        await analyze_pairs([p1], anthropic_client=client)

    assert len(fake.messages.calls) == 1
    assert "qualification_semantic_truncated" in [e["event"] for e in logs]


async def test_analyze_pairs_no_claude_sonnet_5_desliga_pensamento_com_disabled() -> None:
    # Voltar para a ponte pela variável de ambiente: o Sonnet 5 recusa
    # `between_tools` e desliga com `disabled`.
    p1 = _pair()
    fake = _FakeAnthropic(
        side_effect=[_tool_response([{"pair_id": p1.pair_id, "status": "ok", "motivo": "ok"}])]
    )
    client = AnthropicClient(
        api_key=SecretStr("fake"), model="claude-sonnet-5", timeout=10.0, anthropic_client=fake
    )
    await analyze_pairs([p1], anthropic_client=client)
    assert fake.messages.calls[0]["thinking"] == {"type": "disabled"}
    assert fake.messages.calls[0]["tool_choice"] == {"type": "auto"}


async def test_analyze_pairs_refusal_nao_tenta_de_novo() -> None:
    p1 = _pair()
    fake = _FakeAnthropic(
        side_effect=[
            _Message(
                blocks=[],
                stop_reason="refusal",
                stop_details=_StopDetails("cyber", "texto livre da recusa"),
            )
        ]
    )
    client = AnthropicClient(
        api_key=SecretStr("fake"), model="claude-sonnet-5-5", timeout=10.0, anthropic_client=fake
    )

    with capture_logs() as logs, pytest.raises(AnthropicRefusalError):
        await analyze_pairs([p1], anthropic_client=client)

    assert len(fake.messages.calls) == 1
    assert [e.get("category") for e in logs if e["event"] == "anthropic_refusal"] == ["cyber"]
    assert "texto livre da recusa" not in repr(logs)


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
        model="claude-sonnet-5-5",
        timeout=10.0,
        anthropic_client=fake,
    )
    await analyze_pairs([p1], anthropic_client=client)
    assert fake.messages.calls[0]["max_tokens"] == _MAX_OUTPUT_TOKENS
    assert _MAX_OUTPUT_TOKENS == 8192


async def test_analyze_pairs_between_tools_auto_e_strict() -> None:
    # 86e3wg8et (claude-sonnet-5-5): `disabled` e `tool_choice` forçado são 400.
    # `between_tools` mantém o lote sem pensamento estendido dentro dos 8192.
    p1 = _pair()
    fake = _FakeAnthropic(
        side_effect=[_tool_response([{"pair_id": p1.pair_id, "status": "ok", "motivo": "ok"}])]
    )
    client = AnthropicClient(
        api_key=SecretStr("fake"),
        model="claude-sonnet-5-5",
        timeout=10.0,
        anthropic_client=fake,
    )
    await analyze_pairs([p1], anthropic_client=client)
    call = fake.messages.calls[0]
    assert call["thinking"] == {"type": "between_tools"}
    assert call["tool_choice"] == {"type": "auto"}
    assert "output_config" not in call
    assert call["tools"][0]["strict"] is True
    assert call["model"] == "claude-sonnet-5-5"
    instruction = call["messages"][0]["content"][0]["text"]
    assert "obrigatoriamente uma chamada à tool `report_qualification`" in instruction


async def test_investment_account_injects_aplicacao_rule() -> None:
    """Report #2: conta aplicação injeta o bloco de semântica invertida no
    system prompt (APLICACAO=entrada, RESGATE=saída) como 2º bloco."""
    p1 = _pair()
    fake = _FakeAnthropic(
        side_effect=[_tool_response([{"pair_id": p1.pair_id, "status": "ok", "motivo": "ok"}])]
    )
    client = AnthropicClient(
        api_key=SecretStr("fake"),
        model="claude-sonnet-5-5",
        timeout=10.0,
        anthropic_client=fake,
    )
    await analyze_pairs([p1], anthropic_client=client, account_type="investment")

    system_blocks = fake.messages.calls[0]["system"]
    assert len(system_blocks) == 2  # _SYSTEM_PROMPT + regra de aplicação
    rule_text = system_blocks[1]["text"]
    assert "CONTA DE APLICAÇÃO" in rule_text
    assert "RESGATE" in rule_text


async def test_checking_account_does_not_inject_aplicacao_rule() -> None:
    """Conta corrente (default) NÃO injeta a regra — só o system prompt base."""
    p1 = _pair()
    fake = _FakeAnthropic(
        side_effect=[_tool_response([{"pair_id": p1.pair_id, "status": "ok", "motivo": "ok"}])]
    )
    client = AnthropicClient(
        api_key=SecretStr("fake"),
        model="claude-sonnet-5-5",
        timeout=10.0,
        anthropic_client=fake,
    )
    await analyze_pairs([p1], anthropic_client=client)  # default checking

    system_blocks = fake.messages.calls[0]["system"]
    assert len(system_blocks) == 1


# ----------------------------------------------------------------------
# 86e3mpmcx — a qualificação passa pelo caminho do `AnthropicClient`
# (`call_tool`): crédito esgotado vira `AnthropicCreditError` com o alerta de
# plantão, 5xx tem a nova tentativa, e o log não leva o texto da Anthropic.
# ----------------------------------------------------------------------

_ALERT_WEBHOOK = "https://hooks.test/plantao-credito"
_PROVIDER_TEXT = "Your credit balance is too low to access the Anthropic API."
_FAKE_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _credit_error() -> APIStatusError:
    return APIStatusError(
        message=_PROVIDER_TEXT,
        response=httpx.Response(400, request=_FAKE_REQUEST),
        body={"error": {"type": "invalid_request_error", "message": _PROVIDER_TEXT}},
    )


def _server_error() -> APIStatusError:
    return APIStatusError(
        message="Internal server error",
        response=httpx.Response(500, request=_FAKE_REQUEST),
        body={"error": {"type": "api_error"}},
    )


def _alerting_client(fake: _FakeAnthropic) -> AnthropicClient:
    settings = Settings(ALERT_WEBHOOK_URL=_ALERT_WEBHOOK, ALERT_EMAIL_TO=None, ALERT_SMTP_HOST=None)
    return AnthropicClient(
        api_key=SecretStr("fake"),
        model="claude-sonnet-5-5",
        timeout=10.0,
        anthropic_client=fake,
        alert_settings=settings,
    )


@pytest.fixture
def _fresh_credit_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dedup do alerta é por processo: cada teste começa com a janela vazia."""
    import app.integrations.anthropic.client as client_module

    monkeypatch.setattr(client_module, "_credit_alert_last_at", None)


@pytest.mark.usefixtures("_fresh_credit_window")
async def test_sem_credito_na_qualificacao_levanta_erro_proprio_e_alerta() -> None:
    fake = _FakeAnthropic(side_effect=[_credit_error()])
    client = _alerting_client(fake)

    with respx.mock:
        route = respx.post(_ALERT_WEBHOOK).mock(return_value=httpx.Response(200))
        with pytest.raises(AnthropicCreditError):
            await analyze_pairs([_pair()], anthropic_client=client)

    # Crédito não é transitório: uma chamada só, sem retry nem nova tentativa.
    assert len(fake.messages.calls) == 1
    assert route.call_count == 1
    text = json.loads(route.calls[0].request.content)["text"]
    assert "[Hologram OS] anthropic_credit" in text
    assert f"Ação sugerida: {ALERT_ACTIONS[AlertCode.ANTHROPIC_CREDIT]}" in text
    assert "reprocessar refaz a análise" in text
    assert _PROVIDER_TEXT not in text


@pytest.mark.usefixtures("_fresh_credit_window")
async def test_sem_credito_o_semantic_devolve_vazio_e_loga_so_o_codigo() -> None:
    fake = _FakeAnthropic(side_effect=[_credit_error()])
    client = _alerting_client(fake)

    with respx.mock, capture_logs() as logs:
        respx.post(_ALERT_WEBHOOK).mock(return_value=httpx.Response(200))
        results, tokens, calls = await _run_semantic(
            [_pair()],
            anthropic_client=client,
            account_type="checking",
            client_id=uuid4(),
            glossary_block=None,
        )

    assert (results, calls) == ([], 0)
    assert tokens.input_tokens == 0
    failed = [e for e in logs if e["event"] == "qualification_semantic_failed"]
    assert len(failed) == 1
    assert failed[0]["error_code"] == "ANTHROPIC_CREDIT_EXHAUSTED"
    assert "exc_info" not in failed[0]
    # Nenhum texto do provedor em nenhuma linha de log (§3.3, §4.5).
    assert "credit balance" not in repr(logs).lower()


@pytest.mark.usefixtures("_fresh_credit_window")
async def test_dois_lotes_seguidos_sem_credito_geram_um_alerta() -> None:
    fake = _FakeAnthropic(side_effect=[_credit_error(), _credit_error()])
    client = _alerting_client(fake)

    with respx.mock:
        route = respx.post(_ALERT_WEBHOOK).mock(return_value=httpx.Response(200))
        for _ in range(2):
            results, _tokens, _calls = await _run_semantic(
                [_pair()],
                anthropic_client=client,
                account_type="checking",
                client_id=uuid4(),
                glossary_block=None,
            )
            assert results == []

    assert len(fake.messages.calls) == 2
    assert route.call_count == 1


async def test_qualificacao_tenta_de_novo_um_5xx() -> None:
    p1 = _pair()
    ok = _tool_response(
        [{"pair_id": p1.pair_id, "status": "ok", "motivo": "coerente"}],
        usage=_Usage(input_tokens=10, output_tokens=2),
    )
    fake = _FakeAnthropic(side_effect=[_server_error(), ok])
    client = AnthropicClient(
        api_key=SecretStr("fake"),
        model="claude-sonnet-5-5",
        timeout=10.0,
        anthropic_client=fake,
    )

    results, tokens, calls = await analyze_pairs([p1], anthropic_client=client)

    assert len(fake.messages.calls) == 2
    assert fake.messages.calls[0] == fake.messages.calls[1]
    assert [r.status for r in results] == ["ok"]
    assert (tokens.input_tokens, calls) == (10, 1)
