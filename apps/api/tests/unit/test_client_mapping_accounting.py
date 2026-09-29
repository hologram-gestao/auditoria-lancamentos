"""De-para no destino `conta_contabil` sem banco (Sprint 16, BACK 16.2).

O que se prova aqui (a integração repete o essencial contra Postgres):
  - schema: modelo e migration `f5b8d2e61c37` batem (CHECKs trocados, colunas novas,
    nomes ≤ 63, downgrade com guarda); o item é SNAPSHOT sem FK;
  - borda: `alvo` exige catálogo XOR conta; histórico só com conta, aparado, e acima
    de 500 é erro de FORMA (400 na rota);
  - serviço: catálogo em `conta_contabil` → 422 próprio; conta do plano em outro
    destino → 422 próprio; o validador ÚNICO da 16.1 é quem recusa conta alheia,
    sintética ou inativa; o histórico é cifrado com a pk da DECISÃO; trocar a conta ou
    SÓ o histórico é vigência NOVA; o mesmo pedido é no-op;
  - aplicação: o item leva o código reduzido e o id da vigência, e os dois mudam o
    token da prévia;
  - o texto do histórico nunca aparece em log.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from structlog.testing import capture_logs

from app.core.authz import CurrentUser
from app.core.config import get_settings
from app.core.crypto_service import (
    AAD_DECISION_HISTORY,
    field_locator,
    load_client_cipher,
    new_client_dek,
)
from app.core.exceptions import (
    AccountingAccountNotFoundError,
    AccountingAccountNotPostableError,
    AccountingAccountOutsideDestinationError,
    CatalogTargetInAccountingDestinationError,
    ErrorCode,
)
from app.db.models import (
    ClientAccountingAccount,
    ClientMappingDecision,
    ClientMappingMaterializationItem,
    DecisionType,
    MappingDestination,
    MappingTarget,
    UserRole,
    UserScope,
)
from app.db.models import client_mapping as cm
from app.modules.client_accounting_chart.service import AccountingChartService
from app.modules.client_mapping import repository as mapping_repository
from app.modules.client_mapping.accounting import AccountingDecisionSupport, normalize_history
from app.modules.client_mapping.apply import apply_mapping
from app.modules.client_mapping.schemas import DecisionItemRequest
from app.modules.client_mapping.service import ClientMappingDecisionService, DecisionInput
from app.modules.mapping_catalog.service import MappingCatalogService

pytestmark = pytest.mark.unit

ORG = uuid4()
HOJE = date(2026, 9, 15)
SET = date(2026, 9, 1)
OUT = date(2026, 10, 1)
_SECRET_HISTORY = "RECEBIMENTO REF. ALUGUEL IMOVEL, INQUILINO SIGILOSO - LOJA 04 - 06/2026"

_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
_MIGRATION = "f5b8d2e61c37_s16_mapping_accounting_target.py"


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_mig_f5b8d2e61c37", _VERSIONS / _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchemaDaMigration:
    def test_predicados_batem_com_o_modelo(self) -> None:
        mig = _load_migration()
        assert cm.DECISION_TARGET_COHERENT_CHECK == mig._CK_DECISION_TARGET
        assert cm.DECISION_HISTORY_COHERENT_CHECK == mig._CK_HISTORY
        assert cm.ITEM_TARGET_COHERENT_CHECK == mig._CK_ITEM_TARGET
        assert f"ck_client_mapping_decisions_{mig._CK_HISTORY_LABEL}" == (
            cm.DECISION_HISTORY_CONSTRAINT
        )
        assert f"ck_client_mapping_materialization_items_{mig._CK_ITEM_TARGET_LABEL}" == (
            cm.ITEM_TARGET_CONSTRAINT
        )
        assert mig._FK_ACCOUNT == cm.FK_DECISION_ACCOUNTING_ACCOUNT
        assert mig._IX_ACCOUNT == cm.IX_DECISION_ACCOUNTING_ACCOUNT
        assert (
            ClientMappingMaterializationItem.__table__.c.accounting_account_code.type.length
        ) == mig._ACCOUNT_CODE_MAX

    def test_colunas_novas_estao_na_migration_e_no_modelo(self) -> None:
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        for column in ("accounting_account_id", "history_encrypted", "history_iv"):
            assert f'"{column}"' in upgrade
            assert column in ClientMappingDecision.__table__.c
        for column in ("accounting_account_code", "decision_id"):
            assert f'"{column}"' in upgrade
            assert column in ClientMappingMaterializationItem.__table__.c

    def test_nomes_cabem_no_postgres(self) -> None:
        mig = _load_migration()
        for name in (
            mig._FK_ACCOUNT,
            mig._IX_ACCOUNT,
            cm.DECISION_HISTORY_CONSTRAINT,
            cm.ITEM_TARGET_CONSTRAINT,
            cm.DECISION_TARGET_CONSTRAINT,
        ):
            assert len(name) <= 63, name

    def test_encadeia_na_16_1_e_o_downgrade_tem_guarda(self) -> None:
        mig = _load_migration()
        assert mig.down_revision == "e3a7c1f95b40"
        downgrade = (
            (_VERSIONS / _MIGRATION).read_text(encoding="utf-8").split("def downgrade()", 1)[1]
        )
        # A guarda roda ANTES de qualquer mudança: nada de apagar decisão/materialização.
        assert downgrade.index("_ABORT_IF_ACCOUNTING") < downgrade.index("drop_constraint")
        assert "DELETE" not in downgrade.upper().replace("ON DELETE", "")

    def test_item_e_snapshot_sem_fk_e_decisao_so_referencia_o_plano(self) -> None:
        assert not ClientMappingMaterializationItem.__table__.c.decision_id.foreign_keys
        assert not ClientMappingMaterializationItem.__table__.c.accounting_account_code.foreign_keys
        (fk,) = ClientMappingDecision.__table__.c.accounting_account_id.foreign_keys
        assert fk.column.table.name == "client_accounting_accounts"

    def test_historico_so_existe_cifrado(self) -> None:
        columns = set(ClientMappingDecision.__table__.c.keys())
        assert {"history_encrypted", "history_iv"} <= columns
        assert "history" not in columns
        assert "history_encrypted" not in ClientMappingMaterializationItem.__table__.c
        assert "accounting_account_name" not in ClientMappingMaterializationItem.__table__.c

    def test_limite_do_historico_tem_o_valor_da_s14(self) -> None:
        from app.modules.client_file_ingestion.reader import MAX_DESCRIPTION_CHARS

        assert cm.MAX_DECISION_HISTORY_CHARS == MAX_DESCRIPTION_CHARS == 500

    def test_nao_ha_update_de_decisao_alem_da_resolucao_da_herdada(self) -> None:
        """Vigência append-only: a ÚNICA escrita não-append é `resolve_inherited` (S12)."""
        source = inspect.getsource(mapping_repository)
        updates = re.findall(r"update\(ClientMappingDecision\)", source)
        assert len(updates) == 1
        assert "update(ClientMappingDecision)" in inspect.getsource(
            mapping_repository.ClientMappingRepository.resolve_inherited
        )


# ---------------------------------------------------------------------------
# Borda (forma = 400 genérico na rota)
# ---------------------------------------------------------------------------


class TestBorda:
    def _req(self, **kw: Any) -> DecisionItemRequest:
        return DecisionItemRequest.model_validate({"categoryCode": "c1", **kw})

    def test_alvo_com_conta_do_plano(self) -> None:
        account = uuid4()
        req = self._req(decision="alvo", accountingAccountId=str(account), history="  texto  ")
        assert req.accounting_account_id == account
        assert req.history == "texto"

    @pytest.mark.parametrize(
        "payload",
        [
            {"decision": "alvo"},
            {"decision": "alvo", "targetCode": "1.01", "accountingAccountId": str(uuid4())},
            {"decision": "nao_mapear", "accountingAccountId": str(uuid4())},
            {"decision": "alvo", "targetCode": "1.01", "history": "x"},
            {"decision": "nao_mapear", "history": "x"},
        ],
    )
    def test_formas_invalidas(self, payload: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            self._req(**payload)

    def test_historico_no_limite_passa_e_acima_nao(self) -> None:
        account = str(uuid4())
        ok = self._req(decision="alvo", accountingAccountId=account, history=" " + "x" * 500 + " ")
        assert ok.history is not None
        assert len(ok.history) == 500
        with pytest.raises(ValidationError):
            self._req(decision="alvo", accountingAccountId=account, history="x" * 501)

    def test_historico_vazio_e_sem_historico(self) -> None:
        req = self._req(decision="alvo", accountingAccountId=str(uuid4()), history="   ")
        assert req.history is None
        assert normalize_history(None) is None


# ---------------------------------------------------------------------------
# Serviço
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self) -> None:
        self.id = uuid4()
        self.organization_id = ORG
        self.closed_at = None
        self.dek_wrapped: bytes | None = None


class _Catalog:
    def __init__(self) -> None:
        self.destinations = {
            kind: MappingDestination(
                id=uuid4(), organization_id=ORG, destination_type=kind, name=kind, active=True
            )
            for kind in ("conta_contabil", "fluxo_de_caixa")
        }
        self.targets: dict[UUID, MappingTarget] = {}

    def plant(self, kind: str, code: str) -> MappingTarget:
        target = MappingTarget(
            id=uuid4(), destination_id=self.destinations[kind].id, code=code, name=code, active=True
        )
        self.targets[target.id] = target
        return target

    async def get_destination_by_type(self, organization_id: UUID, kind: str) -> Any:
        return self.destinations.get(kind)

    async def get_targets_by_codes(self, destination_id: UUID, codes: Any) -> dict[str, Any]:
        wanted = set(codes)
        return {
            t.code: t
            for t in self.targets.values()
            if t.destination_id == destination_id and t.code in wanted
        }


class _Repo:
    """Dublê do repositório de decisões. Preserva a pk (o histórico é cifrado com ela)."""

    def __init__(self, catalog: _Catalog) -> None:
        self.catalog = catalog
        self.decisions: list[ClientMappingDecision] = []

    async def lock_client_destination(self, *_: Any) -> None:
        return None

    async def list_decisions(
        self, client_id: UUID, destination_id: UUID, *, category_codes: Any = None
    ) -> list[ClientMappingDecision]:
        codes = set(category_codes) if category_codes is not None else None
        return [
            d
            for d in self.decisions
            if d.destination_id == destination_id and (codes is None or d.category_code in codes)
        ]

    async def target_codes(self, target_ids: Any) -> dict[UUID, str]:
        return {tid: self.catalog.targets[tid].code for tid in target_ids if tid}

    async def insert_decisions(self, decisions: list[ClientMappingDecision]) -> bool:
        for d in decisions:
            d.created_at = datetime.now(UTC)
        self.decisions.extend(decisions)
        return True

    async def materialized_competences(self, *_: Any) -> list[date]:
        return []


class _ChartRepo:
    def __init__(self) -> None:
        self.accounts: dict[UUID, ClientAccountingAccount] = {}
        self.client_id: UUID | None = None

    def plant(
        self, client_id: UUID, *, account_type: str = "analitica", active: bool = True
    ) -> Any:
        account = ClientAccountingAccount(
            id=uuid4(),
            client_id=client_id,
            code=str(len(self.accounts) + 600),
            name_encrypted="v1:k1:00",
            name_iv="0" * 24,
            account_type=account_type,
            active=active,
        )
        self.accounts[account.id] = account
        return account

    async def get_many(self, client_id: UUID, ids: Any) -> list[ClientAccountingAccount]:
        return [a for i in ids if (a := self.accounts.get(i)) and a.client_id == client_id]


class _World:
    def __init__(self) -> None:
        self.catalog = _Catalog()
        self.repo = _Repo(self.catalog)
        self.chart = _ChartRepo()
        settings = get_settings()
        support = AccountingDecisionSupport(
            None,  # type: ignore[arg-type]
            settings=settings,
            chart=AccountingChartService(
                None,  # type: ignore[arg-type]
                settings=settings,
                repository=self.chart,  # type: ignore[arg-type]
            ),
        )
        self.service = ClientMappingDecisionService(
            self.repo,  # type: ignore[arg-type]
            catalog=self.catalog,  # type: ignore[arg-type]
            catalog_service=MappingCatalogService(self.catalog),  # type: ignore[arg-type]
            accounting=support,
        )
        self.client = _FakeClient()

    async def with_dek(self) -> _World:
        _cipher, wrapped = await new_client_dek(self.client.id, settings=get_settings())
        self.client.dek_wrapped = wrapped
        return self

    async def write(self, kind: str, item: DecisionInput, *, start: date = SET) -> Any:
        return await self.service.write_decisions(
            self.client,  # type: ignore[arg-type]
            kind,
            [item],
            author=_author(),
            effective_from=start,
            today=HOJE,
        )


def _author() -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="staff@hologram.com.br",
        name="Staff",
        role=UserRole.ADMIN.value,
        scope=UserScope.SYSTEM.value,
        client_id=None,
        organization_id=ORG,
    )


def _account_input(account_id: UUID, history: str | None = _SECRET_HISTORY) -> DecisionInput:
    return DecisionInput(
        category_code="Aluguel D",
        decision_type=DecisionType.ALVO,
        target_code=None,
        source_type="arquivo",
        accounting_account_id=account_id,
        history=history,
    )


class TestRegrasDoDestino:
    async def test_catalogo_no_conta_contabil_e_422_proprio(self) -> None:
        w = _World()
        w.catalog.plant("conta_contabil", "662")
        with pytest.raises(CatalogTargetInAccountingDestinationError) as excinfo:
            await w.write(
                "conta_contabil",
                DecisionInput(
                    category_code="c", decision_type=DecisionType.ALVO, target_code="662"
                ),
            )
        assert excinfo.value.status_code == 422
        assert excinfo.value.code is ErrorCode.ALVO_EXIGE_PLANO_CONTABIL
        assert w.repo.decisions == []

    async def test_conta_do_plano_em_outro_destino_e_422_proprio(self) -> None:
        w = _World()
        account = w.chart.plant(w.client.id)
        with pytest.raises(AccountingAccountOutsideDestinationError) as excinfo:
            await w.write("fluxo_de_caixa", _account_input(account.id, history=None))
        assert excinfo.value.status_code == 422
        assert excinfo.value.code is ErrorCode.CONTA_CONTABIL_FORA_DO_DESTINO
        assert w.repo.decisions == []

    async def test_conta_de_outro_cliente_e_404_pelo_validador_unico(self) -> None:
        w = await _World().with_dek()
        alheia = w.chart.plant(uuid4())
        with pytest.raises(AccountingAccountNotFoundError):
            await w.write("conta_contabil", _account_input(alheia.id))
        assert w.repo.decisions == []

    @pytest.mark.parametrize(("kind", "active"), [("sintetica", True), ("analitica", False)])
    async def test_sintetica_ou_inativa_e_422(self, kind: str, *, active: bool) -> None:
        w = await _World().with_dek()
        account = w.chart.plant(w.client.id, account_type=kind, active=active)
        with pytest.raises(AccountingAccountNotPostableError):
            await w.write("conta_contabil", _account_input(account.id))
        assert w.repo.decisions == []

    async def test_nao_mapear_segue_valendo_no_conta_contabil(self) -> None:
        w = _World()
        result = await w.write(
            "conta_contabil",
            DecisionInput(
                category_code="c", decision_type=DecisionType.NAO_MAPEAR, target_code=None
            ),
        )
        assert result.created == 1


class TestHistoricoEVigencia:
    async def test_historico_cifrado_com_a_pk_da_decisao(self) -> None:
        w = await _World().with_dek()
        account = w.chart.plant(w.client.id)
        await w.write("conta_contabil", _account_input(account.id))
        (decision,) = w.repo.decisions
        assert decision.accounting_account_id == account.id
        assert decision.target_id is None
        assert decision.history_encrypted is not None
        assert _SECRET_HISTORY not in decision.history_encrypted
        cipher = await load_client_cipher(w.client, settings=get_settings())
        assert (
            cipher.decrypt(
                decision.history_encrypted,
                decision.history_iv or "",
                field_locator(AAD_DECISION_HISTORY, decision.id),
            )
            == _SECRET_HISTORY
        )

    async def test_trocar_so_o_historico_e_vigencia_nova_e_o_mesmo_pedido_e_no_op(self) -> None:
        w = await _World().with_dek()
        account = w.chart.plant(w.client.id)
        await w.write("conta_contabil", _account_input(account.id))
        same = await w.write("conta_contabil", _account_input(account.id))
        assert (same.created, same.unchanged) == (0, 1)

        novo = await w.write(
            "conta_contabil", _account_input(account.id, history="OUTRO TEXTO"), start=OUT
        )
        assert novo.created == 1
        assert len(w.repo.decisions) == 2
        primeira, segunda = w.repo.decisions
        assert primeira.id != segunda.id
        assert primeira.effective_from == SET
        assert segunda.effective_from == OUT

    async def test_trocar_a_conta_e_vigencia_nova(self) -> None:
        w = await _World().with_dek()
        a1 = w.chart.plant(w.client.id)
        a2 = w.chart.plant(w.client.id)
        await w.write("conta_contabil", _account_input(a1.id))
        result = await w.write("conta_contabil", _account_input(a2.id), start=OUT)
        assert result.created == 1
        assert [d.accounting_account_id for d in w.repo.decisions] == [a1.id, a2.id]

    async def test_legado_do_catalogo_e_marcado_e_a_conta_o_substitui(self) -> None:
        w = await _World().with_dek()
        target = w.catalog.plant("conta_contabil", "662")
        destination = w.catalog.destinations["conta_contabil"]
        w.repo.decisions.append(
            ClientMappingDecision(
                id=uuid4(),
                client_id=w.client.id,
                source_type="arquivo",
                category_code="Aluguel D",
                destination_id=destination.id,
                decision_type="alvo",
                target_id=target.id,
                origin="confirmada",
                effective_from=date(2026, 6, 1),
                author_id=uuid4(),
                created_at=datetime.now(UTC),
            )
        )
        vigentes = await w.service.vigentes(w.client, destination, SET)  # type: ignore[arg-type]
        (view,) = vigentes.values()
        assert view.legacy_catalog_target is True
        assert view.target_code == "662"
        # Nenhuma conversão: a linha legada fica intacta; a conta entra como vigência nova.
        account = w.chart.plant(w.client.id)
        await w.write("conta_contabil", _account_input(account.id))
        legado, nova = w.repo.decisions
        assert legado.target_id == target.id
        assert legado.accounting_account_id is None
        assert nova.accounting_account_id == account.id
        vigentes = await w.service.vigentes(w.client, destination, SET)  # type: ignore[arg-type]
        (view,) = vigentes.values()
        assert view.legacy_catalog_target is False
        assert view.history == _SECRET_HISTORY

    async def test_historico_nunca_vai_para_o_log(self) -> None:
        w = await _World().with_dek()
        account = w.chart.plant(w.client.id)
        with capture_logs() as logs:
            await w.write("conta_contabil", _account_input(account.id))
            await w.write("conta_contabil", _account_input(account.id, history="X"), start=OUT)
            destination = w.catalog.destinations["conta_contabil"]
            await w.service.vigentes(w.client, destination, OUT)  # type: ignore[arg-type]
        assert _SECRET_HISTORY not in repr(logs)
        assert _SECRET_HISTORY not in repr(_account_input(account.id))


# ---------------------------------------------------------------------------
# Aplicação pura
# ---------------------------------------------------------------------------


class _Mv:
    def __init__(self, mid: str, amount: str, category: str) -> None:
        self.source_type = "arquivo"
        self.source_movement_id = mid
        self.source_account_id = None
        self.movement_date = date(2026, 9, 5)
        self.amount = Decimal(amount)
        self.category_code = category
        self.status = "presente"


class _Dec:
    def __init__(self, category: str, *, decision_id: UUID | None = None) -> None:
        self.id = decision_id or uuid4()
        self.source_type = "arquivo"
        self.category_code = category
        self.effective_from = SET
        self.decision_type = "alvo"


class TestAplicacao:
    def test_item_leva_codigo_reduzido_e_vigencia_e_o_token_muda_com_eles(self) -> None:
        decision = _Dec("Aluguel D")
        movements = [_Mv("1", "5466.87", "Aluguel D")]
        key = ("arquivo", "Aluguel D")
        base = apply_mapping(
            movements, [decision], SET, target_code_of={key: None}, accounting_code_of={key: "662"}
        )
        (item,) = base.items
        assert item.accounting_account_code == "662"
        assert item.target_code is None
        assert item.decision_id == decision.id
        token = base.fingerprint(destination_id="d")

        outra_conta = apply_mapping(
            movements, [decision], SET, target_code_of={key: None}, accounting_code_of={key: "663"}
        )
        assert outra_conta.fingerprint(destination_id="d") != token
        outra_vigencia = apply_mapping(
            movements,
            [_Dec("Aluguel D")],
            SET,
            target_code_of={key: None},
            accounting_code_of={key: "662"},
        )
        assert outra_vigencia.fingerprint(destination_id="d") != token
        # Mesma entrada → mesmo token (determinismo preservado).
        again = apply_mapping(
            movements, [decision], SET, target_code_of={key: None}, accounting_code_of={key: "662"}
        )
        assert again.fingerprint(destination_id="d") == token
