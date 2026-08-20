"""Lista com filtros/paginação + detalhe de fonte única (Sprint 4 / BACK 04.3).

O teste central é `test_totalizadores_do_detalhe_batem_com_as_abas`: ele prova o
critério que o PRD chama de crítico — "os totalizadores/saldos vêm de FONTE
ÚNICA reutilizada pelas abas; não recalcular em paralelo". E
`test_acao_de_revisao_nao_derruba_o_contador` cobre a divergência que **existia
de fato** antes desta task: o recompute da revisão não contava
`conciliado_data_divergente`, então bastava o analista tocar em uma linha para o
contador da lista cair sozinho.

Cobre ainda: filtros combinados (conta + mês + status) com `total` calculado sob
os mesmos filtros, paginação, `total_files` por item e isolamento por carteira.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import hash_password
from app.db.models import (
    AnomalySeverity,
    AnomalyType,
    Client,
    ClientAssignment,
    FileEntrySituation,
    OmieEntryStatus,
    ReconciliationAnomaly,
    ReconciliationFile,
    ReconciliationFileEntry,
    ReconciliationFileStatus,
    ReconciliationOmieEntry,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserRole,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

ADMIN_EMAIL = "totals-admin@hologram.com.br"
MANAGER_A_EMAIL = "totals-mgr-a@hologram.com.br"
MANAGER_B_EMAIL = "totals-mgr-b@hologram.com.br"
PLAIN_PASSWORD = "Senh@ForteParaTeste#1"
FAKE_APP_KEY = "totals-app-key"
FAKE_APP_SECRET = "totals-app-secret"


def _hex64(salt: str) -> str:
    return hashlib.sha256(salt.encode()).hexdigest()


async def _seed_user(session: AsyncSession, *, email: str, role: UserRole) -> User:
    user = User(
        name="Totals User",
        email=email.lower(),
        password_hash=hash_password(PLAIN_PASSWORD),
        role=role.value,
        active=True,
    )
    session.add(user)
    await session.flush()
    return user


async def _seed_client(
    session: AsyncSession,
    *,
    name: str,
    creator: User,
    manager: User | None = None,
) -> Client:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct_key, iv_key = encrypt(FAKE_APP_KEY, hex_key)
    ct_secret, iv_secret = encrypt(FAKE_APP_SECRET, hex_key)
    client = Client(
        name=name,
        omie_app_key_encrypted=ct_key,
        omie_app_key_iv=iv_key,
        omie_app_secret_encrypted=ct_secret,
        omie_app_secret_iv=iv_secret,
        active=True,
        created_by=creator.id,
    )
    session.add(client)
    await session.flush()
    if manager is not None:
        session.add(
            ClientAssignment(client_id=client.id, user_id=manager.id, assigned_by=creator.id)
        )
        await session.flush()
    return client


async def _seed_session(
    session: AsyncSession,
    *,
    client: Client,
    creator: User,
    account_type: str = "checking",
    omie_conta_id: int = 42,
    reference_month: date = date(2026, 4, 1),
    status_value: str = ReconciliationStatus.REVIEWING.value,
    n_files: int = 1,
    created_at_offset: timedelta = timedelta(0),
    counts: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0),
) -> ReconciliationSession:
    total, conciliated, sem_omie, omie_sem_arquivo, anomalies = counts
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        account_type=account_type,
        omie_conta_id=omie_conta_id,
        reference_month=reference_month,
        date_tolerance_days=0,
        file_hash=None,
        status=status_value,
        created_at=datetime.now(UTC) + created_at_offset,
        total_file_entries=total,
        conciliated_count=conciliated,
        sem_omie_count=sem_omie,
        omie_sem_arquivo_count=omie_sem_arquivo,
        anomaly_count=anomalies,
    )
    session.add(sess)
    await session.flush()
    for i in range(n_files):
        session.add(
            ReconciliationFile(
                session_id=sess.id,
                file_hash=_hex64(f"{sess.id}-{i}"),
                status=ReconciliationFileStatus.PARSED.value,
            )
        )
    await session.flush()
    return sess


async def _seed_entry(
    session: AsyncSession,
    *,
    sess: ReconciliationSession,
    situation: str,
    amount: Decimal = Decimal("-10.00"),
    omie_lancamento_id: int | None = None,
    day: int = 5,
    description: str | None = None,
) -> ReconciliationFileEntry:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt(description or f"Movimento {situation} {day}", hex_key)
    entry = ReconciliationFileEntry(
        session_id=sess.id,
        transaction_date=date(2026, 4, day),
        description_encrypted=ct,
        description_iv=iv,
        amount=amount,
        situation=situation,
        omie_lancamento_id=omie_lancamento_id,
    )
    session.add(entry)
    await session.flush()
    return entry


async def _seed_omie_entry(
    session: AsyncSession, *, sess: ReconciliationSession, omie_id: int
) -> None:
    session.add(
        ReconciliationOmieEntry(
            session_id=sess.id,
            omie_lancamento_id=omie_id,
            transaction_date=date(2026, 4, 7),
            omie_status=OmieEntryStatus.ATRASADO.value,
        )
    )
    await session.flush()


async def _seed_anomaly(
    session: AsyncSession,
    *,
    sess: ReconciliationSession,
    code: str,
    severity: str = AnomalySeverity.CRITICAL.value,
    resolved: bool = False,
) -> None:
    atype = await session.scalar(select(AnomalyType).where(AnomalyType.code == code))
    if atype is None:
        atype = AnomalyType(
            code=code,
            name=code.replace("_", " ").title(),
            description="Seed de teste",
            severity=severity,
            active=True,
        )
        session.add(atype)
        await session.flush()
    session.add(
        ReconciliationAnomaly(
            session_id=sess.id,
            anomaly_type_id=atype.id,
            detected_by="ai",
            resolved=resolved,
        )
    )
    await session.flush()


async def _login(client: AsyncClient, email: str) -> None:
    resp = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PLAIN_PASSWORD}
    )
    assert resp.status_code == 200, resp.text


async def _total_of(client: AsyncClient, url: str) -> int:
    resp = await client.get(url)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["pagination"]["total"])


# ----------------------------------------------------------------------
# Fonte única: detalhe vs abas de revisão
# ----------------------------------------------------------------------


class TestFonteUnica:
    async def test_totalizadores_do_detalhe_batem_com_as_abas(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """O número do detalhe é o mesmo que cada aba mostra — sem 2º cálculo.

        A sessão tem PROPOSITALMENTE uma linha `conciliado_data_divergente`: é
        o caso em que os dois cálculos divergiam.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        await _seed_entry(
            db_session, sess=sess, situation=FileEntrySituation.CONCILIADO.value, day=1
        )
        await _seed_entry(
            db_session, sess=sess, situation=FileEntrySituation.CONCILIADO.value, day=2
        )
        await _seed_entry(
            db_session,
            sess=sess,
            situation=FileEntrySituation.CONCILIADO_DATA_DIVERGENTE.value,
            day=3,
        )
        await _seed_entry(db_session, sess=sess, situation=FileEntrySituation.SEM_OMIE.value, day=4)
        await _seed_entry(db_session, sess=sess, situation=FileEntrySituation.IGNORADO.value, day=5)
        await _seed_omie_entry(db_session, sess=sess, omie_id=9001)
        await _seed_omie_entry(db_session, sess=sess, omie_id=9002)
        await _seed_anomaly(db_session, sess=sess, code="missing_in_omie")
        await _login(client_with_db, ADMIN_EMAIL)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert detail.status_code == 200, detail.text
        d = detail.json()["data"]

        base = f"/api/v1/reconciliations/{sess.id}"
        total_movimentacoes = await _total_of(client_with_db, f"{base}/file-entries")
        conciliados = await _total_of(client_with_db, f"{base}/file-entries?situation=conciliado")
        divergentes = await _total_of(
            client_with_db, f"{base}/file-entries?situation=conciliado_data_divergente"
        )
        sem_omie = await _total_of(client_with_db, f"{base}/file-entries?situation=sem_omie")
        omie_sem_arquivo = await _total_of(client_with_db, f"{base}/omie-entries")
        anomalias = await _total_of(client_with_db, f"{base}/anomalies")

        assert d["total_file_entries"] == total_movimentacoes == 5
        # Conciliado inclui a linha com data divergente (casou por valor).
        assert d["conciliated_count"] == conciliados + divergentes == 3
        assert d["sem_omie_count"] == sem_omie == 1
        assert d["omie_sem_arquivo_count"] == omie_sem_arquivo == 2
        assert d["anomaly_count"] == anomalias == 1

    async def test_acao_de_revisao_nao_derruba_o_contador(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Regressão da divergência real: tocar numa linha não pode zerar nada.

        Antes da fonte única, o recompute da revisão contava só
        `situation='conciliado'` — a linha com data divergente sumia do
        `conciliated_count` no primeiro PATCH, sem nada ter mudado de fato.
        """
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        await _seed_entry(
            db_session,
            sess=sess,
            situation=FileEntrySituation.CONCILIADO_DATA_DIVERGENTE.value,
            omie_lancamento_id=555,
            day=3,
        )
        alvo = await _seed_entry(
            db_session, sess=sess, situation=FileEntrySituation.SEM_OMIE.value, day=4
        )
        await _login(client_with_db, ADMIN_EMAIL)

        antes = (await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")).json()["data"]
        assert antes["conciliated_count"] == 1

        patch = await client_with_db.patch(
            f"/api/v1/reconciliations/{sess.id}/file-entries/{alvo.id}",
            json={"user_action": "flag", "situation": "ignorado"},
        )
        assert patch.status_code == 200, patch.text

        depois = (await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")).json()["data"]
        # A linha divergente continua contando como conciliada.
        assert depois["conciliated_count"] == 1
        assert depois["sem_omie_count"] == 0
        assert depois["total_file_entries"] == 2

        # …e a LISTA (que lê as colunas materializadas) diz o mesmo que o detalhe.
        lista = await client_with_db.get(f"/api/v1/clients/{cliente.id}/reconciliations")
        assert lista.status_code == 200, lista.text
        item = next(i for i in lista.json()["data"] if i["id"] == str(sess.id))
        assert item["conciliated_count"] == depois["conciliated_count"]
        assert item["sem_omie_count"] == depois["sem_omie_count"]
        assert item["total_file_entries"] == depois["total_file_entries"]


# ----------------------------------------------------------------------
# Lista: filtros, paginação, nº de arquivos
# ----------------------------------------------------------------------


class TestListaDeConciliacoes:
    async def test_filtro_de_status_usa_vocabulario_do_produto(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """`processed` cobre `reviewing` E `done` — é uma coisa só para quem opera."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        em_processamento = await _seed_session(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=1,
            status_value=ReconciliationStatus.PROCESSING.value,
        )
        revisando = await _seed_session(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=2,
            status_value=ReconciliationStatus.REVIEWING.value,
        )
        concluida = await _seed_session(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=3,
            status_value=ReconciliationStatus.DONE.value,
        )
        com_erro = await _seed_session(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=4,
            status_value=ReconciliationStatus.ERROR.value,
        )
        await _login(client_with_db, ADMIN_EMAIL)
        base = f"/api/v1/clients/{cliente.id}/reconciliations"

        async def _ids(query: str) -> set[str]:
            resp = await client_with_db.get(f"{base}?{query}")
            assert resp.status_code == 200, resp.text
            return {i["id"] for i in resp.json()["data"]}

        assert await _ids("status=processing") == {str(em_processamento.id)}
        assert await _ids("status=processed") == {str(revisando.id), str(concluida.id)}
        assert await _ids("status=error") == {str(com_erro.id)}

    async def test_status_invalido_retorna_400(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/clients/{cliente.id}/reconciliations?status=reviewing"
        )
        assert resp.status_code == 400, resp.text

    async def test_filtros_combinam_com_e_e_total_respeita_os_filtros(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """`total` tem de ser a contagem SOB os filtros — senão o rodapé mente."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        alvo = await _seed_session(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=100,
            reference_month=date(2026, 4, 1),
            status_value=ReconciliationStatus.REVIEWING.value,
        )
        # Mesma conta, mês diferente.
        await _seed_session(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=100,
            reference_month=date(2026, 3, 1),
            status_value=ReconciliationStatus.REVIEWING.value,
        )
        # Mesmo mês, conta diferente.
        await _seed_session(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=200,
            reference_month=date(2026, 4, 1),
            status_value=ReconciliationStatus.REVIEWING.value,
        )
        # Mesma conta e mês, status diferente (soft-delete libera a unicidade).
        outra = await _seed_session(
            db_session,
            client=cliente,
            creator=admin,
            omie_conta_id=300,
            reference_month=date(2026, 4, 1),
            status_value=ReconciliationStatus.ERROR.value,
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(
            f"/api/v1/clients/{cliente.id}/reconciliations"
            "?omie_conta_id=100&month=2026-04&status=processed"
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pagination"]["total"] == 1
        assert [i["id"] for i in body["data"]] == [str(alvo.id)]
        assert str(outra.id) not in {i["id"] for i in body["data"]}

    async def test_item_traz_numero_de_arquivos(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        tres = await _seed_session(
            db_session, client=cliente, creator=admin, omie_conta_id=1, n_files=3
        )
        uma = await _seed_session(
            db_session, client=cliente, creator=admin, omie_conta_id=2, n_files=1
        )
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/clients/{cliente.id}/reconciliations")
        assert resp.status_code == 200, resp.text
        by_id = {i["id"]: i for i in resp.json()["data"]}
        assert by_id[str(tres.id)]["total_files"] == 3
        assert by_id[str(uma.id)]["total_files"] == 1

    async def test_paginacao_teto_de_100(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        await _login(client_with_db, ADMIN_EMAIL)
        base = f"/api/v1/clients/{cliente.id}/reconciliations"

        assert (await client_with_db.get(f"{base}?pageSize=100")).status_code == 200
        assert (await client_with_db.get(f"{base}?pageSize=101")).status_code == 400

    async def test_manager_de_outra_carteira_nao_ve_a_lista(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        mgr_a = await _seed_user(db_session, email=MANAGER_A_EMAIL, role=UserRole.MANAGER)
        await _seed_user(db_session, email=MANAGER_B_EMAIL, role=UserRole.MANAGER)
        cliente = await _seed_client(db_session, name="Austral", creator=admin, manager=mgr_a)
        await _seed_session(db_session, client=cliente, creator=admin)
        await _login(client_with_db, MANAGER_B_EMAIL)

        resp = await client_with_db.get(f"/api/v1/clients/{cliente.id}/reconciliations")
        assert resp.status_code in {403, 404}, resp.text


# ----------------------------------------------------------------------
# Detalhe: erro e saldos
# ----------------------------------------------------------------------


class TestDetalheDaConciliacao:
    async def test_detalhe_de_sessao_em_erro_nao_vaza_linguagem_interna(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(
            db_session,
            client=cliente,
            creator=admin,
            status_value=ReconciliationStatus.ERROR.value,
        )
        sess.error_message = "Erro interno ao processar a conciliação. Tente novamente."
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert resp.status_code == 200, resp.text
        message = resp.json()["data"]["error_message"] or ""
        assert "token" not in message.lower()
        assert "traceback" not in message.lower()

    async def test_detalhe_traz_resumo_de_saldos(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Saldos vêm das colunas persistidas — a MESMA fonte que o export lê."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Austral", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        sess.balance_start = Decimal("1000.00")
        sess.balance_end_file = Decimal("1234.56")
        sess.balance_end_omie = Decimal("1200.00")
        sess.balance_difference = Decimal("34.56")
        await db_session.flush()
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert resp.status_code == 200, resp.text
        d = resp.json()["data"]
        assert Decimal(str(d["balance_start"])) == Decimal("1000.00")
        assert Decimal(str(d["balance_end_file"])) == Decimal("1234.56")
        assert Decimal(str(d["balance_end_omie"])) == Decimal("1200.00")
        assert Decimal(str(d["balance_difference"])) == Decimal("34.56")

    async def test_sessao_inexistente_retorna_404(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        await _login(client_with_db, ADMIN_EMAIL)

        resp = await client_with_db.get(f"/api/v1/reconciliations/{UUID(int=0)}")
        assert resp.status_code == 404


@pytest.mark.integration
class TestResumoSomasDaSessaoInteira:
    """86e2u513f — as somas do Resumo cobrem a sessão INTEIRA, no backend.

    Antes o front somava as 50 primeiras linhas em float; acima disso o total
    exibido era menor que o real — e a maioria dos extratos passa de 50.
    """

    async def test_somas_cobrem_mais_de_50_linhas(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """60 linhas: os totais batem com a soma REAL, não com as 50 primeiras."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Resumo Cheio", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        # 40 débitos de -7.37 e 20 créditos de +3.11 → o dia varia só para
        # espalhar; a soma certa é conhecida por construção.
        for i in range(40):
            await _seed_entry(
                db_session,
                sess=sess,
                situation=FileEntrySituation.CONCILIADO.value,
                amount=Decimal("-7.37"),
                day=(i % 28) + 1,
            )
        for i in range(20):
            await _seed_entry(
                db_session,
                sess=sess,
                situation=FileEntrySituation.SEM_OMIE.value,
                amount=Decimal("3.11"),
                day=(i % 28) + 1,
            )
        await _login(client_with_db, ADMIN_EMAIL)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        assert detail.status_code == 200, detail.text
        data = detail.json()["data"]
        # Decimal serializa como STRING (§3.4) — comparar como Decimal para
        # não depender de formatação.
        assert Decimal(data["credits_total"]) == Decimal("62.20")
        assert Decimal(data["debits_total"]) == Decimal("294.80")
        assert data["card_charges_total"] is None  # conta corrente não tem encargos

    async def test_breakdown_de_anomalias_da_sessao_inteira(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Resumo Anomalias", creator=admin)
        sess = await _seed_session(db_session, client=cliente, creator=admin)
        await _seed_anomaly(db_session, sess=sess, code="ta-critical", severity="critical")
        await _seed_anomaly(
            db_session, sess=sess, code="ta-critical", severity="critical", resolved=True
        )
        await _seed_anomaly(db_session, sess=sess, code="ta-moderate", severity="moderate")
        await _seed_anomaly(db_session, sess=sess, code="ta-info", severity="info", resolved=True)
        await _login(client_with_db, ADMIN_EMAIL)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        data = detail.json()["data"]
        assert data["anomalies_critical"] == 2
        assert data["anomalies_moderate"] == 1
        assert data["anomalies_info"] == 1
        assert data["anomalies_resolved"] == 2
        # e o total continua batendo com a mesma fonte
        assert data["anomaly_count"] == 4

    async def test_encargos_do_cartao_por_descricao(
        self, client_with_db: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Encargos = débitos com IOF/juros/multa na descrição (cifrada no banco)."""
        admin = await _seed_user(db_session, email=ADMIN_EMAIL, role=UserRole.ADMIN)
        cliente = await _seed_client(db_session, name="Resumo Cartao", creator=admin)
        sess = await _seed_session(
            db_session, client=cliente, creator=admin, account_type="credit_card"
        )
        await _seed_entry(
            db_session,
            sess=sess,
            situation=FileEntrySituation.CONCILIADO.value,
            amount=Decimal("-12.00"),
            description="IOF sobre compra internacional",
            day=1,
        )
        await _seed_entry(
            db_session,
            sess=sess,
            situation=FileEntrySituation.SEM_OMIE.value,
            amount=Decimal("-8.50"),
            description="JUROS DE MORA",
            day=2,
        )
        await _seed_entry(
            db_session,
            sess=sess,
            situation=FileEntrySituation.CONCILIADO.value,
            amount=Decimal("-100.00"),
            description="Posto Shell",
            day=3,
        )
        # Estorno com "juros" na descrição é CRÉDITO — não entra nos encargos.
        await _seed_entry(
            db_session,
            sess=sess,
            situation=FileEntrySituation.CONCILIADO.value,
            amount=Decimal("5.00"),
            description="Estorno juros cobrados indevidamente",
            day=4,
        )
        await _login(client_with_db, ADMIN_EMAIL)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sess.id}")
        data = detail.json()["data"]
        assert Decimal(data["card_charges_total"]) == Decimal("20.50")
        assert Decimal(data["debits_total"]) == Decimal("120.50")
        assert Decimal(data["credits_total"]) == Decimal("5.00")
