"""Lote de lançamento no Omie — `POST /reconciliations/{id}/omie-postings` (BACK 07.4).

⚠️ **É a única escrita do ADL no ERP do cliente.** O guardrail da sprint é zero
lançamento duplicado, e o critério de rollback é "um único duplicado desliga o
recurso" — então a maior parte destes testes é sobre o que o endpoint **se
recusa** a fazer.

A Omie é o `MockOmieClient` (credencial com prefixo `FAKE_DEMO_OMIE_`, o mesmo
caminho de dev): o percurso rota → service → repositório → banco é o de
produção, e o comportamento do fornecedor é controlado por `monkeypatch`.

**O que estes testes NÃO provam:** que o contrato do `IncluirLancCC` está certo.
Isso é S-1 e só a fixture real da BACK 07.1 responde — um mock que repete os
nomes assumidos confirmaria a invenção (defeito P11).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.exceptions import OmieFaultError, OmieTimeoutError
from app.core.security import hash_password
from app.db.models import (
    AnomalyDetectedBy,
    AnomalySeverity,
    AnomalyType,
    Client,
    ClientAssignment,
    FileEntrySituation,
    OmiePostingStatus,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
    ReconciliationSession,
    ReconciliationStatus,
    SessionAccountType,
    User,
    UserRole,
    UserScope,
)
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX, MockOmieClient
from app.integrations.omie.schemas import IncluirLancCCResponse, LancamentoExtrato
from app.modules.reconciliations.omie_posting.keys import derive_cod_int_lanc
from app.modules.reconciliations.processing.anomalies import ANOMALY_CODE_MISSING_IN_OMIE

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PLAIN_PASSWORD = "Senh@Lancamento#1"
POSTING_URL = "/api/v1/reconciliations/{session_id}/omie-postings"


# ----------------------------------------------------------------------
# Seeds
# ----------------------------------------------------------------------


async def _seed_user(
    session: AsyncSession,
    *,
    email: str,
    role: UserRole = UserRole.ADMIN,
    scope: UserScope = UserScope.SYSTEM,
    client_id: object = None,
) -> User:
    user = User(
        name="Lancamento",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
        scope=scope.value,
        client_id=client_id,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(session: AsyncSession, *, creator: User, name: str) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_k, iv_k = encrypt(f"{FAKE_DEMO_KEY_PREFIX}{uuid4().hex[:8]}", hex_key)
    ct_s, iv_s = encrypt("demo-secret", hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_k,
        omie_app_key_iv=iv_k,
        omie_app_secret_encrypted=ct_s,
        omie_app_secret_iv=iv_s,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    return client


async def _seed_session(
    session: AsyncSession,
    *,
    client: Client,
    creator: User,
    account_type: str = SessionAccountType.CREDIT_CARD.value,
    omie_conta_id: int = 900_000_003,
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=omie_conta_id,
        account_type=account_type,
        reference_month=date(2026, 4, 1),
        date_tolerance_days=0,
        file_hash=None,
        status=ReconciliationStatus.REVIEWING.value,
    )
    session.add(sess)
    await session.flush()
    return sess


async def _seed_entry(
    session: AsyncSession,
    *,
    sess: ReconciliationSession,
    amount: str = "-12.00",
    day: int = 15,
    situation: str = FileEntrySituation.SEM_OMIE.value,
    description: str = "CAFETERIA DO LARGO",
) -> ReconciliationFileEntry:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt(description, hex_key)
    entry = ReconciliationFileEntry(
        session_id=sess.id,
        transaction_date=date(2026, 4, day),
        description_encrypted=ct,
        description_iv=iv,
        amount=Decimal(amount),
        situation=situation,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _seed_missing_in_omie_anomaly(
    session: AsyncSession,
    *,
    sess: ReconciliationSession,
    entry: ReconciliationFileEntry,
) -> ReconciliationAnomaly:
    anomaly_type = (
        await session.execute(
            select(AnomalyType).where(AnomalyType.code == ANOMALY_CODE_MISSING_IN_OMIE)
        )
    ).scalar_one_or_none()
    if anomaly_type is None:
        anomaly_type = AnomalyType(
            code=ANOMALY_CODE_MISSING_IN_OMIE,
            name="Sem lançamento no Omie",
            description="Linha existe no arquivo e não existe no Omie.",
            severity=AnomalySeverity.CRITICAL.value,
            active=True,
        )
        session.add(anomaly_type)
        await session.flush()
    anomaly = ReconciliationAnomaly(
        session_id=sess.id,
        anomaly_type_id=anomaly_type.id,
        file_entry_id=entry.id,
        detected_by=AnomalyDetectedBy.AI.value,
        resolved=False,
    )
    session.add(anomaly)
    await session.flush()
    return anomaly


class Scenario(NamedTuple):
    admin: User
    client: Client
    session: ReconciliationSession


@pytest.fixture
async def scenario(db_session: AsyncSession) -> Scenario:
    admin = await _seed_user(db_session, email=f"lanc-{uuid4().hex[:8]}@hologram.com.br")
    client = await _seed_client(db_session, creator=admin, name=f"Cli {uuid4().hex[:6]}")
    sess = await _seed_session(db_session, client=client, creator=admin)
    return Scenario(admin, client, sess)


@pytest.fixture
def posting_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Liga o kill-switch. O default é `False` **de propósito** (ADR-027-BE)."""
    monkeypatch.setenv("OMIE_POSTING_ENABLED", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


def _body(*pairs: tuple[UUID, str]) -> dict:
    return {
        "lines": [
            {"file_entry_id": str(entry_id), "cod_categoria": categoria}
            for entry_id, categoria in pairs
        ]
    }


# ----------------------------------------------------------------------
# Guardas do lote
# ----------------------------------------------------------------------


@pytest.mark.integration
class TestKillSwitchAndEligibility:
    async def test_disabled_by_default_returns_a_handled_error(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """Sem `OMIE_POSTING_ENABLED`, nada é lançado — e não é 500."""
        get_settings.cache_clear()
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((entry.id, "1.01.01")),
        )

        assert resp.status_code == 409, resp.text
        assert resp.status_code < 500
        assert "desativado" in resp.json()["error"]["userMessage"]
        assert await _posting_count(db_session, entry.id) == 0

    @pytest.mark.usefixtures("posting_enabled")
    async def test_checking_account_session_is_refused_with_a_business_error(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Só cartão (`credit_card` = `CR` no Omie — ADR-020-BE). Conta corrente
        é 4xx com motivo, nunca 500 nem um lote vazio silencioso."""
        admin = await _seed_user(db_session, email=f"cc-{uuid4().hex[:8]}@hologram.com.br")
        client = await _seed_client(db_session, creator=admin, name="Cli CC")
        sess = await _seed_session(
            db_session,
            client=client,
            creator=admin,
            account_type=SessionAccountType.CHECKING.value,
        )
        entry = await _seed_entry(db_session, sess=sess)
        await _login(client_with_db, admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=sess.id), json=_body((entry.id, "1.01.01"))
        )

        assert resp.status_code == 400, resp.text
        assert "cartão" in resp.json()["error"]["userMessage"]
        assert await _posting_count(db_session, entry.id) == 0

    @pytest.mark.usefixtures("posting_enabled")
    async def test_batch_over_the_server_cap_is_refused(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """O teto é do SERVIDOR — o cliente não escolhe o tamanho do lote."""
        monkey_cap = 2
        get_settings.cache_clear()
        entries = [
            await _seed_entry(db_session, sess=scenario.session, day=d) for d in (10, 11, 12)
        ]
        await _login(client_with_db, scenario.admin.email)

        import os

        os.environ["OMIE_POSTING_MAX_BATCH"] = str(monkey_cap)
        get_settings.cache_clear()
        try:
            resp = await client_with_db.post(
                POSTING_URL.format(session_id=scenario.session.id),
                json=_body(*[(e.id, "1.01.01") for e in entries]),
            )
        finally:
            os.environ.pop("OMIE_POSTING_MAX_BATCH", None)
            get_settings.cache_clear()

        assert resp.status_code == 400, resp.text
        for entry in entries:
            assert await _posting_count(db_session, entry.id) == 0

    @pytest.mark.usefixtures("posting_enabled")
    async def test_blank_category_is_refused_by_the_contract(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """Categoria em branco chegaria à Omie como categoria inexistente."""
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((entry.id, "   ")),
        )
        # 400, não 422: o handler global do repo mapeia RequestValidationError
        # para VALIDATION_ERROR/400 (§9 do PLANO). Inventar 422 só nesta rota
        # quebraria o tratamento de erro do front, que é único.
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.usefixtures("posting_enabled")
    async def test_same_line_twice_in_one_batch_is_refused(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((entry.id, "1.01.01"), (entry.id, "2.02.01")),
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


# ----------------------------------------------------------------------
# Caminho feliz e reflexo na conciliação
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("posting_enabled")
class TestHappyPath:
    async def test_line_is_posted_and_reflected_on_the_reconciliation(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        anomaly = await _seed_missing_in_omie_anomaly(
            db_session, sess=scenario.session, entry=entry
        )
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((entry.id, "2.01.03")),
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["lancadas"] == 1
        assert data["bloqueadas"] == data["com_erro"] == 0
        line = data["lines"][0]
        assert line["status"] == "lancada"
        assert line["omie_lancamento_id"] is not None

        await db_session.refresh(entry)
        assert entry.situation == FileEntrySituation.CONCILIADO.value
        assert entry.omie_lancamento_id == line["omie_lancamento_id"]

        await db_session.refresh(anomaly)
        assert anomaly.resolved is True, "a anomalia missing_in_omie ficou pendente para sempre"

        await db_session.refresh(scenario.session)
        assert scenario.session.sem_omie_count == 0
        assert scenario.session.conciliated_count == 1

    async def test_two_identical_purchases_produce_two_postings(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """Mesma data, mesmo valor, mesma descrição: DOIS lançamentos.

        O caso do "dinheiro faltando" — uma chave por conteúdo colapsaria as
        duas e a segunda nunca entraria, sem o rollback perceber.
        """
        first = await _seed_entry(db_session, sess=scenario.session)
        second = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((first.id, "2.01.03"), (second.id, "2.01.03")),
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["lancadas"] == 2
        ids = {line["omie_lancamento_id"] for line in data["lines"]}
        assert len(ids) == 2, "as duas compras idênticas viraram um lançamento só"


# ----------------------------------------------------------------------
# Não duplicar — o guardrail da sprint
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("posting_enabled")
class TestNeverPostsTwice:
    async def test_resending_the_same_line_does_not_create_a_second_posting(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """A prova é o ESTADO do ADL, não um mock que finge deduplicar."""
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        url = POSTING_URL.format(session_id=scenario.session.id)
        body = _body((entry.id, "2.01.03"))

        first = await client_with_db.post(url, json=body)
        second = await client_with_db.post(url, json=body)

        assert first.json()["data"]["lancadas"] == 1
        assert second.json()["data"]["lancadas"] == 0
        blocked = second.json()["data"]["lines"][0]
        assert blocked["status"] == "bloqueada"
        assert blocked["reason"] == "ja_lancada"
        assert await _posting_count(db_session, entry.id) == 1

    async def test_timeout_then_reexecution_reconciles_instead_of_duplicating(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """O caminho REAL do "único duplicado".

        1º POST: o Omie ACEITA, mas a resposta expira (timeout no transporte).
        Na reexecução, o ADL vê a intenção `pending` com tentativa anterior,
        consulta o extrato pelo `cCodIntLanc` — e encontra. Nenhum POST novo é
        feito, e a linha é reconciliada com o lançamento que já existe.
        """
        entry = await _seed_entry(db_session, sess=scenario.session)
        cod_int_lanc = derive_cod_int_lanc(entry.id)
        accepted_id = 951_234_567
        posts: list[str] = []

        async def timing_out_post(self: MockOmieClient, request: object) -> IncluirLancCCResponse:
            posts.append("post")
            raise OmieTimeoutError("Timeout após retries em IncluirLancCC")

        monkeypatch.setattr(MockOmieClient, "incluir_lanc_cc", timing_out_post)
        await _login(client_with_db, scenario.admin.email)
        url = POSTING_URL.format(session_id=scenario.session.id)
        body = _body((entry.id, "2.01.03"))

        first = await client_with_db.post(url, json=body)
        assert first.status_code >= 500, "timeout com zero sucessos tem de virar 5xx"
        assert len(posts) == 1

        # O Omie tinha aceitado: o lançamento aparece no extrato com a chave.
        async def extrato_with_the_entry(
            self: MockOmieClient, **_kwargs: object
        ) -> list[LancamentoExtrato]:
            return [
                LancamentoExtrato.model_validate(
                    {
                        "nCodLancamento": accepted_id,
                        "cNatureza": "D",
                        "dDataLancamento": "15/04/2026",
                        "nValorDocumento": Decimal("12.00"),
                        "cSituacao": "Conciliado",
                        "cCodIntLanc": cod_int_lanc,
                    }
                )
            ]

        monkeypatch.setattr(MockOmieClient, "listar_extrato", extrato_with_the_entry)

        second = await client_with_db.post(url, json=body)

        assert second.status_code == 200, second.text
        assert len(posts) == 1, "houve um SEGUNDO POST — lançamento duplicado no Omie"
        line = second.json()["data"]["lines"][0]
        assert line["status"] == "lancada"
        assert line["reason"] == "reconciliada"
        assert line["omie_lancamento_id"] == accepted_id
        await db_session.refresh(entry)
        assert entry.omie_lancamento_id == accepted_id

    async def test_inconclusive_reconciliation_never_resends(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Extrato sem `cCodIntLanc` (S-1): não dá para saber → NÃO reenvia.

        Se a Omie não devolver a chave de integração, "não achei" e "não sei
        olhar" são indistinguíveis. Reenviar aqui seria apostar o dinheiro do
        cliente numa suposição.
        """
        entry = await _seed_entry(db_session, sess=scenario.session)
        posts: list[str] = []

        async def timing_out_post(self: MockOmieClient, request: object) -> IncluirLancCCResponse:
            posts.append("post")
            raise OmieTimeoutError("Timeout após retries em IncluirLancCC")

        async def extrato_without_the_key(
            self: MockOmieClient, **_kwargs: object
        ) -> list[LancamentoExtrato]:
            return [
                LancamentoExtrato.model_validate(
                    {
                        "nCodLancamento": 70_999,
                        "cNatureza": "D",
                        "dDataLancamento": "15/04/2026",
                        "nValorDocumento": Decimal("12.00"),
                        "cSituacao": "Conciliado",
                    }
                )
            ]

        monkeypatch.setattr(MockOmieClient, "incluir_lanc_cc", timing_out_post)
        monkeypatch.setattr(MockOmieClient, "listar_extrato", extrato_without_the_key)
        await _login(client_with_db, scenario.admin.email)
        url = POSTING_URL.format(session_id=scenario.session.id)
        body = _body((entry.id, "2.01.03"))

        await client_with_db.post(url, json=body)
        second = await client_with_db.post(url, json=body)

        assert len(posts) == 1, "reenviou às cegas depois de um timeout"
        line = second.json()["data"]["lines"][0]
        assert line["status"] == "bloqueada"
        assert line["reason"] == "envio_anterior_sem_confirmacao"
        await db_session.refresh(entry)
        assert entry.omie_lancamento_id is None
        assert entry.situation == FileEntrySituation.SEM_OMIE.value


# ----------------------------------------------------------------------
# Erro do fornecedor
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("posting_enabled")
class TestProviderFault:
    async def test_faultstring_marks_nothing_as_posted(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Omie responde HTTP 200 em erro. Isso é falha DEFINITIVA da linha."""
        entry = await _seed_entry(db_session, sess=scenario.session)
        provider_message = "Ocorreu um erro ao acessar o Omie: Categoria [9.99] não cadastrada."

        async def faulting(self: MockOmieClient, request: object) -> IncluirLancCCResponse:
            raise OmieFaultError("Fault em IncluirLancCC", user_message=provider_message)

        monkeypatch.setattr(MockOmieClient, "incluir_lanc_cc", faulting)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((entry.id, "9.99")),
        )

        assert resp.status_code == 200, resp.text
        line = resp.json()["data"]["lines"][0]
        assert line["status"] == "erro"
        assert line["reason"] == "erro_omie"
        # A mensagem do provedor volta ao usuário — é o que a torna acionável.
        assert line["message"] == provider_message
        # ...e nenhuma credencial vaza junto.
        assert "app_key" not in resp.text
        assert "app_secret" not in resp.text

        await db_session.refresh(entry)
        assert entry.situation == FileEntrySituation.SEM_OMIE.value
        assert entry.omie_lancamento_id is None
        posting = await _posting(db_session, entry.id)
        assert posting is not None
        assert posting.status == OmiePostingStatus.FAILED.value
        assert posting.omie_lancamento_id is None

    async def test_a_failed_line_can_be_retried_without_duplicating_the_posted_ones(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """R5: linhas com erro seguem pendentes e reexecutáveis."""
        ok_entry = await _seed_entry(db_session, sess=scenario.session, day=10)
        bad_entry = await _seed_entry(db_session, sess=scenario.session, day=11)
        original = MockOmieClient.incluir_lanc_cc

        async def selective(self: MockOmieClient, request: object) -> IncluirLancCCResponse:
            if request.c_cod_categ == "BAD":  # type: ignore[attr-defined]
                raise OmieFaultError("Fault", user_message="Categoria inválida.")
            return await original(self, request)  # type: ignore[arg-type]

        monkeypatch.setattr(MockOmieClient, "incluir_lanc_cc", selective)
        await _login(client_with_db, scenario.admin.email)
        url = POSTING_URL.format(session_id=scenario.session.id)

        first = await client_with_db.post(
            url, json=_body((ok_entry.id, "2.01.03"), (bad_entry.id, "BAD"))
        )
        assert first.json()["data"] == {
            **first.json()["data"],
            "lancadas": 1,
            "com_erro": 1,
        }

        # Reexecução com a categoria corrigida: a linha boa NÃO é relançada.
        second = await client_with_db.post(
            url, json=_body((ok_entry.id, "2.01.03"), (bad_entry.id, "2.02.01"))
        )
        data = second.json()["data"]
        assert data["lancadas"] == 1
        assert data["bloqueadas"] == 1
        by_id = {line["file_entry_id"]: line for line in data["lines"]}
        assert by_id[str(ok_entry.id)]["reason"] == "ja_lancada"
        assert by_id[str(bad_entry.id)]["status"] == "lancada"
        assert await _posting_count(db_session, ok_entry.id) == 1


# ----------------------------------------------------------------------
# Elegibilidade por linha
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("posting_enabled")
class TestLineEligibility:
    @pytest.mark.parametrize(
        ("situation", "expected_reason"),
        [
            (FileEntrySituation.IGNORADO.value, "linha_ignorada"),
            (FileEntrySituation.CONCILIADO.value, "nao_e_sem_omie"),
            (FileEntrySituation.CONCILIADO_DATA_DIVERGENTE.value, "nao_e_sem_omie"),
        ],
    )
    async def test_non_eligible_lines_are_blocked_with_a_reason(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        situation: str,
        expected_reason: str,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session, situation=situation)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((entry.id, "2.01.03")),
        )

        assert resp.status_code == 200, resp.text
        line = resp.json()["data"]["lines"][0]
        assert line["status"] == "bloqueada"
        assert line["reason"] == expected_reason
        assert await _posting_count(db_session, entry.id) == 0

    async def test_line_from_another_session_is_not_posted(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """UUID de linha de outra conciliação no body não lança nada."""
        other_session = await _seed_session(
            db_session,
            client=scenario.client,
            creator=scenario.admin,
            omie_conta_id=900_000_004,
        )
        foreign = await _seed_entry(db_session, sess=other_session)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((foreign.id, "2.01.03")),
        )

        line = resp.json()["data"]["lines"][0]
        assert line["status"] == "bloqueada"
        assert line["reason"] == "linha_inexistente"
        assert await _posting_count(db_session, foreign.id) == 0


# ----------------------------------------------------------------------
# Autorização
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.usefixtures("posting_enabled")
class TestAuthorization:
    async def test_client_operator_of_the_tenant_can_post(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """`review_export` da matriz: quem revisa, lança (ADR-026-BE)."""
        operator = await _seed_user(
            db_session,
            email=f"op-{uuid4().hex[:8]}@cli.com.br",
            role=UserRole.CLIENT_OPERATOR,
            scope=UserScope.CLIENT,
            client_id=scenario.client.id,
        )
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, operator.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((entry.id, "2.01.03")),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["lancadas"] == 1

    async def test_manager_outside_the_portfolio_gets_404(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """404 e não 403 — a conversão anti-enumeração da S3, preservada."""
        outsider = await _seed_user(
            db_session,
            email=f"mgr-{uuid4().hex[:8]}@hologram.com.br",
            role=UserRole.MANAGER,
        )
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, outsider.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((entry.id, "2.01.03")),
        )
        assert resp.status_code == 404, resp.text
        assert await _posting_count(db_session, entry.id) == 0

    async def test_manager_inside_the_portfolio_can_post(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        """O contraponto do teste acima: sem ele, "404 sempre" passaria."""
        manager = await _seed_user(
            db_session,
            email=f"mgr-in-{uuid4().hex[:8]}@hologram.com.br",
            role=UserRole.MANAGER,
        )
        db_session.add(
            ClientAssignment(
                client_id=scenario.client.id,
                user_id=manager.id,
                assigned_by=scenario.admin.id,
            )
        )
        entry = await _seed_entry(db_session, sess=scenario.session)
        await db_session.flush()
        await _login(client_with_db, manager.email)

        resp = await client_with_db.post(
            POSTING_URL.format(session_id=scenario.session.id),
            json=_body((entry.id, "2.01.03")),
        )
        assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------
# Helpers de asserção
# ----------------------------------------------------------------------


async def _posting_count(db: AsyncSession, file_entry_id: UUID) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(ReconciliationOmiePosting)
            .where(ReconciliationOmiePosting.file_entry_id == file_entry_id)
        )
    ) or 0


async def _posting(db: AsyncSession, file_entry_id: UUID) -> ReconciliationOmiePosting | None:
    return (
        await db.execute(
            select(ReconciliationOmiePosting).where(
                ReconciliationOmiePosting.file_entry_id == file_entry_id
            )
        )
    ).scalar_one_or_none()
