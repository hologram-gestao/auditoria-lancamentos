"""Testes de integração do job de processamento (S10 / BACK 8.2-8.5).

Aborda: chamar `run_reconciliation_processing(session_id, ...)` direto, com
o Omie mockado por `respx`. Assert no resultado persistido (file_entries
matched, omie_entries inseridos, anomalies, status='reviewing').

Cenários cobertos:
    - Caminho feliz: 2 file_entries x 2 lançamentos Omie + 1 título
      Atrasado sobrando → 2 matches + 1 omie_entry + 1 anomaly
      `missing_in_file`.
    - Falha no Omie (auth) → status='error' com mensagem PT-BR.
    - File entry sem correspondente → anomaly `missing_in_omie`.
    - `Previsto` não vira anomaly (mesmo que vire omie_entry sem match).

NÃO cobre o agendamento via BackgroundTasks — o `run_reconciliation_processing`
é chamado diretamente, com `settings` + `session_factory` de teste.
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
from app.core.crypto import decrypt, encrypt
from app.core.security import hash_password
from app.db.models import (
    AnomalySeverity,
    AnomalyType,
    Client,
    FileEntrySituation,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
    User,
    UserRole,
)
from app.modules.reconciliations.processing.anomalies import (
    ANOMALY_CODE_MISSING_IN_FILE,
    ANOMALY_CODE_MISSING_IN_OMIE,
    ANOMALY_CODE_WRONG_DATE,
)
from app.modules.reconciliations.processing.job import run_reconciliation_processing

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


FAKE_APP_KEY = "test-app-key-12345"
FAKE_APP_SECRET = "test-app-secret-67890"

OMIE_EXTRATO_URL = "https://app.omie.com.br/api/v1/financas/extrato/"
OMIE_PAGAR_URL = "https://app.omie.com.br/api/v1/financas/contapagar/"
OMIE_RECEBER_URL = "https://app.omie.com.br/api/v1/financas/contareceber/"


# ----------------------------------------------------------------------
# Helpers para semear dados pelo session_factory (NÃO pelo db_session da
# fixture — o worker abre sua própria session, então precisamos commitar
# no DB de verdade pra ele enxergar).
# ----------------------------------------------------------------------


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


@pytest.fixture
async def factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """sessionmaker apontando pro DB do testcontainers — passado ao job."""
    return async_sessionmaker(
        db_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )


async def _seed_anomaly_types(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s, s.begin():
        for code, severity in (
            (ANOMALY_CODE_MISSING_IN_OMIE, AnomalySeverity.CRITICAL),
            (ANOMALY_CODE_MISSING_IN_FILE, AnomalySeverity.CRITICAL),
            (ANOMALY_CODE_WRONG_DATE, AnomalySeverity.MODERATE),
        ):
            existing = await s.scalar(select(AnomalyType).where(AnomalyType.code == code))
            if existing is None:
                s.add(
                    AnomalyType(
                        code=code,
                        name=code.replace("_", " ").title(),
                        description=f"Seed para teste — {code}",
                        severity=severity.value,
                        active=True,
                    )
                )


async def _seed_admin(factory: async_sessionmaker[AsyncSession], email: str) -> User:
    async with factory() as s, s.begin():
        existing = await s.scalar(select(User).where(User.email == email.lower()))
        if existing is not None:
            return existing
        user = User(
            name="Admin",
            email=email.lower(),
            password_hash=hash_password("Senh@ForteParaTeste#1"),
            role=UserRole.ADMIN.value,
            active=True,
        )
        s.add(user)
    async with factory() as s:
        return (await s.execute(select(User).where(User.email == email.lower()))).scalar_one()


async def _seed_client(
    factory: async_sessionmaker[AsyncSession], creator_id: UUID, name: str
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    async with factory() as s, s.begin():
        client = Client(
            name=name,
            omie_app_key_encrypted=ct_key,
            omie_app_key_iv=iv_key,
            omie_app_secret_encrypted=ct_secret,
            omie_app_secret_iv=iv_secret,
            active=True,
            created_by=creator_id,
        )
        s.add(client)
        await s.flush()
        client_id = client.id
    async with factory() as s:
        return (await s.execute(select(Client).where(Client.id == client_id))).scalar_one()


async def _seed_session_with_entries(
    factory: async_sessionmaker[AsyncSession],
    *,
    client_id: UUID,
    created_by: UUID,
    transactions: list[tuple[date, str, Decimal]],
    file_hash: str,
    account_type: str = "checking",
) -> UUID:
    """Cria uma sessão `processing` + entries pré-criptografados."""
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    async with factory() as s, s.begin():
        sess = ReconciliationSession(
            client_id=client_id,
            created_by=created_by,
            omie_conta_id=42,
            account_type=account_type,
            reference_month=date(2026, 4, 1),
            date_tolerance_days=0,
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


# ----------------------------------------------------------------------
# Mocks Omie
# ----------------------------------------------------------------------


def _ok_extrato_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Envelope de `ListarExtrato` — chave canônica `listaMovimentos`."""
    return {"listaMovimentos": items}


def _ok_pagar_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Resposta de `ListarContasPagar` — chave canônica `conta_pagar_cadastro`."""
    return {"conta_pagar_cadastro": items}


def _ok_receber_payload(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Resposta de `ListarContasReceber` — chave canônica `conta_receber_cadastro`."""
    return {"conta_receber_cadastro": items}


def _empty_pagar_payload() -> dict[str, Any]:
    return {"conta_pagar_cadastro": []}


def _empty_receber_payload() -> dict[str, Any]:
    return {"conta_receber_cadastro": []}


# ----------------------------------------------------------------------
# Cenários
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestJobHappyPath:
    @respx.mock
    async def test_two_matches_and_one_anomaly_missing_in_file(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """File: 2 transações; Extrato Omie: 2 lançamentos casando + 1 título
        ATRASADO sem correspondente no extrato → 2 matches + 1 anomaly.
        """
        await _seed_anomaly_types(factory)
        admin = await _seed_admin(factory, "job-happy-admin@hologram.com.br")
        cliente = await _seed_client(factory, admin.id, "Padaria Pão Quente")

        session_id = await _seed_session_with_entries(
            factory,
            client_id=cliente.id,
            created_by=admin.id,
            transactions=[
                (date(2026, 4, 5), "Pagamento Sicredi", Decimal("-100.00")),
                (date(2026, 4, 12), "Recebimento Cliente Z", Decimal("250.00")),
            ],
            file_hash=_hex64("happy-path"),
        )

        # Mock do extrato — 2 lançamentos que casam exatamente
        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(
                200,
                json=_ok_extrato_payload(
                    [
                        {
                            "nCodLancamento": 1001,
                            "cNatureza": "D",
                            "dDataLancamento": "05/04/2026",
                            "nValorDocumento": 100.00,
                            "cObservacoes": "Sicredi",
                            "cSituacao": "Conciliado",
                        },
                        {
                            "nCodLancamento": 1002,
                            "cNatureza": "C",
                            "dDataLancamento": "12/04/2026",
                            "nValorDocumento": 250.00,
                            "cObservacoes": "Cliente Z",
                            "cSituacao": "Conciliado",
                        },
                    ]
                ),
            )
        )

        # Mock contas pagar/receber — uma chamada por status (ATRASADO/AVENCER).
        # Pagar(ATRASADO): 1 título sem correspondente → vira anomaly.
        # Pagar(AVENCER), Receber(ATRASADO+AVENCER): vazios.
        pagar_responses = [
            httpx.Response(
                200,
                json=_ok_pagar_payload(
                    [
                        {
                            "codigo_lancamento_omie": 9999,
                            "data_vencimento": "20/04/2026",
                            "valor_documento": 333.00,
                            "codigo_cliente_fornecedor": 5001,
                            "codigo_categoria": "OT",
                            "observacao": "Fornecedor Atrasado — Outros",
                            "status_titulo": "ATR",
                        }
                    ]
                ),
            ),
            httpx.Response(200, json=_empty_pagar_payload()),  # AVENCER vazio
        ]
        receber_responses = [
            httpx.Response(200, json=_empty_receber_payload()),  # ATRASADO
            httpx.Response(200, json=_empty_receber_payload()),  # AVENCER
        ]
        respx.post(OMIE_PAGAR_URL).mock(side_effect=pagar_responses)
        respx.post(OMIE_RECEBER_URL).mock(side_effect=receber_responses)

        # Run job
        await run_reconciliation_processing(
            str(session_id), settings=get_settings(), session_factory=factory
        )

        # Asserts
        async with factory() as s:
            sess = (
                await s.execute(
                    select(ReconciliationSession).where(ReconciliationSession.id == session_id)
                )
            ).scalar_one()
            assert sess.status == "reviewing", sess.error_message
            assert sess.conciliated_count == 2
            assert sess.sem_omie_count == 0
            assert sess.omie_sem_arquivo_count == 1
            assert sess.anomaly_count == 1
            assert sess.processed_at is not None

            # File entries: ambos com situation='conciliado'
            entries = (
                (
                    await s.execute(
                        select(ReconciliationFileEntry).where(
                            ReconciliationFileEntry.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert all(e.situation == FileEntrySituation.CONCILIADO.value for e in entries)
            assert {e.omie_lancamento_id for e in entries} == {1001, 1002}

            # Omie entry: 1 inserido (Atrasado), com status original.
            omie_rows = (
                (
                    await s.execute(
                        select(ReconciliationOmieEntry).where(
                            ReconciliationOmieEntry.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(omie_rows) == 1
            assert omie_rows[0].omie_lancamento_id == 9999
            # Normalizado para a forma canônica do DB (`OmieEntryStatus`).
            assert omie_rows[0].omie_status == "Atrasado"

            # Anomaly: 1 missing_in_file apontando pro omie_entry.
            anomalies = (
                (
                    await s.execute(
                        select(ReconciliationAnomaly).where(
                            ReconciliationAnomaly.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(anomalies) == 1
            assert anomalies[0].omie_entry_id == omie_rows[0].id
            assert anomalies[0].file_entry_id is None
            assert anomalies[0].detected_by == "ai"


@pytest.mark.integration
class TestJobDateDivergence:
    """FASE 1 (BACK 1.6) — regressão de conta corrente: a tolerância de data
    deixou de casar como `conciliado`. Agora numa única sessão:
        - data exata        → conciliado
        - 1-3 dias divergência → conciliado_data_divergente + anomalia wrong_date
        - sem candidato     → sem_omie + anomalia missing_in_omie
    """

    @respx.mock
    async def test_exact_divergent_and_unmatched_in_one_session(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_anomaly_types(factory)
        admin = await _seed_admin(factory, "job-divergence-admin@hologram.com.br")
        cliente = await _seed_client(factory, admin.id, "Conta Corrente X")

        session_id = await _seed_session_with_entries(
            factory,
            client_id=cliente.id,
            created_by=admin.id,
            transactions=[
                (date(2026, 4, 5), "Exata", Decimal("-100.00")),  # mesmo dia
                (date(2026, 4, 10), "Divergente", Decimal("-200.00")),  # Omie +2 dias
                (date(2026, 4, 20), "Sem match", Decimal("-300.00")),  # nada no Omie
            ],
            file_hash=_hex64("divergence"),
        )

        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(
                200,
                json=_ok_extrato_payload(
                    [
                        {
                            "nCodLancamento": 2001,
                            "cNatureza": "D",
                            "dDataLancamento": "05/04/2026",  # exato
                            "nValorDocumento": 100.00,
                            "cObservacoes": "Exata",
                            "cSituacao": "Conciliado",
                        },
                        {
                            "nCodLancamento": 2002,
                            "cNatureza": "D",
                            "dDataLancamento": "12/04/2026",  # +2 dias de 10/04
                            "nValorDocumento": 200.00,
                            "cObservacoes": "Divergente",
                            "cSituacao": "Conciliado",
                        },
                    ]
                ),
            )
        )
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=httpx.Response(200, json=_empty_pagar_payload())
        )
        respx.post(OMIE_RECEBER_URL).mock(
            return_value=httpx.Response(200, json=_empty_receber_payload())
        )

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
            # 2 casados (exato + divergente) contam como conciliated; 1 sem match.
            assert sess.conciliated_count == 2
            assert sess.sem_omie_count == 1
            # anomalias: 1 wrong_date (divergente) + 1 missing_in_omie (sem match).
            assert sess.anomaly_count == 2

            entries = (
                (
                    await s.execute(
                        select(ReconciliationFileEntry).where(
                            ReconciliationFileEntry.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_amount = {e.amount: e for e in entries}
            exata = by_amount[Decimal("-100.00")]
            divergente = by_amount[Decimal("-200.00")]
            sem_match = by_amount[Decimal("-300.00")]

            assert exata.situation == FileEntrySituation.CONCILIADO.value
            assert exata.omie_lancamento_id == 2001
            assert divergente.situation == FileEntrySituation.CONCILIADO_DATA_DIVERGENTE.value
            assert divergente.omie_lancamento_id == 2002
            assert sem_match.situation == FileEntrySituation.SEM_OMIE.value
            assert sem_match.omie_lancamento_id is None

            # Anomalias linkadas às linhas certas, pelo code do tipo.
            type_by_id = {
                t.id: t.code for t in (await s.execute(select(AnomalyType))).scalars().all()
            }
            anomalies = (
                (
                    await s.execute(
                        select(ReconciliationAnomaly).where(
                            ReconciliationAnomaly.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            code_by_file = {
                a.file_entry_id: type_by_id[a.anomaly_type_id]
                for a in anomalies
                if a.file_entry_id is not None
            }
            assert code_by_file[divergente.id] == ANOMALY_CODE_WRONG_DATE
            assert code_by_file[sem_match.id] == ANOMALY_CODE_MISSING_IN_OMIE

            # BACK 1.7 — contexto da anomalia wrong_date: data arquivo + data
            # Omie lado a lado, criptografado. Linha divergente: arquivo 10/04,
            # Omie 12/04.
            hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
            wd = next(a for a in anomalies if a.file_entry_id == divergente.id)
            assert wd.context_encrypted is not None
            assert wd.context_iv is not None
            assert (
                decrypt(wd.context_encrypted, wd.context_iv, hex_key)
                == "Data arquivo: 10/04/2026 · Data Omie: 12/04/2026"
            )
            # A anomalia missing_in_omie (sem match) não tem contexto.
            mio = next(a for a in anomalies if a.file_entry_id == sem_match.id)
            assert mio.context_encrypted is None


@pytest.mark.integration
class TestJobCreditCard:
    """FASE 1 (BACK 1.7) — fatura de cartão usa a MESMA engine de cruzamento.

    Cobre: `account_type='credit_card'` classifica igual ao checking (item 1);
    parcelas são cruzadas individualmente, sem agrupar (particularidade); e
    lançamento Omie do cartão sem correspondente vai p/ omie_entries (item 5).
    """

    @respx.mock
    async def test_card_session_same_engine_with_parcelas(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        await _seed_anomaly_types(factory)
        admin = await _seed_admin(factory, "job-card-admin@hologram.com.br")
        cliente = await _seed_client(factory, admin.id, "Cartão ELO Austral")

        session_id = await _seed_session_with_entries(
            factory,
            client_id=cliente.id,
            created_by=admin.id,
            account_type="credit_card",
            transactions=[
                (date(2026, 4, 5), "Compra exata", Decimal("-100.00")),  # exata
                (date(2026, 4, 10), "Compra divergente", Decimal("-250.00")),  # Omie +2 dias
                # Compra parcelada 3x — 3 linhas na fatura, R$50 cada. O Omie
                # tem 1 lançamento pelo total (R$150). Não agrupa → 3x sem_omie.
                (date(2026, 4, 8), "Tênis 1/3", Decimal("-50.00")),
                (date(2026, 4, 9), "Tênis 2/3", Decimal("-50.00")),
                (date(2026, 4, 10), "Tênis 3/3", Decimal("-50.00")),
            ],
            file_hash=_hex64("card-parcelas"),
        )

        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(
                200,
                json=_ok_extrato_payload(
                    [
                        {
                            "nCodLancamento": 3001,
                            "cNatureza": "D",
                            "dDataLancamento": "05/04/2026",
                            "nValorDocumento": 100.00,
                            "cObservacoes": "Compra exata",
                            "cSituacao": "Conciliado",
                        },
                        {
                            "nCodLancamento": 3002,
                            "cNatureza": "D",
                            "dDataLancamento": "12/04/2026",  # +2 dias de 10/04
                            "nValorDocumento": 250.00,
                            "cObservacoes": "Compra divergente",
                            "cSituacao": "Conciliado",
                        },
                        {
                            "nCodLancamento": 3003,
                            "cNatureza": "D",
                            "dDataLancamento": "08/04/2026",
                            "nValorDocumento": 150.00,  # total da compra parcelada
                            "cObservacoes": "Tênis 3x",
                            "cSituacao": "Conciliado",
                        },
                    ]
                ),
            )
        )
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=httpx.Response(200, json=_empty_pagar_payload())
        )
        respx.post(OMIE_RECEBER_URL).mock(
            return_value=httpx.Response(200, json=_empty_receber_payload())
        )

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
            assert sess.account_type == "credit_card"
            # exata + divergente conciliadas; 3 parcelas sem_omie.
            assert sess.conciliated_count == 2
            assert sess.sem_omie_count == 3
            # 3 missing_in_omie (parcelas) + 1 wrong_date (divergente).
            assert sess.anomaly_count == 4

            entries = (
                (
                    await s.execute(
                        select(ReconciliationFileEntry).where(
                            ReconciliationFileEntry.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            by_amount: dict[Decimal, list[ReconciliationFileEntry]] = {}
            for e in entries:
                by_amount.setdefault(e.amount, []).append(e)

            assert by_amount[Decimal("-100.00")][0].situation == FileEntrySituation.CONCILIADO.value
            assert (
                by_amount[Decimal("-250.00")][0].situation
                == FileEntrySituation.CONCILIADO_DATA_DIVERGENTE.value
            )
            # As 3 parcelas (R$50) ficam sem_omie — sistema NÃO agrupa.
            parcelas = by_amount[Decimal("-50.00")]
            assert len(parcelas) == 3
            assert all(p.situation == FileEntrySituation.SEM_OMIE.value for p in parcelas)

            # Item 5: lançamento Omie do cartão sem correspondente (o total
            # R$150 da compra parcelada) vai p/ reconciliation_omie_entries.
            omie_rows = (
                (
                    await s.execute(
                        select(ReconciliationOmieEntry).where(
                            ReconciliationOmieEntry.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {o.omie_lancamento_id for o in omie_rows} == {3003}


@pytest.mark.integration
class TestJobMissingInOmie:
    @respx.mock
    async def test_unmatched_file_entry_creates_missing_in_omie_anomaly(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """File com 1 transação que não bate com nada no Omie → 1 anomaly
        `missing_in_omie`, situation='sem_omie'."""
        await _seed_anomaly_types(factory)
        admin = await _seed_admin(factory, "job-missingomie-admin@hologram.com.br")
        cliente = await _seed_client(factory, admin.id, "X")
        session_id = await _seed_session_with_entries(
            factory,
            client_id=cliente.id,
            created_by=admin.id,
            transactions=[(date(2026, 4, 10), "Sem correspondente", Decimal("999.00"))],
            file_hash=_hex64("missing-omie"),
        )

        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(200, json=_ok_extrato_payload([]))
        )
        respx.post(OMIE_PAGAR_URL).mock(
            return_value=httpx.Response(200, json=_empty_pagar_payload())
        )
        respx.post(OMIE_RECEBER_URL).mock(
            return_value=httpx.Response(200, json=_empty_receber_payload())
        )

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
            assert sess.sem_omie_count == 1
            assert sess.anomaly_count == 1

            anomalies = (
                (
                    await s.execute(
                        select(ReconciliationAnomaly).where(
                            ReconciliationAnomaly.session_id == session_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(anomalies) == 1
            assert anomalies[0].file_entry_id is not None
            assert anomalies[0].omie_entry_id is None


@pytest.mark.integration
class TestJobPrevistoNotAnomaly:
    @respx.mock
    async def test_previsto_omie_entry_persisted_but_not_anomaly(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Título previsto (AVENCER no filtro) sem correspondente: vira
        omie_entry (registro de divergência) MAS NÃO vira anomaly (Doc §13
        — esperado não estar)."""
        await _seed_anomaly_types(factory)
        admin = await _seed_admin(factory, "job-previsto-admin@hologram.com.br")
        cliente = await _seed_client(factory, admin.id, "X")
        session_id = await _seed_session_with_entries(
            factory,
            client_id=cliente.id,
            created_by=admin.id,
            transactions=[(date(2026, 4, 5), "Mov", Decimal("-50.00"))],
            file_hash=_hex64("previsto"),
        )

        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(
                200,
                json=_ok_extrato_payload(
                    [
                        {
                            "nCodLancamento": 5001,
                            "cNatureza": "D",
                            "dDataLancamento": "05/04/2026",
                            "nValorDocumento": 50.00,
                            "cObservacoes": "Mov",
                            "cSituacao": "Conciliado",
                        }
                    ]
                ),
            )
        )
        # Pagar(AVENCER) devolve 1 título; (ATRASADO) vazio.
        respx.post(OMIE_PAGAR_URL).mock(
            side_effect=[
                httpx.Response(200, json=_empty_pagar_payload()),  # ATRASADO
                httpx.Response(
                    200,
                    json=_ok_pagar_payload(
                        [
                            {
                                "codigo_lancamento_omie": 7777,
                                "data_vencimento": "30/04/2026",
                                "valor_documento": 100.00,
                                "codigo_cliente_fornecedor": 7700,
                                "codigo_categoria": "SV",
                                "observacao": "Futuro — Serviços",
                                "status_titulo": "AVN",
                            }
                        ]
                    ),
                ),
            ]
        )
        respx.post(OMIE_RECEBER_URL).mock(
            return_value=httpx.Response(200, json=_empty_receber_payload())
        )

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
            assert sess.conciliated_count == 1
            assert sess.omie_sem_arquivo_count == 1  # AVENCER virou omie_entry
            assert sess.anomaly_count == 0  # mas NÃO virou anomaly


# ----------------------------------------------------------------------
# Falha do Omie → status='error'
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestJobOmieAuthError:
    @respx.mock
    async def test_omie_auth_failure_marks_session_error(
        self, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Omie retorna `faultstring` de auth → sessão fica `error` com
        mensagem PT-BR. Nenhuma file_entry foi alterada."""
        await _seed_anomaly_types(factory)
        admin = await _seed_admin(factory, "job-autherr-admin@hologram.com.br")
        cliente = await _seed_client(factory, admin.id, "X")
        session_id = await _seed_session_with_entries(
            factory,
            client_id=cliente.id,
            created_by=admin.id,
            transactions=[(date(2026, 4, 5), "Mov", Decimal("100.00"))],
            file_hash=_hex64("auth-err"),
        )

        # Omie devolve 200 + faultstring com keyword de auth (CLAUDE.md §7 client.py)
        respx.post(OMIE_EXTRATO_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "faultstring": "App_Key inválida",
                    "faultcode": "SOAP-ENV:Client-101",
                },
            )
        )

        await run_reconciliation_processing(
            str(session_id), settings=get_settings(), session_factory=factory
        )

        async with factory() as s:
            sess = (
                await s.execute(
                    select(ReconciliationSession).where(ReconciliationSession.id == session_id)
                )
            ).scalar_one()
            assert sess.status == "error"
            assert sess.error_message is not None
            assert "Credenciais" in sess.error_message  # PT-BR

            # File entry não foi alterada.
            entry = (
                await s.execute(
                    select(ReconciliationFileEntry).where(
                        ReconciliationFileEntry.session_id == session_id
                    )
                )
            ).scalar_one()
            assert entry.situation == FileEntrySituation.SEM_OMIE.value
            assert entry.omie_lancamento_id is None
