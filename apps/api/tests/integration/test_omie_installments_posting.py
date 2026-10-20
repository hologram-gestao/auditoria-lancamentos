"""Parcelas FUTURAS de compra parcelada no Contas a Pagar (86e3qhhtb).

O medo do Gabriel é o critério: "tenho medo de criar o Contas a Pagar das parcelas
e duplicar". Estes testes travam, pela rota de produção e contra o banco:

- a fatura de setembro com `7/10` provisiona a 7 e as 8, 9 e 10, com vencimento no
  mesmo dia da compra nos meses seguintes; reenviar não cria nada;
- a fatura de outubro com `8/10` ENCONTRA o título da 8 (`ja_lancada`), sem título
  novo; no processo "na data da compra" só a baixa vai ao Omie;
- a fatura de novembro sem a `9/10` avisa (`parcela_nao_veio`, com o id do título)
  e nada é excluído;
- flag desligada: a sessão diz que a opção não existe e o pedido é recusado por
  linha, sem nada enviado.

A Omie é o `MockOmieClient`, idempotente pela chave como a real (captura de
13/10/2026), embrulhado para registrar o que foi enviado.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.crypto_service import (
    AAD_ANOMALY_CONTEXT,
    field_locator,
    load_client_cipher,
    provision_client_cipher,
)
from app.core.exceptions import OmieFaultError
from app.db.models import (
    AnomalySeverity,
    AnomalyType,
    CardPostingDateMode,
    FileEntrySituation,
    InstallmentStep,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmiePostingInstallment,
    ReconciliationSession,
    ReconciliationStatus,
    SessionAccountType,
)
from app.integrations.omie import mock_client
from app.integrations.omie.mock_client import MockOmieClient, reset_mock_payables_state
from app.modules.reconciliations.omie_posting.keys import derive_installment_payable_key
from app.modules.reconciliations.omie_posting.missing_installments import (
    ANOMALY_CODE_INSTALLMENT_MISSING,
    flag_missing_installments,
)
from tests.integration.test_omie_posting_endpoint import (
    POSTING_URL,
    _login,
    _seed_client,
    _seed_user,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.models import Client, User

pytestmark = pytest.mark.integration

#: CNPJ com cadastro no mock (seed).
CNPJ_FOUND = "12.345.678/0001-95"
CONTA = 900_000_003
AMOUNT = "-100.00"


# ----------------------------------------------------------------------
# Fixtures e helpers
# ----------------------------------------------------------------------


@pytest.fixture
def flags(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """As três escritas, por teste. Default: provisão e parcelas ligadas."""

    def _set(*, provisao: bool = True, parcelas: bool = True) -> None:
        monkeypatch.setenv("OMIE_POSTING_ENABLED", "false")
        monkeypatch.setenv("OMIE_PAYABLE_POSTING_ENABLED", "true" if provisao else "false")
        monkeypatch.setenv("OMIE_INSTALLMENT_POSTING_ENABLED", "true" if parcelas else "false")
        get_settings.cache_clear()

    _set()
    yield _set
    get_settings.cache_clear()


class Recorder:
    """Embrulha os métodos de escrita do mock e guarda cada request."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: dict[str, list[Any]] = {}
        self.fail_keys: dict[str, int] = {}
        for name in ("listar_clientes_por_cnpj", "incluir_conta_pagar", "lancar_pagamento"):
            self._wrap(monkeypatch, name)

    def _wrap(self, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        original = getattr(MockOmieClient, name)
        calls = self.calls.setdefault(name, [])
        recorder = self

        async def wrapper(self_: MockOmieClient, *args: Any, **kwargs: Any) -> Any:
            request = args[0] if args else kwargs
            key = getattr(request, "codigo_lancamento_integracao", None)
            if key is not None and recorder.fail_keys.get(key, 0) > 0:
                recorder.fail_keys[key] -= 1
                raise OmieFaultError(
                    "Fault: 101 - recusa",
                    user_message="Ocorreu um erro ao acessar o Omie: 101 - recusa",
                    metadata={"fault_code": "SOAP-ENV:Client-101"},
                )
            calls.append(request)
            return await original(self_, *args, **kwargs)

        monkeypatch.setattr(MockOmieClient, name, wrapper)

    def n(self, name: str) -> int:
        return len(self.calls.get(name, []))


@pytest.fixture
def omie(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorder]:
    monkeypatch.setattr(mock_client, "_DELAY_LISTAR_CONTAS_SECONDS", 0.0)
    reset_mock_payables_state()
    yield Recorder(monkeypatch)
    reset_mock_payables_state()


async def _actors(db: AsyncSession) -> tuple[User, Client]:
    admin = await _seed_user(db, email=f"parc-{uuid4().hex[:8]}@hologram.com.br")
    client = await _seed_client(db, creator=admin, name=f"Parc {uuid4().hex[:6]}")
    # DEK provisionada: o aviso da parcela que não veio cifra o contexto.
    await provision_client_cipher(client, settings=get_settings())
    await db.flush()
    return admin, client


async def _session(
    db: AsyncSession,
    *,
    client: Client,
    creator: User,
    month: int,
    mode: str = CardPostingDateMode.PURCHASE_DATE.value,
    due: date | None = None,
) -> ReconciliationSession:
    sess = ReconciliationSession(
        client_id=client.id,
        created_by=creator.id,
        omie_conta_id=CONTA,
        account_type=SessionAccountType.CREDIT_CARD.value,
        reference_month=date(2026, month, 1),
        date_tolerance_days=0,
        file_hash=None,
        status=ReconciliationStatus.REVIEWING.value,
        card_posting_date_mode=mode,
        invoice_due_date=due,
    )
    db.add(sess)
    await db.flush()
    return sess


async def _entry(
    db: AsyncSession,
    *,
    sess: ReconciliationSession,
    day: date,
    amount: str = AMOUNT,
    pair: tuple[int, int] | None = None,
    description: str = "LOJA PARCELADA",
) -> ReconciliationFileEntry:
    hex_key = get_settings().OMIE_ENCRYPTION_KEY.get_secret_value()
    ct, iv = encrypt(description, hex_key)
    entry = ReconciliationFileEntry(
        session_id=sess.id,
        transaction_date=day,
        description_encrypted=ct,
        description_iv=iv,
        amount=Decimal(amount),
        situation=FileEntrySituation.SEM_OMIE.value,
        installment_number=pair[0] if pair else None,
        installment_total=pair[1] if pair else None,
    )
    db.add(entry)
    await db.flush()
    return entry


def _line(entry_id: UUID, **extra: Any) -> dict[str, Any]:
    return {
        "file_entry_id": str(entry_id),
        "cod_categoria": "2.01.03",
        "cnpj_fornecedor": CNPJ_FOUND,
        **extra,
    }


async def _post(http: AsyncClient, sess: ReconciliationSession, *lines: dict[str, Any]) -> Any:
    resp = await http.post(POSTING_URL.format(session_id=sess.id), json={"lines": list(lines)})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["lines"]


async def _installments(
    db: AsyncSession, client_id: UUID
) -> list[ReconciliationOmiePostingInstallment]:
    return list(
        (
            await db.execute(
                select(ReconciliationOmiePostingInstallment)
                .where(ReconciliationOmiePostingInstallment.client_id == client_id)
                .order_by(ReconciliationOmiePostingInstallment.installment_number)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _ensure_missing_type(db: AsyncSession) -> None:
    """Get-or-create: o banco de teste nasce do metadata, sem o seed da migration."""
    exists = await db.scalar(
        select(AnomalyType.id).where(AnomalyType.code == ANOMALY_CODE_INSTALLMENT_MISSING)
    )
    if exists is None:
        db.add(
            AnomalyType(
                code=ANOMALY_CODE_INSTALLMENT_MISSING,
                name="Parcela provisionada que não veio na fatura",
                description="teste",
                severity=AnomalySeverity.MODERATE.value,
                active=True,
            )
        )
        await db.flush()


async def _september(
    http: AsyncClient,
    db: AsyncSession,
    *,
    admin: User,
    client: Client,
    mode: str = CardPostingDateMode.PURCHASE_DATE.value,
) -> tuple[ReconciliationSession, ReconciliationFileEntry, list[dict[str, Any]]]:
    """A fatura de setembro: `7/10` em 10/09, outra compra em 28/09, vencimento 05/10."""
    sep = await _session(
        db, client=client, creator=admin, month=9, mode=mode, due=date(2026, 10, 5)
    )
    line = await _entry(db, sess=sep, day=date(2026, 9, 10), pair=(7, 10))
    await _entry(db, sess=sep, day=date(2026, 9, 28), amount="-30.00", description="MERCADO")
    await _login(http, admin.email)
    lines = await _post(http, sep, _line(line.id, parcelas_futuras=True))
    return sep, line, lines


# ----------------------------------------------------------------------
# Critério 1: setembro cria a 7 e as 8, 9 e 10; reenviar não cria nada
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("flags")
class TestSetembroCriaAsParcelasFuturas:
    async def test_cria_a_parcela_corrente_e_as_tres_futuras(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        admin, client = await _actors(db_session)
        _sep, line, lines = await _september(client_with_db, db_session, admin=admin, client=client)

        result = lines[0]
        assert result["status"] == "lancada", result
        assert [p["numero"] for p in result["parcelas"]] == [8, 9, 10]
        assert [p["vencimento"] for p in result["parcelas"]] == [
            "2026-10-10",
            "2026-11-10",
            "2026-12-10",
        ]
        # Resposta (c): emissão = a fatura em que a parcela cai (05/10 + k meses).
        assert [p["emissao"] for p in result["parcelas"]] == [
            "2026-11-05",
            "2026-12-05",
            "2027-01-05",
        ]
        assert {p["status"] for p in result["parcelas"]} == {"provisionada"}
        # Título da 7 + 3 parcelas; a baixa só da 7 (processo "na data da compra").
        assert omie.n("incluir_conta_pagar") == 4
        assert omie.n("lancar_pagamento") == 1
        sent = {r.codigo_lancamento_integracao: r for r in omie.calls["incluir_conta_pagar"]}
        for n, due in ((8, "10/10/2026"), (9, "10/11/2026"), (10, "10/12/2026")):
            request = sent[derive_installment_payable_key(line.id, n)]
            assert request.data_vencimento == due
            assert request.valor_documento == Decimal("100.00")
            assert f"parcela {n} de 10" in (request.observacao or "")

        rows = await _installments(db_session, client.id)
        assert [(r.installment_number, r.step) for r in rows] == [
            (8, InstallmentStep.CONCLUIDO.value),
            (9, InstallmentStep.CONCLUIDO.value),
            (10, InstallmentStep.CONCLUIDO.value),
        ]
        assert [r.payable_id for r in rows] == [p["payable_id"] for p in result["parcelas"]]

    async def test_reenviar_nao_cria_nada_e_devolve_os_mesmos_ids(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        admin, client = await _actors(db_session)
        sep, line, first = await _september(client_with_db, db_session, admin=admin, client=client)
        calls_before = omie.n("incluir_conta_pagar")

        again = await _post(client_with_db, sep, _line(line.id, parcelas_futuras=True))

        assert again[0]["reason"] == "ja_lancada"
        assert [p["payable_id"] for p in again[0]["parcelas"]] == [
            p["payable_id"] for p in first[0]["parcelas"]
        ]
        assert omie.n("incluir_conta_pagar") == calls_before
        assert len(await _installments(db_session, client.id)) == 3

    async def test_a_revisao_mostra_o_par_as_futuras_e_a_serie(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        admin, client = await _actors(db_session)
        sep = await _session(
            db_session, client=client, creator=admin, month=9, due=date(2026, 10, 5)
        )
        line = await _entry(db_session, sess=sep, day=date(2026, 9, 10), pair=(7, 10))
        await _login(client_with_db, admin.email)

        listed = await client_with_db.get(f"/api/v1/reconciliations/{sep.id}/file-entries")
        assert listed.status_code == 200, listed.text
        item = next(i for i in listed.json()["data"] if i["id"] == str(line.id))
        assert item["installment"]["numero"] == 7
        assert [f["vencimento"] for f in item["installment"]["futuras"]] == [
            "2026-10-10",
            "2026-11-10",
            "2026-12-10",
        ]
        assert item["omie_posting"] is None

        await _post(client_with_db, sep, _line(line.id, parcelas_futuras=True))
        listed = await client_with_db.get(f"/api/v1/reconciliations/{sep.id}/file-entries")
        item = next(i for i in listed.json()["data"] if i["id"] == str(line.id))
        assert [s["numero"] for s in item["omie_posting"]["installments"]] == [8, 9, 10]
        assert omie.n("incluir_conta_pagar") == 4

    async def test_parcela_lida_da_descricao_quando_a_linha_nao_tem_as_colunas(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        admin, client = await _actors(db_session)
        sep = await _session(
            db_session, client=client, creator=admin, month=9, due=date(2026, 10, 5)
        )
        line = await _entry(
            db_session, sess=sep, day=date(2026, 9, 10), description="LOJA PARC 09/10"
        )
        await _login(client_with_db, admin.email)

        lines = await _post(client_with_db, sep, _line(line.id, parcelas_futuras=True))

        assert [p["numero"] for p in lines[0]["parcelas"]] == [10]
        assert omie.n("incluir_conta_pagar") == 2


# ----------------------------------------------------------------------
# Critério 2: outubro ENCONTRA a 8 (sem título novo); só a baixa
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("flags")
class TestOutubroEncontraAParcela:
    async def test_data_da_compra_encontra_e_so_baixa(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        admin, client = await _actors(db_session)
        _sep, _line7, sep_lines = await _september(
            client_with_db, db_session, admin=admin, client=client
        )
        title_8 = sep_lines[0]["parcelas"][0]["payable_id"]
        oct_ = await _session(
            db_session, client=client, creator=admin, month=10, due=date(2026, 11, 5)
        )
        line8 = await _entry(db_session, sess=oct_, day=date(2026, 10, 10), pair=(8, 10))
        await _entry(db_session, sess=oct_, day=date(2026, 10, 28), amount="-5.00")
        titles_before = omie.n("incluir_conta_pagar")
        payments_before = omie.n("lancar_pagamento")

        lines = await _post(client_with_db, oct_, _line(line8.id, parcelas_futuras=True))

        result = lines[0]
        assert result["status"] == "lancada", result
        assert result["reason"] == "ja_lancada"
        assert result["payable_id"] == title_8
        assert omie.n("incluir_conta_pagar") == titles_before  # zero títulos novos
        assert omie.n("lancar_pagamento") == payments_before + 1
        assert omie.calls["lancar_pagamento"][-1].codigo_lancamento == title_8
        # A série tem um dono só: as 9 e 10 já existem, nada é recriado.
        assert [(p["numero"], p["status"]) for p in result["parcelas"]] == [
            (9, "ja_existia"),
            (10, "ja_existia"),
        ]
        rows = await _installments(db_session, client.id)
        assert rows[0].claimed_by_file_entry_id == line8.id
        assert len(rows) == 3

    async def test_vencimento_da_fatura_encontra_sem_baixa(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        mode = CardPostingDateMode.INVOICE_DUE_DATE.value
        admin, client = await _actors(db_session)
        _sep, _l7, sep_lines = await _september(
            client_with_db, db_session, admin=admin, client=client, mode=mode
        )
        # Sem baixa no processo do vencimento, nem para a 7.
        assert omie.n("lancar_pagamento") == 0
        title_8 = sep_lines[0]["parcelas"][0]["payable_id"]
        oct_ = await _session(
            db_session, client=client, creator=admin, month=10, mode=mode, due=date(2026, 11, 5)
        )
        line8 = await _entry(db_session, sess=oct_, day=date(2026, 10, 10), pair=(8, 10))
        titles_before = omie.n("incluir_conta_pagar")

        lines = await _post(client_with_db, oct_, _line(line8.id))

        assert lines[0]["reason"] == "ja_lancada"
        assert lines[0]["payable_id"] == title_8
        assert omie.n("incluir_conta_pagar") == titles_before
        assert omie.n("lancar_pagamento") == 0

    async def test_parcela_registrada_sem_titulo_bloqueia_em_vez_de_duplicar(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        admin, client = await _actors(db_session)
        sep = await _session(
            db_session, client=client, creator=admin, month=9, due=date(2026, 10, 5)
        )
        line7 = await _entry(db_session, sess=sep, day=date(2026, 9, 10), pair=(7, 10))
        await _login(client_with_db, admin.email)
        omie.fail_keys[derive_installment_payable_key(line7.id, 8)] = 1

        sep_lines = await _post(client_with_db, sep, _line(line7.id, parcelas_futuras=True))
        assert sep_lines[0]["reason"] == "parcelas_incompletas"
        assert [(p["numero"], p["status"]) for p in sep_lines[0]["parcelas"]] == [
            (8, "erro"),
            (9, "provisionada"),
            (10, "provisionada"),
        ]

        oct_ = await _session(db_session, client=client, creator=admin, month=10)
        line8 = await _entry(db_session, sess=oct_, day=date(2026, 10, 10), pair=(8, 10))
        titles_before = omie.n("incluir_conta_pagar")
        lines = await _post(client_with_db, oct_, _line(line8.id))

        assert lines[0]["status"] == "bloqueada"
        assert lines[0]["reason"] == "parcela_pendente_na_fatura_anterior"
        assert omie.n("incluir_conta_pagar") == titles_before

        # Retomar setembro envia SÓ a 8, com a MESMA chave.
        retry = await _post(client_with_db, sep, _line(line7.id, parcelas_futuras=True))
        assert {p["status"] for p in retry[0]["parcelas"]} == {"provisionada"}
        assert omie.n("incluir_conta_pagar") == titles_before + 1
        assert omie.calls["incluir_conta_pagar"][-1].codigo_lancamento_integracao == (
            derive_installment_payable_key(line7.id, 8)
        )


# ----------------------------------------------------------------------
# Critério 3: novembro sem a 9 avisa, nada é excluído
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("flags")
class TestNovembroSemAParcela:
    async def test_aviso_com_o_id_do_titulo_e_nada_excluido(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        await _ensure_missing_type(db_session)
        admin, client = await _actors(db_session)
        _sep, _l7, sep_lines = await _september(
            client_with_db, db_session, admin=admin, client=client
        )
        title_9 = sep_lines[0]["parcelas"][1]["payable_id"]
        nov = await _session(db_session, client=client, creator=admin, month=11)
        other = await _entry(db_session, sess=nov, day=date(2026, 11, 12), amount="-7.00")
        await _entry(db_session, sess=nov, day=date(2026, 11, 28), amount="-8.00")

        # O fim do lote de lançamento de novembro olha as parcelas que deviam vir.
        await _post(client_with_db, nov, _line(other.id))

        anomalies = (
            (
                await db_session.execute(
                    select(ReconciliationAnomaly)
                    .join(AnomalyType, AnomalyType.id == ReconciliationAnomaly.anomaly_type_id)
                    .where(
                        ReconciliationAnomaly.session_id == nov.id,
                        AnomalyType.code == ANOMALY_CODE_INSTALLMENT_MISSING,
                    )
                )
            )
            .scalars()
            .all()
        )
        # A 9 (10/11) já devia ter vindo; a 10 (10/12) ainda não.
        assert len(anomalies) == 1
        cipher = await load_client_cipher(client, settings=get_settings())
        context = cipher.decrypt(
            anomalies[0].context_encrypted,
            anomalies[0].context_iv,
            field_locator(AAD_ANOMALY_CONTEXT, anomalies[0].id),
        )
        assert "Parcela 9/10" in context
        assert f"título nº {title_9}" in context
        rows = await _installments(db_session, client.id)
        assert len(rows) == 3  # nada excluído
        assert rows[1].missing_anomaly_id == anomalies[0].id

        # Sem aviso duplicado numa segunda passada.
        assert await flag_missing_installments(db_session, session=nov, cipher=cipher) == 0

    async def test_a_linha_que_veio_nao_e_avisada(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        await _ensure_missing_type(db_session)
        admin, client = await _actors(db_session)
        await _september(client_with_db, db_session, admin=admin, client=client)
        nov = await _session(db_session, client=client, creator=admin, month=11)
        await _entry(db_session, sess=nov, day=date(2026, 11, 10), pair=(9, 10))
        await _entry(db_session, sess=nov, day=date(2026, 11, 28), amount="-8.00")
        cipher = await load_client_cipher(client, settings=get_settings())

        assert await flag_missing_installments(db_session, session=nov, cipher=cipher) == 0


# ----------------------------------------------------------------------
# Critério 4: flag desligada
# ----------------------------------------------------------------------


class TestFlagDesligada:
    async def test_opcao_some_e_pedido_e_recusado_por_linha(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        omie: Recorder,
        flags: Any,
    ) -> None:
        flags(parcelas=False)
        admin, client = await _actors(db_session)
        sep = await _session(db_session, client=client, creator=admin, month=9)
        line = await _entry(db_session, sess=sep, day=date(2026, 9, 10), pair=(7, 10))
        await _login(client_with_db, admin.email)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sep.id}")
        assert detail.json()["data"]["installment_posting_enabled"] is False

        lines = await _post(client_with_db, sep, _line(line.id, parcelas_futuras=True))

        assert lines[0]["status"] == "bloqueada"
        assert lines[0]["reason"] == "parcelas_futuras_desligadas"
        assert omie.n("incluir_conta_pagar") == 0
        assert omie.n("listar_clientes_por_cnpj") == 0
        assert await _installments(db_session, client.id) == []

    async def test_ligada_a_sessao_de_cartao_oferece(
        self,
        client_with_db: AsyncClient,
        db_session: AsyncSession,
        flags: Any,
    ) -> None:
        flags(parcelas=True)
        admin, client = await _actors(db_session)
        sep = await _session(db_session, client=client, creator=admin, month=9)
        await _login(client_with_db, admin.email)

        detail = await client_with_db.get(f"/api/v1/reconciliations/{sep.id}")
        assert detail.json()["data"]["installment_posting_enabled"] is True

    @pytest.mark.usefixtures("flags")
    async def test_linha_sem_parcela_futura_recusa_o_pedido(
        self, client_with_db: AsyncClient, db_session: AsyncSession, omie: Recorder
    ) -> None:
        admin, client = await _actors(db_session)
        sep = await _session(db_session, client=client, creator=admin, month=9)
        avista = await _entry(db_session, sess=sep, day=date(2026, 9, 10))
        ultima = await _entry(db_session, sess=sep, day=date(2026, 9, 11), pair=(10, 10))
        await _login(client_with_db, admin.email)

        lines = await _post(
            client_with_db,
            sep,
            _line(avista.id, parcelas_futuras=True),
            _line(ultima.id, parcelas_futuras=True),
        )

        assert {ln["reason"] for ln in lines} == {"sem_parcelas_futuras"}
        assert omie.n("incluir_conta_pagar") == 0
