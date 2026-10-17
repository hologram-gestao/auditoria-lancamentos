"""Lançamento como Contas a Pagar — `POST /reconciliations/{id}/omie-postings` (86e3n70q0).

A Omie é o `MockOmieClient` (credencial `FAKE_DEMO_OMIE_`), com cada método
embrulhado para registrar o que foi enviado: o percurso rota → serviço →
repositório → banco é o de produção. O que estes testes travam:

- o roteamento por linha (`provisionar` padrão, `direto` explícito) e as duas
  flags, cada uma bloqueando só as linhas dela;
- a máquina de estados: cada passo grava o id e COMMITA antes do próximo; uma
  falha no meio para a linha no passo, e a próxima execução RETOMA dali, sem
  refazer o que já tem id;
- o processo "vencimento da fatura" só provisiona (sem baixa); "na data da
  compra" provisiona e dá baixa na data da compra;
- o fornecedor: achado pelo CNPJ, cadastrado com razão social e e-mail, padrão
  do cliente, ou a linha bloqueada;
- nada de CNPJ, razão social ou e-mail no banco nem no log (§4.5).

**O que estes testes NÃO provam:** o contrato das chamadas. Isso é a fixture real
de 13/10/2026 e o gate `test_omie_fixtures.py` + `test_omie_payables_client.py`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from sqlalchemy import select
from structlog.testing import capture_logs

from app.core.config import get_settings
from app.core.exceptions import OmieFaultError, OmieTimeoutError
from app.db.models import (
    FileEntrySituation,
    OmiePostingStatus,
    ReconciliationOmiePosting,
    UsageEvent,
)
from app.integrations.omie import mock_client
from app.integrations.omie.mock_client import MockOmieClient, reset_mock_payables_state
from app.modules.reconciliations.omie_posting.keys import (
    derive_payable_key,
    derive_payment_key,
    derive_supplier_key,
)
from tests.integration.test_omie_posting_endpoint import (
    POSTING_URL,
    Scenario,
    _login,
    _seed_client,
    _seed_entry,
    _seed_missing_in_omie_anomaly,
    _seed_session,
    _seed_user,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

#: CNPJs do mock: com cadastro (seed) e sem cadastro.
CNPJ_FOUND = "12.345.678/0001-95"
CNPJ_FOUND_CODE = 700_000_001
CNPJ_NEW = "11.222.333/0001-81"
CNPJ_NEW_DIGITS = "11222333000181"


@pytest.fixture
async def scenario(db_session: AsyncSession) -> Scenario:
    from uuid import uuid4

    admin = await _seed_user(db_session, email=f"prov-{uuid4().hex[:8]}@hologram.com.br")
    client = await _seed_client(db_session, creator=admin, name=f"Prov {uuid4().hex[:6]}")
    sess = await _seed_session(db_session, client=client, creator=admin)
    return Scenario(admin, client, sess)


@pytest.fixture
def flags(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Liga/desliga as duas escritas por teste. Default: as duas ligadas."""

    def _set(*, direto: bool = True, provisao: bool = True) -> None:
        monkeypatch.setenv("OMIE_POSTING_ENABLED", "true" if direto else "false")
        monkeypatch.setenv("OMIE_PAYABLE_POSTING_ENABLED", "true" if provisao else "false")
        get_settings.cache_clear()

    _set()
    yield _set
    get_settings.cache_clear()


class Recorder:
    """Embrulha os métodos do mock e guarda cada request, por método."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: dict[str, list[Any]] = {}
        self._monkeypatch = monkeypatch
        for name in (
            "listar_clientes_por_cnpj",
            "incluir_cliente",
            "incluir_conta_pagar",
            "lancar_pagamento",
            "incluir_lanc_cc",
        ):
            self._wrap(name)

    def _wrap(self, name: str) -> None:
        original = getattr(MockOmieClient, name)
        calls = self.calls.setdefault(name, [])

        async def wrapper(self_: MockOmieClient, *args: Any, **kwargs: Any) -> Any:
            calls.append(args[0] if args else kwargs)
            return await original(self_, *args, **kwargs)

        self._monkeypatch.setattr(MockOmieClient, name, wrapper)

    def fail(self, name: str, exc: Exception, *, times: int = 1) -> None:
        """As próximas `times` chamadas de `name` levantam `exc` (e ficam registradas)."""
        original = getattr(MockOmieClient, name)
        state = {"left": times}

        async def failing(self_: MockOmieClient, *args: Any, **kwargs: Any) -> Any:
            if state["left"] > 0:
                state["left"] -= 1
                self.calls.setdefault(f"{name}:failed", []).append(args[0] if args else kwargs)
                raise exc
            return await original(self_, *args, **kwargs)

        self._monkeypatch.setattr(MockOmieClient, name, failing)

    def n(self, name: str) -> int:
        return len(self.calls.get(name, []))


@pytest.fixture
def omie(monkeypatch: pytest.MonkeyPatch) -> Iterator[Recorder]:
    monkeypatch.setattr(mock_client, "_DELAY_LISTAR_CONTAS_SECONDS", 0.0)
    reset_mock_payables_state()
    yield Recorder(monkeypatch)
    reset_mock_payables_state()


def _line(entry_id: UUID, **extra: Any) -> dict[str, Any]:
    return {"file_entry_id": str(entry_id), "cod_categoria": "2.01.03", **extra}


async def _post(http: AsyncClient, scenario: Scenario, *lines: dict[str, Any]) -> Any:
    return await http.post(
        POSTING_URL.format(session_id=scenario.session.id), json={"lines": list(lines)}
    )


async def _posting(db: AsyncSession, entry_id: UUID) -> ReconciliationOmiePosting:
    return (
        await db.execute(
            select(ReconciliationOmiePosting)
            .where(ReconciliationOmiePosting.file_entry_id == entry_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


def _fault(code: str, text: str = "erro") -> OmieFaultError:
    return OmieFaultError(
        f"Fault: {code} - {text}",
        user_message=f"Ocorreu um erro ao acessar o Omie: {code} - {text}",
        metadata={"fault_code": f"SOAP-ENV:Client-{code}"},
    )


# ----------------------------------------------------------------------
# Flags e roteamento
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("omie")
class TestFlagsERoteamento:
    async def test_provisao_desligada_bloqueia_so_a_provisao(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession, flags: Any
    ) -> None:
        flags(direto=True, provisao=False)
        provisao = await _seed_entry(db_session, sess=scenario.session, day=10)
        direto = await _seed_entry(db_session, sess=scenario.session, day=11)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(
            client_with_db,
            scenario,
            _line(provisao.id, cnpj_fornecedor=CNPJ_FOUND),
            _line(direto.id, modo="direto"),
        )

        assert resp.status_code == 200, resp.text
        by_id = {ln["file_entry_id"]: ln for ln in resp.json()["data"]["lines"]}
        assert by_id[str(provisao.id)]["reason"] == "provisao_desligada"
        assert by_id[str(direto.id)]["status"] == "lancada"

    async def test_direto_desligado_bloqueia_so_o_direto(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession, flags: Any
    ) -> None:
        flags(direto=False, provisao=True)
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id, modo="direto"))

        assert resp.json()["data"]["lines"][0]["reason"] == "direto_desligado"

    async def test_as_duas_desligadas_recusam_o_lote(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession, flags: Any
    ) -> None:
        flags(direto=False, provisao=False)
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id))

        assert resp.status_code == 409, resp.text

    @pytest.mark.usefixtures("flags")
    async def test_campos_da_provisao_num_direto_sao_400(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        resp = await _post(
            client_with_db, scenario, _line(entry.id, modo="direto", cnpj_fornecedor=CNPJ_FOUND)
        )
        assert resp.status_code == 400, resp.text


# ----------------------------------------------------------------------
# Caminho feliz nos dois processos
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("flags")
class TestProvisaoNosDoisProcessos:
    async def test_data_da_compra_provisiona_e_da_baixa(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session, amount="-12.34", day=15)
        anomaly = await _seed_missing_in_omie_anomaly(
            db_session, sess=scenario.session, entry=entry
        )
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        assert resp.status_code == 200, resp.text
        line = resp.json()["data"]["lines"][0]
        assert line["status"] == "lancada"
        assert line["kind"] == "provisao"
        assert line["step"] == "concluido"
        assert line["supplier_code"] == CNPJ_FOUND_CODE
        assert line["payable_id"] is not None
        assert line["payment_id"] is not None

        titulo = omie.calls["incluir_conta_pagar"][0]
        assert titulo.codigo_lancamento_integracao == derive_payable_key(entry.id)
        assert titulo.codigo_cliente_fornecedor == CNPJ_FOUND_CODE
        assert titulo.data_emissao == "15/04/2026"
        # Sem vencimento gravado na sessão: vence na própria data da compra.
        assert titulo.data_vencimento == "15/04/2026"
        assert titulo.valor_documento == Decimal("12.34")
        assert titulo.codigo_categoria == "2.01.03"
        assert titulo.categorias is None
        assert titulo.id_conta_corrente == scenario.session.omie_conta_id
        assert titulo.observacao == "CAFETERIA DO LARGO"
        baixa = omie.calls["lancar_pagamento"][0]
        assert baixa.codigo_baixa_integracao == derive_payment_key(entry.id)
        assert baixa.codigo_lancamento == line["payable_id"]
        assert baixa.data == "15/04/2026"
        assert baixa.valor == Decimal("12.34")

        posting = await _posting(db_session, entry.id)
        assert posting.status == OmiePostingStatus.CONFIRMED.value
        assert (posting.kind, posting.step) == ("provisao", "concluido")
        await db_session.refresh(entry)
        # A linha NÃO é casada com um lançamento que ninguém viu no extrato.
        assert entry.situation == FileEntrySituation.SEM_OMIE.value
        assert entry.omie_lancamento_id is None
        await db_session.refresh(anomaly)
        assert anomaly.resolved is True

    async def test_vencimento_da_fatura_so_provisiona(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        scenario.session.card_posting_date_mode = "invoice_due_date"
        scenario.session.invoice_due_date = date(2026, 5, 10)
        await db_session.flush()
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        line = resp.json()["data"]["lines"][0]
        assert line["status"] == "lancada"
        assert line["step"] == "concluido"
        assert line["payment_id"] is None
        assert omie.n("lancar_pagamento") == 0
        titulo = omie.calls["incluir_conta_pagar"][0]
        assert titulo.data_vencimento == "10/05/2026"
        assert titulo.data_previsao == "10/05/2026"
        assert titulo.data_emissao == "15/04/2026"

    async def test_vencimento_bloqueia_o_direto_e_provisiona_no_mesmo_lote(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        scenario.session.card_posting_date_mode = "invoice_due_date"
        scenario.session.invoice_due_date = date(2026, 5, 10)
        await db_session.flush()
        direto = await _seed_entry(db_session, sess=scenario.session, day=10)
        provisao = await _seed_entry(db_session, sess=scenario.session, day=11)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(
            client_with_db,
            scenario,
            _line(direto.id, modo="direto"),
            _line(provisao.id, cnpj_fornecedor=CNPJ_FOUND),
        )

        by_id = {ln["file_entry_id"]: ln for ln in resp.json()["data"]["lines"]}
        assert by_id[str(direto.id)]["reason"] == "direto_indisponivel_no_modo_vencimento"
        assert by_id[str(provisao.id)]["status"] == "lancada"
        assert omie.n("incluir_lanc_cc") == 0

    async def test_lote_misto_direto_e_provisao(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        direto = await _seed_entry(db_session, sess=scenario.session, day=10)
        provisao = await _seed_entry(db_session, sess=scenario.session, day=11)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(
            client_with_db,
            scenario,
            _line(direto.id, modo="direto"),
            _line(provisao.id, cnpj_fornecedor=CNPJ_FOUND),
        )

        data = resp.json()["data"]
        assert data["lancadas"] == 2
        assert omie.n("incluir_lanc_cc") == 1
        assert omie.n("incluir_conta_pagar") == 1
        kinds = {ln["file_entry_id"]: ln["kind"] for ln in data["lines"]}
        assert kinds == {str(direto.id): "direto", str(provisao.id): "provisao"}

    async def test_reenviar_a_linha_concluida_nao_lanca_de_novo(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        again = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))
        direto = await _post(client_with_db, scenario, _line(entry.id, modo="direto"))

        assert again.json()["data"]["lines"][0]["reason"] == "ja_lancada"
        assert direto.json()["data"]["lines"][0]["reason"] == "ja_lancada"
        assert omie.n("incluir_conta_pagar") == 1
        assert omie.n("incluir_lanc_cc") == 0


# ----------------------------------------------------------------------
# Fornecedor
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("flags")
class TestFornecedor:
    async def test_cnpj_sem_cadastro_cadastra_uma_vez_por_cnpj(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        um = await _seed_entry(db_session, sess=scenario.session, day=10)
        outro = await _seed_entry(db_session, sess=scenario.session, day=11)
        await _login(client_with_db, scenario.admin.email)
        dados = {"cnpj_fornecedor": CNPJ_NEW, "razao_social": "NOVO LTDA", "email": "a@b.com"}

        resp = await _post(
            client_with_db, scenario, _line(um.id, **dados), _line(outro.id, **dados)
        )

        assert resp.json()["data"]["lancadas"] == 2
        assert omie.n("incluir_cliente") == 1, "o 2º achou o cadastro criado pelo 1º"
        cadastro = omie.calls["incluir_cliente"][0]
        assert cadastro.codigo_cliente_integracao == derive_supplier_key(CNPJ_NEW_DIGITS)
        assert cadastro.cnpj_cpf == CNPJ_NEW
        codes = {ln["supplier_code"] for ln in resp.json()["data"]["lines"]}
        assert len(codes) == 1

    async def test_sem_cnpj_usa_o_fornecedor_padrao(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        scenario.client.omie_default_supplier_code = 4242
        await db_session.flush()
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id))

        line = resp.json()["data"]["lines"][0]
        assert line["status"] == "lancada"
        assert line["supplier_code"] == 4242
        assert omie.n("listar_clientes_por_cnpj") == 0

    async def test_codigo_escolhido_vai_ao_titulo_sem_nova_busca(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        """86e3qhhrd: o cadastro escolhido pelo nome (aqui, um do exterior, sem
        CNPJ) recebe o título como veio, sem `ListarClientes` nem cadastro novo."""
        scenario.client.omie_default_supplier_code = 4242
        await db_session.flush()
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id, codigo_fornecedor=700_000_003))

        line = resp.json()["data"]["lines"][0]
        assert line["status"] == "lancada", line
        assert line["supplier_code"] == 700_000_003
        assert omie.n("listar_clientes_por_cnpj") == 0
        assert omie.n("incluir_cliente") == 0
        titulo = omie.calls["incluir_conta_pagar"][0]
        assert titulo.codigo_cliente_fornecedor == 700_000_003

    @pytest.mark.parametrize(
        "extra",
        [
            {"codigo_fornecedor": 700_000_001, "cnpj_fornecedor": CNPJ_FOUND},
            {"codigo_fornecedor": 0},
            {"codigo_fornecedor": 2**63},
            {"codigo_fornecedor": 700_000_001, "modo": "direto"},
        ],
        ids=["com-cnpj", "zero", "acima-do-bigint", "no-direto"],
    )
    async def test_codigo_fornecedor_invalido_e_400_sem_escrever(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
        extra: dict[str, Any],
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id, **extra))

        assert resp.status_code == 400, resp.text
        assert omie.n("incluir_conta_pagar") == 0
        assert omie.n("incluir_lanc_cc") == 0

    async def test_cnpj_sem_cadastro_e_sem_dados_cai_no_padrao(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        scenario.client.omie_default_supplier_code = 4242
        await db_session.flush()
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_NEW))

        assert resp.json()["data"]["lines"][0]["supplier_code"] == 4242
        assert omie.n("incluir_cliente") == 0

    async def test_sem_fornecedor_nenhum_bloqueia_sem_escrever(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_NEW))

        line = resp.json()["data"]["lines"][0]
        assert line["status"] == "bloqueada"
        assert line["reason"] == "fornecedor_pendente"
        assert line["step"] == "fornecedor"
        assert omie.n("incluir_conta_pagar") == 0
        posting = await _posting(db_session, entry.id)
        assert posting.step == "fornecedor"
        assert posting.attempts == 0

    async def test_nem_cnpj_nem_dados_do_fornecedor_persistem_ou_vao_para_o_log(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        with capture_logs() as logs:
            await _post(
                client_with_db,
                scenario,
                _line(
                    entry.id,
                    cnpj_fornecedor=CNPJ_NEW,
                    razao_social="FORNECEDOR SIGILOSO LTDA",
                    email="sigilo@fornecedor.com",
                ),
            )

        assert omie.n("incluir_cliente") == 1
        dump = repr(logs)
        for secret in (CNPJ_NEW_DIGITS, CNPJ_NEW, "FORNECEDOR SIGILOSO", "sigilo@fornecedor"):
            assert secret not in dump
        posting = await _posting(db_session, entry.id)
        row = repr({c.name: getattr(posting, c.name) for c in posting.__table__.columns})
        for secret in (CNPJ_NEW_DIGITS, "FORNECEDOR SIGILOSO", "sigilo@fornecedor"):
            assert secret not in row


# ----------------------------------------------------------------------
# Rateio por departamento e categorias
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("flags")
class TestRateioECategorias:
    async def test_departamento_obrigatorio_sem_rateio_bloqueia(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        scenario.client.omie_department_required = True
        await db_session.flush()
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        assert resp.json()["data"]["lines"][0]["reason"] == "departamento_pendente"
        assert omie.n("listar_clientes_por_cnpj") == 0

    async def test_rateio_vai_com_valor_e_percentual_fechando_o_total(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        scenario.client.omie_department_required = True
        await db_session.flush()
        entry = await _seed_entry(db_session, sess=scenario.session, amount="-10.00")
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(
            client_with_db,
            scenario,
            _line(
                entry.id,
                cnpj_fornecedor=CNPJ_FOUND,
                departamentos=[
                    {"codigo": "800000002", "percentual": "33.33"},
                    {"codigo": "800000003", "percentual": "33.33"},
                    {"codigo": "800000004", "percentual": "33.34"},
                ],
            ),
        )

        assert resp.json()["data"]["lines"][0]["status"] == "lancada", resp.text
        dist = omie.calls["incluir_conta_pagar"][0].distribuicao
        assert [d.c_cod_dep for d in dist] == ["800000002", "800000003", "800000004"]
        assert sum(d.n_val_dep for d in dist) == Decimal("10.00")
        assert sum(d.n_per_dep for d in dist) == Decimal("100")

    async def test_percentuais_que_nao_somam_100_sao_400(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        resp = await _post(
            client_with_db,
            scenario,
            _line(entry.id, departamentos=[{"codigo": "1", "percentual": "90"}]),
        )
        assert resp.status_code == 400, resp.text

    async def test_duas_categorias_absorvem_o_centavo_na_ultima(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session, amount="-100.00")
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(
            client_with_db,
            scenario,
            {
                "file_entry_id": str(entry.id),
                "cnpj_fornecedor": CNPJ_FOUND,
                "categorias": [
                    {"codigo": "2.05.02", "valor": "3.38"},
                    {"codigo": "2.01.03", "valor": "96.61"},
                ],
            },
        )

        assert resp.json()["data"]["lines"][0]["status"] == "lancada", resp.text
        titulo = omie.calls["incluir_conta_pagar"][0]
        assert titulo.codigo_categoria is None
        assert [(c.codigo_categoria, c.valor) for c in titulo.categorias] == [
            ("2.05.02", Decimal("3.38")),
            ("2.01.03", Decimal("96.62")),
        ]
        assert sum(c.percentual for c in titulo.categorias) == Decimal("100")

    async def test_categorias_que_nao_fecham_bloqueiam_sem_escrever(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session, amount="-100.00")
        await _login(client_with_db, scenario.admin.email)

        resp = await _post(
            client_with_db,
            scenario,
            {
                "file_entry_id": str(entry.id),
                "cnpj_fornecedor": CNPJ_FOUND,
                "categorias": [
                    {"codigo": "2.05.02", "valor": "3.38"},
                    {"codigo": "2.01.03", "valor": "90.00"},
                ],
            },
        )

        assert resp.json()["data"]["lines"][0]["reason"] == "categorias_nao_fecham"
        assert omie.n("listar_clientes_por_cnpj") == 0


# ----------------------------------------------------------------------
# Falha no meio e retomada
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("flags")
class TestFalhaERetomada:
    async def test_baixa_recusada_para_no_passo_e_a_retomada_nao_recria_o_titulo(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        omie.fail("lancar_pagamento", _fault("1234", "conta bloqueada"))

        first = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        line = first.json()["data"]["lines"][0]
        assert (line["status"], line["reason"], line["step"]) == ("erro", "erro_omie", "baixa")
        assert line["payable_id"] is not None
        posting = await _posting(db_session, entry.id)
        assert posting.status == OmiePostingStatus.FAILED.value
        assert posting.payable_id == line["payable_id"]
        assert posting.attempts == 0, "fault é resposta definitiva: a contagem recomeça"

        second = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        line = second.json()["data"]["lines"][0]
        assert line["status"] == "lancada"
        assert omie.n("incluir_conta_pagar") == 1, "a retomada refez o título"
        assert omie.n("listar_clientes_por_cnpj") == 1, "a retomada refez o fornecedor"
        assert omie.n("lancar_pagamento") == 1

    async def test_timeout_no_titulo_reenvia_com_a_mesma_chave(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        omie.fail("incluir_conta_pagar", OmieTimeoutError("timeout"))

        first = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))
        # Nada foi ESCRITO no lote (a busca do fornecedor é leitura): vira 5xx.
        assert first.status_code >= 500, first.text
        posting = await _posting(db_session, entry.id)
        assert (posting.step, posting.attempts) == ("titulo", 1)

        second = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        assert second.json()["data"]["lines"][0]["status"] == "lancada"
        keys = [r.codigo_lancamento_integracao for r in omie.calls["incluir_conta_pagar:failed"]]
        keys += [r.codigo_lancamento_integracao for r in omie.calls["incluir_conta_pagar"]]
        assert keys == [derive_payable_key(entry.id)] * 2

    async def test_timeout_na_baixa_depois_do_titulo_devolve_200_com_o_passo(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        """Com um título já escrito no lote, a resposta honesta é 200 com o passo."""
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        omie.fail("lancar_pagamento", OmieTimeoutError("timeout"))

        resp = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        assert resp.status_code == 200, resp.text
        line = resp.json()["data"]["lines"][0]
        assert (line["reason"], line["step"]) == ("omie_indisponivel", "baixa")
        assert line["payable_id"] is not None

    async def test_tres_envios_sem_confirmacao_param_a_linha(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        omie.fail("incluir_conta_pagar", OmieTimeoutError("timeout"), times=10)

        for _ in range(3):
            await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))
        fourth = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        line = fourth.json()["data"]["lines"][0]
        assert line["status"] == "bloqueada"
        assert line["reason"] == "envio_anterior_sem_confirmacao"
        assert derive_payable_key(entry.id) in line["message"]
        assert omie.n("incluir_conta_pagar:failed") == 3

    async def test_competencia_fechada_tem_motivo_proprio(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        omie.fail("incluir_conta_pagar", _fault("757", "competência fechada"))

        resp = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        line = resp.json()["data"]["lines"][0]
        assert (line["status"], line["reason"], line["step"]) == (
            "erro",
            "competencia_fechada",
            "titulo",
        )
        assert "757" in line["message"]


# ----------------------------------------------------------------------
# Troca de forma
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("flags")
class TestTrocaDeForma:
    async def test_provisao_com_titulo_trava_o_direto(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        omie.fail("lancar_pagamento", _fault("1234"))
        await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        resp = await _post(client_with_db, scenario, _line(entry.id, modo="direto"))

        assert resp.json()["data"]["lines"][0]["reason"] == "modo_divergente"
        assert omie.n("incluir_lanc_cc") == 0

    async def test_provisao_parada_no_fornecedor_pode_virar_direto(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_NEW))

        resp = await _post(client_with_db, scenario, _line(entry.id, modo="direto"))

        assert resp.json()["data"]["lines"][0]["status"] == "lancada"
        posting = await _posting(db_session, entry.id)
        assert (posting.kind, posting.step) == ("direto", None)

    async def test_direto_recusado_uma_vez_pode_virar_provisao(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
        omie: Recorder,
    ) -> None:
        entry = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)
        omie.fail("incluir_lanc_cc", _fault("5001"))
        await _post(client_with_db, scenario, _line(entry.id, modo="direto"))

        resp = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

        assert resp.json()["data"]["lines"][0]["status"] == "lancada"
        posting = await _posting(db_session, entry.id)
        assert (posting.kind, posting.step) == ("provisao", "concluido")


# ----------------------------------------------------------------------
# Evento de uso
# ----------------------------------------------------------------------


@pytest.mark.usefixtures("flags", "omie")
class TestEvento:
    async def test_um_evento_por_lote_so_com_contagens(
        self, client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
    ) -> None:
        ok = await _seed_entry(db_session, sess=scenario.session, day=10)
        pendente = await _seed_entry(db_session, sess=scenario.session, day=11)
        await _login(client_with_db, scenario.admin.email)

        await _post(
            client_with_db,
            scenario,
            _line(ok.id, cnpj_fornecedor=CNPJ_FOUND),
            _line(pendente.id, cnpj_fornecedor=CNPJ_NEW),
        )

        rows = (
            (
                await db_session.execute(
                    select(UsageEvent).where(
                        UsageEvent.event == "lancamento_omie_provisao",
                        UsageEvent.session_id == scenario.session.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        props = rows[0].props
        assert props["processo"] == "purchase_date"
        assert (props["linhas"], props["concluidas"], props["bloqueadas"]) == (2, 1, 1)
        assert (props["titulos_criados"], props["baixas_criadas"]) == (1, 1)
        assert CNPJ_NEW_DIGITS not in repr(props)


@pytest.mark.usefixtures("flags", "omie")
async def test_provisao_concluida_resolve_a_anomalia_e_nao_casa_a_linha(
    client_with_db: AsyncClient, scenario: Scenario, db_session: AsyncSession
) -> None:
    """A anomalia "não existe no Omie" deixou de ser verdade e fecha; a linha não é
    casada com um lançamento de extrato que ninguém viu (o reprocessamento acha).

    O cliente do cenário não tem DEK provisionada, então a nota segue o caminho
    fail-soft de sempre (resolve sem nota) — o conteúdo da nota é só ids.
    """
    entry = await _seed_entry(db_session, sess=scenario.session)
    anomaly = await _seed_missing_in_omie_anomaly(db_session, sess=scenario.session, entry=entry)
    await _login(client_with_db, scenario.admin.email)

    resp = await _post(client_with_db, scenario, _line(entry.id, cnpj_fornecedor=CNPJ_FOUND))

    assert resp.json()["data"]["lines"][0]["status"] == "lancada"
    await db_session.refresh(anomaly)
    assert anomaly.resolved is True
    await db_session.refresh(entry)
    assert entry.situation == FileEntrySituation.SEM_OMIE.value
    assert entry.omie_lancamento_id is None


# ----------------------------------------------------------------------
# Sugestão de fornecedor por linha (86e3qhhrd)
# ----------------------------------------------------------------------

SUGGESTIONS_URL = "/api/v1/reconciliations/{session_id}/omie-postings/fornecedores-sugeridos"


@pytest.mark.usefixtures("omie")
class TestSugestaoDeFornecedor:
    async def test_sugere_pela_descricao_na_ordem_do_pedido(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
    ) -> None:
        software = await _seed_entry(
            db_session, sess=scenario.session, day=10, description="SOFTWARE DEMO*ASSINATURA"
        )
        cafe = await _seed_entry(db_session, sess=scenario.session, day=11)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.get(
            SUGGESTIONS_URL.format(session_id=scenario.session.id),
            params=[("file_entry_id", str(cafe.id)), ("file_entry_id", str(software.id))],
        )

        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert [d["file_entry_id"] for d in data] == [str(cafe.id), str(software.id)]
        assert data[0]["sugestoes"] == []
        assert [s["codigo"] for s in data[1]["sugestoes"]] == [700_000_003]

    async def test_linha_de_outra_conciliacao_nao_volta(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
    ) -> None:
        outra = await _seed_session(
            db_session, client=scenario.client, creator=scenario.admin, omie_conta_id=900_000_004
        )
        alheia = await _seed_entry(db_session, sess=outra, description="SOFTWARE DEMO")
        propria = await _seed_entry(db_session, sess=scenario.session)
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.get(
            SUGGESTIONS_URL.format(session_id=scenario.session.id),
            params=[("file_entry_id", str(alheia.id)), ("file_entry_id", str(propria.id))],
        )

        assert resp.status_code == 200, resp.text
        assert [d["file_entry_id"] for d in resp.json()["data"]] == [str(propria.id)]

    async def test_descricao_nao_vai_para_o_log(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
    ) -> None:
        entry = await _seed_entry(
            db_session, sess=scenario.session, description="SOFTWARE DEMO*ASSINATURA"
        )
        await _login(client_with_db, scenario.admin.email)

        with capture_logs() as logs:
            resp = await client_with_db.get(
                SUGGESTIONS_URL.format(session_id=scenario.session.id),
                params={"file_entry_id": str(entry.id)},
            )

        assert resp.status_code == 200, resp.text
        dump = repr(logs)
        assert "ASSINATURA" not in dump
        assert "SOFTWARE DEMO GMBH" not in dump

    async def test_cliente_encerrado_e_409(
        self,
        client_with_db: AsyncClient,
        scenario: Scenario,
        db_session: AsyncSession,
    ) -> None:
        from datetime import UTC, datetime

        entry = await _seed_entry(db_session, sess=scenario.session)
        scenario.client.closed_at = datetime.now(UTC)
        await db_session.flush()
        await _login(client_with_db, scenario.admin.email)

        resp = await client_with_db.get(
            SUGGESTIONS_URL.format(session_id=scenario.session.id),
            params={"file_entry_id": str(entry.id)},
        )
        assert resp.status_code == 409, resp.text
