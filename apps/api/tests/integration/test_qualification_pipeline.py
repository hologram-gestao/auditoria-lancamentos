"""Teste end-to-end do pipeline com qualificação (S19 BACK 12.1).

Roda `run_reconciliation_processing` com Omie mockado (respx) e Anthropic
mockado (monkeypatch direto no `AnthropicClient`). Valida:

    - Com QUALIFICATION_ENABLED=true + seed → cria anomalias `qualificacao_*`.
    - Com QUALIFICATION_ENABLED=false → matching segue, sem anomalias
      novas além das estruturais.
    - Falha do Anthropic (5xx persistente) NÃO derruba o matching.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    AnomalySeverity,
    AnomalyType,
    Client,
    FileEntrySituation,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationSession,
    User,
    UserRole,
)
from app.modules.reconciliations.processing.anomalies import (
    ANOMALY_CODE_MISSING_IN_FILE,
    ANOMALY_CODE_MISSING_IN_OMIE,
)
from app.modules.reconciliations.processing.job import run_reconciliation_processing
from app.modules.reconciliations.qualification.semantic import QUALIFY_TOOL_NAME
from app.modules.reconciliations.qualification.service import (
    ANOMALY_CODE_PADRAO_QUEBRADO,
    ANOMALY_CODE_QUALIF_INCOERENTE,
    ANOMALY_CODE_QUALIF_SUSPEITA,
    ANOMALY_CODE_VALOR_OUTLIER,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


OMIE_EXTRATO_URL = "https://app.omie.com.br/api/v1/financas/extrato/"
OMIE_PAGAR_URL = "https://app.omie.com.br/api/v1/financas/contapagar/"
OMIE_RECEBER_URL = "https://app.omie.com.br/api/v1/financas/contareceber/"


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


@pytest.fixture
async def factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _seed_all_anomaly_types(factory: async_sessionmaker[AsyncSession]) -> None:
    seed = [
        (ANOMALY_CODE_MISSING_IN_OMIE, AnomalySeverity.CRITICAL),
        (ANOMALY_CODE_MISSING_IN_FILE, AnomalySeverity.CRITICAL),
        (ANOMALY_CODE_QUALIF_SUSPEITA, AnomalySeverity.MODERATE),
        (ANOMALY_CODE_QUALIF_INCOERENTE, AnomalySeverity.CRITICAL),
        (ANOMALY_CODE_PADRAO_QUEBRADO, AnomalySeverity.INFO),
        (ANOMALY_CODE_VALOR_OUTLIER, AnomalySeverity.INFO),
    ]
    async with factory() as s, s.begin():
        for code, severity in seed:
            existing = await s.scalar(select(AnomalyType).where(AnomalyType.code == code))
            if existing is None:
                s.add(
                    AnomalyType(
                        code=code,
                        name=code.replace("_", " ").title(),
                        description=f"Seed teste — {code}",
                        severity=severity.value,
                        active=True,
                    )
                )


async def _seed_admin(factory: async_sessionmaker[AsyncSession], email: str) -> User:
    async with factory() as s, s.begin():
        existing = await s.scalar(select(User).where(User.email == email.lower()))
        if existing is not None:
            return existing
        u = User(
            name="A",
            email=email.lower(),
            password_hash=hash_password("Senh@Forte#1"),
            role=UserRole.ADMIN.value,
            active=True,
        )
        s.add(u)
    async with factory() as s:
        return (await s.execute(select(User).where(User.email == email.lower()))).scalar_one()


async def _seed_client(
    factory: async_sessionmaker[AsyncSession],
    creator_id: UUID,
    name: str,
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt("k", hex_key)
    ct_secret, iv_secret = encrypt("s", hex_key)
    async with factory() as s, s.begin():
        c = Client(
            name=name,
            omie_app_key_encrypted=ct_key,
            omie_app_key_iv=iv_key,
            omie_app_secret_encrypted=ct_secret,
            omie_app_secret_iv=iv_secret,
            active=True,
            created_by=creator_id,
        )
        s.add(c)
        await s.flush()
        cid = c.id
    async with factory() as s:
        return (await s.execute(select(Client).where(Client.id == cid))).scalar_one()


async def _seed_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    client_id: UUID,
    created_by: UUID,
    transactions: list[tuple[date, str, Decimal]],
    file_hash: str,
) -> UUID:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    async with factory() as s, s.begin():
        sess = ReconciliationSession(
            client_id=client_id,
            created_by=created_by,
            omie_conta_id=42,
            reference_month=date(2026, 4, 1),
            date_tolerance_days=3,
            file_hash=file_hash,
            status="processing",
            balance_start=Decimal("0.00"),
        )
        s.add(sess)
        await s.flush()
        for tx_date, descr, amount in transactions:
            ct, iv = encrypt(descr, hex_key)
            s.add(
                ReconciliationFileEntry(
                    session_id=sess.id,
                    transaction_date=tx_date,
                    description_encrypted=ct,
                    description_iv=iv,
                    amount=amount,
                    situation=FileEntrySituation.SEM_OMIE.value,
                )
            )
        return sess.id


def _ok_extrato(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"listaMovimentos": items}


def _empty_pagar() -> dict[str, Any]:
    return {"conta_pagar_cadastro": []}


def _empty_receber() -> dict[str, Any]:
    return {"conta_receber_cadastro": []}


def _setup_omie_mocks_for_one_match() -> None:
    """1 transação → 1 match. Extrato + pagar/receber vazios depois."""
    # `populate_from_extrato` chama `listar_extrato` 1x extra. Mock devolve
    # o mesmo payload nas duas chamadas (mais simples e realista).
    respx.post(OMIE_EXTRATO_URL).mock(
        return_value=httpx.Response(
            200,
            json=_ok_extrato(
                [
                    {
                        "nCodLancamento": 7001,
                        "cNatureza": "D",
                        "dDataLancamento": "05/04/2026",
                        "nValorDocumento": 100.00,
                        "cObservacoes": "PAGAMENTO TARIFA",
                        "cSituacao": "Conciliado",
                        "cRazCliente": "Banco do Brasil",
                        "cDesCategoria": "Tarifa Bancária",
                    }
                ]
            ),
        )
    )
    respx.post(OMIE_PAGAR_URL).mock(return_value=httpx.Response(200, json=_empty_pagar()))
    respx.post(OMIE_RECEBER_URL).mock(return_value=httpx.Response(200, json=_empty_receber()))


# Fakes do Anthropic — mesmo padrão de test_anthropic_client.py.
class _ToolUseBlock:
    def __init__(self, *, name: str, payload: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = name
        self.id = "tu_test"
        self.input = payload


class _Usage:
    def __init__(self) -> None:
        self.input_tokens = 1200
        self.output_tokens = 80
        self.cache_read_input_tokens = 1100


class _Message:
    def __init__(self, *, blocks: list[Any]) -> None:
        self.content = blocks
        self.usage = _Usage()


class _FakeMessages:
    def __init__(self, *, side_effect: Any | list[Any]) -> None:
        self._se = side_effect
        self._queue = list(side_effect) if isinstance(side_effect, list) else []
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self._se, list):
            if not self._queue:
                raise RuntimeError("Fake esgotou.")
            value = self._queue.pop(0)
        else:
            value = self._se
        if isinstance(value, BaseException):
            raise value
        return value


class _FakeAnthropic:
    def __init__(self, *, side_effect: Any | list[Any]) -> None:
        self.messages = _FakeMessages(side_effect=side_effect)


def _semantic_response_incoerente(pair_id: str) -> _Message:
    return _Message(
        blocks=[
            _ToolUseBlock(
                name=QUALIFY_TOOL_NAME,
                payload={
                    "results": [
                        {
                            "pair_id": pair_id,
                            "status": "incoerente",
                            "motivo": "Tarifa bancária classificada como receita.",
                        }
                    ]
                },
            )
        ]
    )


def _semantic_response_ok(pair_id: str) -> _Message:
    return _Message(
        blocks=[
            _ToolUseBlock(
                name=QUALIFY_TOOL_NAME,
                payload={"results": [{"pair_id": pair_id, "status": "ok", "motivo": "ok"}]},
            )
        ]
    )


def _patch_anthropic(monkeypatch: pytest.MonkeyPatch, side_effect: Any) -> None:
    """Substitui o `_get_client` do AnthropicClient pra retornar fake."""
    from app.integrations.anthropic.client import AnthropicClient

    fake = _FakeAnthropic(side_effect=side_effect)

    def _fake_get_client(self: AnthropicClient) -> Any:
        return fake

    monkeypatch.setattr(AnthropicClient, "_get_client", _fake_get_client)


# ----------------------------------------------------------------------
# Casos
# ----------------------------------------------------------------------


@pytest.mark.integration
@respx.mock
async def test_pipeline_creates_qualification_anomaly_when_enabled(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sessão com 1 match + IA retorna `incoerente` → 1 anomaly criada."""
    monkeypatch.setenv("QUALIFICATION_ENABLED", "true")
    get_settings.cache_clear()

    await _seed_all_anomaly_types(factory)
    admin = await _seed_admin(factory, "pipeline-q-on@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cli Pipeline Q On")
    session_id = await _seed_session(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        transactions=[(date(2026, 4, 5), "PAGAMENTO TARIFA", Decimal("-100.00"))],
        file_hash=_hex64("pipeline-q-on"),
    )

    _setup_omie_mocks_for_one_match()

    # IA: vamos receber 1 par. Patch retorna `incoerente`.
    # Pegamos o `pair_id` real depois (= str(file_entry.id)). Aqui o lote
    # tem só 1 par; vamos usar um catch-all callable que devolve `incoerente`
    # pro 1º pair_id que aparecer.
    pair_id_holder: dict[str, str] = {}

    def _capture_and_respond(**kwargs: Any) -> _Message:
        # Inspeciona o user message pra extrair o pair_id.
        msgs = kwargs.get("messages", [])
        if msgs:
            content = msgs[0].get("content", [])
            for block in content:
                text = block.get("text", "") if isinstance(block, dict) else ""
                if "pair_id" in text:
                    import json
                    import re

                    match = re.search(r"\[.*\]", text)
                    if match:
                        data = json.loads(match.group(0))
                        if data:
                            pair_id_holder["v"] = data[0]["pair_id"]
        pid = pair_id_holder.get("v", "")
        return _semantic_response_incoerente(pid)

    class _CallableFake:
        async def create(self, **kwargs: Any) -> Any:
            return _capture_and_respond(**kwargs)

    class _CallableAnthropic:
        def __init__(self) -> None:
            self.messages = _CallableFake()

    fake = _CallableAnthropic()
    from app.integrations.anthropic.client import AnthropicClient

    monkeypatch.setattr(AnthropicClient, "_get_client", lambda self: fake)

    await run_reconciliation_processing(
        str(session_id), settings=get_settings(), session_factory=factory
    )

    async with factory() as s:
        sess = (
            await s.execute(
                select(ReconciliationSession).where(ReconciliationSession.id == session_id)
            )
        ).scalar_one()
        assert sess.status == "reviewing", sess.error_message
        anomalies = (
            await s.execute(
                select(ReconciliationAnomaly, AnomalyType)
                .join(AnomalyType, ReconciliationAnomaly.anomaly_type_id == AnomalyType.id)
                .where(ReconciliationAnomaly.session_id == session_id)
            )
        ).all()
        codes = [atype.code for _, atype in anomalies]
        assert ANOMALY_CODE_QUALIF_INCOERENTE in codes


@pytest.mark.integration
@respx.mock
async def test_pipeline_skips_qualification_when_flag_disabled(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag desligada → mesmo cenário, NENHUMA anomaly de qualificação."""
    monkeypatch.setenv("QUALIFICATION_ENABLED", "false")
    get_settings.cache_clear()

    await _seed_all_anomaly_types(factory)
    admin = await _seed_admin(factory, "pipeline-q-off@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cli Pipeline Q Off")
    session_id = await _seed_session(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        transactions=[(date(2026, 4, 5), "PAGAMENTO TARIFA", Decimal("-100.00"))],
        file_hash=_hex64("pipeline-q-off"),
    )

    # Sem populate_from_extrato → só uma chamada extrato.
    respx.post(OMIE_EXTRATO_URL).mock(
        return_value=httpx.Response(
            200,
            json=_ok_extrato(
                [
                    {
                        "nCodLancamento": 7001,
                        "cNatureza": "D",
                        "dDataLancamento": "05/04/2026",
                        "nValorDocumento": 100.00,
                        "cObservacoes": "PAGAMENTO TARIFA",
                        "cSituacao": "Conciliado",
                    }
                ]
            ),
        )
    )
    respx.post(OMIE_PAGAR_URL).mock(return_value=httpx.Response(200, json=_empty_pagar()))
    respx.post(OMIE_RECEBER_URL).mock(return_value=httpx.Response(200, json=_empty_receber()))

    # Anthropic nem deveria ser chamada — mas se for, levanta.
    class _BoomAnthropic:
        class messages:  # noqa: N801  — mimic SDK API
            @staticmethod
            async def create(**_: Any) -> Any:
                raise AssertionError("Anthropic não deveria ser chamada com flag off.")

    from app.integrations.anthropic.client import AnthropicClient

    monkeypatch.setattr(AnthropicClient, "_get_client", lambda self: _BoomAnthropic())

    await run_reconciliation_processing(
        str(session_id), settings=get_settings(), session_factory=factory
    )

    async with factory() as s:
        sess = (
            await s.execute(
                select(ReconciliationSession).where(ReconciliationSession.id == session_id)
            )
        ).scalar_one()
        assert sess.status == "reviewing"
        rows = (
            await s.execute(
                select(ReconciliationAnomaly, AnomalyType)
                .join(AnomalyType, ReconciliationAnomaly.anomaly_type_id == AnomalyType.id)
                .where(ReconciliationAnomaly.session_id == session_id)
            )
        ).all()
        codes = [atype.code for _, atype in rows]
        # Não pode aparecer NENHUM code de qualificação.
        for c in (
            ANOMALY_CODE_QUALIF_SUSPEITA,
            ANOMALY_CODE_QUALIF_INCOERENTE,
            ANOMALY_CODE_PADRAO_QUEBRADO,
            ANOMALY_CODE_VALOR_OUTLIER,
        ):
            assert c not in codes


@pytest.mark.integration
@respx.mock
async def test_pipeline_survives_anthropic_failure(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Anthropic explode 5xx → matching segue, sessão fica reviewing."""
    monkeypatch.setenv("QUALIFICATION_ENABLED", "true")
    get_settings.cache_clear()

    await _seed_all_anomaly_types(factory)
    admin = await _seed_admin(factory, "pipeline-q-fail@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cli Pipeline Q Fail")
    session_id = await _seed_session(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        transactions=[(date(2026, 4, 5), "PAGAMENTO TARIFA", Decimal("-100.00"))],
        file_hash=_hex64("pipeline-q-fail"),
    )

    _setup_omie_mocks_for_one_match()

    # Anthropic levanta sempre — qualquer exceção é OK (try/except externo).
    class _BoomAnthropic:
        class messages:  # noqa: N801
            @staticmethod
            async def create(**_: Any) -> Any:
                raise RuntimeError("simulated 5xx persistente Anthropic")

    from app.integrations.anthropic.client import AnthropicClient

    monkeypatch.setattr(AnthropicClient, "_get_client", lambda self: _BoomAnthropic())

    await run_reconciliation_processing(
        str(session_id), settings=get_settings(), session_factory=factory
    )

    async with factory() as s:
        sess = (
            await s.execute(
                select(ReconciliationSession).where(ReconciliationSession.id == session_id)
            )
        ).scalar_one()
        # Matching foi bem-sucedido apesar da falha do Anthropic.
        assert sess.status == "reviewing", sess.error_message
        assert sess.conciliated_count == 1


@pytest.mark.integration
@respx.mock
async def test_stage_is_written_at_each_boundary_and_never_blocks_the_write_step(
    factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """86e3mpmcw — a etapa é gravada ANTES de cada passo e nunca trava o passo 4.

    Dentro do passo 4 a qualificação escreve na linha da sessão
    (`qualification_used_glossary`) e o fim do passo grava os contadores. Uma
    gravação de etapa por OUTRA conexão nesse trecho esperaria o lock da linha
    até o commit da própria transação do job: o processamento nunca terminaria. O
    `asyncio.timeout` transforma esse travamento em falha do teste, e a ordem
    dos eventos prova que cada etapa foi gravada na fronteira do passo.
    """
    import asyncio

    from app.modules.reconciliations import repository as repo_module
    from app.modules.reconciliations.processing import job as job_module

    monkeypatch.setenv("QUALIFICATION_ENABLED", "true")
    get_settings.cache_clear()

    await _seed_all_anomaly_types(factory)
    admin = await _seed_admin(factory, "pipeline-stage@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cli Pipeline Etapa")
    session_id = await _seed_session(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        transactions=[(date(2026, 4, 5), "PAGAMENTO TARIFA", Decimal("-100.00"))],
        file_hash=_hex64("pipeline-stage"),
    )
    _setup_omie_mocks_for_one_match()

    # A IA responde e a qualificação roda de verdade (escreve na linha da sessão).
    async with factory() as s:
        entry_id = (
            await s.execute(
                select(ReconciliationFileEntry.id).where(
                    ReconciliationFileEntry.session_id == session_id
                )
            )
        ).scalar_one()
    fake = _FakeAnthropic(side_effect=_semantic_response_incoerente(str(entry_id)))
    from app.integrations.anthropic.client import AnthropicClient

    monkeypatch.setattr(AnthropicClient, "_get_client", lambda self: fake)

    events: list[str] = []
    real_mark = job_module._mark_processing_stage
    real_fetch = job_module.fetch_realized
    real_match = job_module.match
    real_apply = repo_module.ReconciliationRepository.apply_matches
    real_counters = repo_module.ReconciliationRepository.update_session_after_matching

    async def mark(sid: UUID, stage: Any, sf: Any) -> None:
        events.append(f"etapa:{stage.value}")
        await real_mark(sid, stage, sf)

    async def fetch(*args: Any, **kwargs: Any) -> Any:
        events.append("passo:omie")
        return await real_fetch(*args, **kwargs)

    def do_match(*args: Any, **kwargs: Any) -> Any:
        events.append("passo:cruzamento")
        return real_match(*args, **kwargs)

    async def apply(self: Any, *args: Any, **kwargs: Any) -> Any:
        events.append("passo:gravacao")
        return await real_apply(self, *args, **kwargs)

    async def counters(self: Any, *args: Any, **kwargs: Any) -> Any:
        events.append("passo:contadores")
        return await real_counters(self, *args, **kwargs)

    monkeypatch.setattr(job_module, "_mark_processing_stage", mark)
    monkeypatch.setattr(job_module, "fetch_realized", fetch)
    monkeypatch.setattr(job_module, "match", do_match)
    monkeypatch.setattr(repo_module.ReconciliationRepository, "apply_matches", apply)
    monkeypatch.setattr(
        repo_module.ReconciliationRepository, "update_session_after_matching", counters
    )

    async with asyncio.timeout(60):
        await run_reconciliation_processing(
            str(session_id), settings=get_settings(), session_factory=factory
        )

    assert events == [
        "etapa:carregando",
        "etapa:omie",
        "passo:omie",
        "etapa:cruzamento",
        "passo:cruzamento",
        "etapa:gravacao",
        "passo:gravacao",
        "passo:contadores",
    ]
    async with factory() as s:
        sess = (
            await s.execute(
                select(ReconciliationSession).where(ReconciliationSession.id == session_id)
            )
        ).scalar_one()
        assert sess.status == "reviewing", sess.error_message
        # Sessão concluída guarda a última etapa (diagnóstico).
        assert sess.processing_stage == "gravacao"
        # A qualificação rodou dentro do passo 4 (é ela que trava a linha).
        anomalies = (
            (
                await s.execute(
                    select(AnomalyType.code)
                    .join(
                        ReconciliationAnomaly,
                        ReconciliationAnomaly.anomaly_type_id == AnomalyType.id,
                    )
                    .where(ReconciliationAnomaly.session_id == session_id)
                )
            )
            .scalars()
            .all()
        )
        assert ANOMALY_CODE_QUALIF_INCOERENTE in anomalies


@pytest.mark.integration
async def test_stage_is_not_written_on_a_session_that_left_processing(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    """A etapa só casa sessão em `processing`: a cancelada não volta a parecer viva."""
    from app.db.models import ProcessingStage
    from app.modules.reconciliations.repository import ReconciliationRepository

    admin = await _seed_admin(factory, "pipeline-stage-guard@hologram.com.br")
    cliente = await _seed_client(factory, admin.id, "Cli Etapa Guarda")
    session_id = await _seed_session(
        factory,
        client_id=cliente.id,
        created_by=admin.id,
        transactions=[(date(2026, 4, 5), "X", Decimal("-1.00"))],
        file_hash=_hex64("pipeline-stage-guard"),
    )
    async with factory() as s, s.begin():
        repo = ReconciliationRepository(s)
        assert await repo.mark_processing_stage(session_id, ProcessingStage.OMIE) is True
        await s.execute(
            ReconciliationSession.__table__.update()
            .where(ReconciliationSession.id == session_id)
            .values(status="error")
        )
        assert await repo.mark_processing_stage(session_id, ProcessingStage.GRAVACAO) is False
    async with factory() as s:
        stage = await s.scalar(
            select(ReconciliationSession.processing_stage).where(
                ReconciliationSession.id == session_id
            )
        )
    assert stage == "omie"
