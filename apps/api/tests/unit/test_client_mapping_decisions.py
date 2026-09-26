"""Decisões do de-para sem banco: vigência pura e as regras do serviço (BACK 12.4).

A vigência é função PURA (`vigencia.py`) — testada direto. O serviço roda sobre
dublês em memória do repositório e do catálogo; o validador de alvo é o REAL
(`MappingCatalogService.require_targets`), então o 422 testado aqui é o mesmo da
rota. A integração (`test_client_mapping_decisions_endpoints.py`) repete o essencial
contra o banco.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.authz import CurrentUser
from app.core.exceptions import (
    ClientClosedError,
    MappingDecisionDuplicateError,
    MappingDestinationNotConfiguredError,
    MappingTargetNotFoundError,
    RetroactiveMappingRequiresConfirmationError,
    RetroactiveOverMaterializedError,
    ValidationAppError,
)
from app.db.models import (
    ChartOfAccountsStatus,
    Client,
    ClientMappingDecision,
    DecisionOrigin,
    DecisionType,
    MappingDestination,
    MappingTarget,
    UserRole,
    UserScope,
)
from app.modules.client_mapping.schemas import DecisionItemRequest, DecisionTypeName
from app.modules.client_mapping.service import (
    INHERIT_STATE_NO_CHART,
    INHERIT_STATE_NOT_INHERITING,
    INHERIT_STATE_OK,
    ClientMappingDecisionService,
    DecisionInput,
)
from app.modules.client_mapping.vigencia import (
    add_months,
    affected_competences,
    months_between,
    resolve_vigente,
    resolve_vigentes,
)
from app.modules.mapping_catalog.service import MappingCatalogService

ORG = uuid4()
HOJE = date(2026, 9, 15)
SET = date(2026, 9, 1)
JUN = date(2026, 6, 1)
JUL = date(2026, 7, 1)
AGO = date(2026, 8, 1)


@dataclass
class _D:
    source_type: str
    category_code: str
    effective_from: date
    label: str = ""


# ---------------------------------------------------------------------------
# Vigência pura
# ---------------------------------------------------------------------------


class TestVigenciaPura:
    def test_duas_vigencias_dois_meses_cada_mes_tem_a_sua(self) -> None:
        """Refazer junho depois da vigência de setembro aplica a regra de JUNHO."""
        junho = _D("omie", "2.04", JUN, "regra de junho")
        setembro = _D("omie", "2.04", SET, "regra de setembro")
        assert resolve_vigente([junho, setembro], JUN) is junho
        assert resolve_vigente([junho, setembro], AGO) is junho
        assert resolve_vigente([junho, setembro], SET) is setembro
        assert resolve_vigente([junho, setembro], date(2026, 12, 1)) is setembro

    def test_antes_da_primeira_vigencia_nao_ha_decisao(self) -> None:
        assert resolve_vigente([_D("omie", "2.04", SET)], AGO) is None

    def test_nao_depende_da_ordem_de_entrada(self) -> None:
        rows = [_D("omie", "2.04", add_months(JUN, i), str(i)) for i in range(6)]
        esperado = resolve_vigente(rows, AGO)
        for _ in range(10):
            random.shuffle(rows)
            assert resolve_vigente(rows, AGO) is esperado

    def test_resolve_todas_as_chaves_de_uma_vez(self) -> None:
        rows = [
            _D("omie", "2.04", JUN, "a-jun"),
            _D("omie", "2.04", SET, "a-set"),
            _D("omie", "3.01", JUL, "b-jul"),
            _D("arquivo", "2.04", JUN, "c-jun"),
        ]
        vigentes = resolve_vigentes(rows, AGO)
        assert {k: v.label for k, v in vigentes.items()} == {
            ("omie", "2.04"): "a-jun",
            ("omie", "3.01"): "b-jul",
            ("arquivo", "2.04"): "c-jun",
        }

    def test_meses(self) -> None:
        assert add_months(date(2026, 12, 1), 1) == date(2027, 1, 1)
        assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
        assert months_between(JUN, AGO) == [JUN, JUL, AGO]
        assert months_between(AGO, JUN) == []

    def test_competencias_afetadas_param_na_proxima_vigencia(self) -> None:
        existentes = [_D("omie", "2.04", AGO)]
        assert affected_competences(existentes, JUN, SET) == [JUN, JUL]
        assert affected_competences([], JUN, SET) == [JUN, JUL, AGO]
        assert affected_competences([], SET, SET) == []


# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------


def _user(role: UserRole = UserRole.ADMIN) -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="x@hologram.com.br",
        name="X",
        role=role.value,
        scope=UserScope.SYSTEM.value,
        client_id=None,
        organization_id=ORG,
    )


def _client(*, closed: bool = False) -> Client:
    client = Client(id=uuid4(), name="Cliente", active=True, created_by=uuid4())
    client.organization_id = ORG
    client.closed_at = datetime.now(UTC) if closed else None
    return client


class _Catalog:
    def __init__(self) -> None:
        self.destinations: dict[str, MappingDestination] = {}
        self.targets: dict[UUID, MappingTarget] = {}
        for kind in ("demonstrativo_contabil", "fluxo_de_caixa"):
            self.destinations[kind] = MappingDestination(
                id=uuid4(), organization_id=ORG, destination_type=kind, name=kind, active=True
            )

    def plant(self, kind: str, code: str, *, active: bool = True) -> MappingTarget:
        target = MappingTarget(
            id=uuid4(),
            destination_id=self.destinations[kind].id,
            code=code,
            name=code,
            active=active,
        )
        self.targets[target.id] = target
        return target

    async def get_destination_by_type(self, organization_id: UUID, kind: str) -> Any:
        return self.destinations.get(kind) if organization_id == ORG else None

    async def get_targets_by_codes(self, destination_id: UUID, codes: Any) -> dict[str, Any]:
        wanted = set(codes)
        return {
            t.code: t
            for t in self.targets.values()
            if t.destination_id == destination_id and t.code in wanted
        }


class _Repo:
    def __init__(self, catalog: _Catalog) -> None:
        self.catalog = catalog
        self.decisions: list[ClientMappingDecision] = []
        self.materialized: set[date] = set()
        self.chart: list[Any] = []

    async def list_decisions(
        self, client_id: UUID, destination_id: UUID, *, category_codes: Any = None
    ) -> list[ClientMappingDecision]:
        codes = set(category_codes) if category_codes is not None else None
        return [
            d
            for d in self.decisions
            if d.client_id == client_id
            and d.destination_id == destination_id
            and (codes is None or d.category_code in codes)
        ]

    async def target_codes(self, target_ids: Any) -> dict[UUID, str]:
        return {tid: self.catalog.targets[tid].code for tid in target_ids if tid}

    async def insert_decisions(self, decisions: list[ClientMappingDecision]) -> None:
        for d in decisions:
            d.id = uuid4()
            d.created_at = datetime.now(UTC)
        self.decisions.extend(decisions)

    async def resolve_inherited(
        self,
        decision_id: UUID,
        *,
        client_id: UUID,
        decision_type: str,
        target_id: UUID | None,
        author_id: UUID,
    ) -> bool:
        for d in self.decisions:
            if d.id == decision_id and d.origin == DecisionOrigin.HERDADA.value:
                d.decision_type = decision_type
                d.target_id = target_id
                d.origin = DecisionOrigin.CONFIRMADA.value
                d.author_id = author_id
                return True
        return False

    async def materialized_competences(
        self, client_id: UUID, destination_id: UUID, competences: Any
    ) -> list[date]:
        return sorted(set(competences) & self.materialized)

    async def active_chart(self, client_id: UUID) -> list[Any]:
        return [r for r in self.chart if r.status == ChartOfAccountsStatus.ATIVA.value]

    async def chart_dre_codes(self, client_id: UUID) -> dict[str, str | None]:
        return {r.category_code: r.dre_code for r in self.chart}


def _world() -> tuple[ClientMappingDecisionService, _Repo, _Catalog]:
    catalog = _Catalog()
    repo = _Repo(catalog)
    service = ClientMappingDecisionService(
        repo,  # type: ignore[arg-type]
        catalog=catalog,  # type: ignore[arg-type]
        catalog_service=MappingCatalogService(catalog),  # type: ignore[arg-type]
    )
    return service, repo, catalog


def _alvo(code: str, category: str = "2.04") -> DecisionInput:
    return DecisionInput(category_code=category, decision_type=DecisionType.ALVO, target_code=code)


def _nao(category: str = "2.04") -> DecisionInput:
    return DecisionInput(
        category_code=category, decision_type=DecisionType.NAO_MAPEAR, target_code=None
    )


def _plant_decision(
    repo: _Repo,
    client: Client,
    catalog: _Catalog,
    *,
    kind: str = "demonstrativo_contabil",
    category: str = "2.04",
    start: date,
    target: MappingTarget | None,
    origin: DecisionOrigin = DecisionOrigin.CONFIRMADA,
) -> ClientMappingDecision:
    decision = ClientMappingDecision(
        id=uuid4(),
        client_id=client.id,
        source_type="omie",
        category_code=category,
        destination_id=catalog.destinations[kind].id,
        decision_type=(DecisionType.ALVO if target else DecisionType.NAO_MAPEAR).value,
        target_id=target.id if target else None,
        origin=origin.value,
        effective_from=start,
        author_id=uuid4(),
    )
    decision.created_at = datetime.now(UTC)
    repo.decisions.append(decision)
    return decision


# ---------------------------------------------------------------------------
# Escrita
# ---------------------------------------------------------------------------


class TestEscrita:
    async def test_padrao_e_a_competencia_corrente_do_servidor(self) -> None:
        service, repo, catalog = _world()
        catalog.plant("demonstrativo_contabil", "1.01")
        client = _client()
        result = await service.write_decisions(
            client, "demonstrativo_contabil", [_alvo("1.01")], author=_user(), today=HOJE
        )
        assert result.effective_from == SET
        assert result.created == 1
        (decisao,) = repo.decisions
        assert decisao.origin == DecisionOrigin.CONFIRMADA.value
        assert decisao.effective_from == SET

    async def test_alterar_cria_vigencia_nova_e_nao_toca_a_anterior(self) -> None:
        service, repo, catalog = _world()
        antigo = catalog.plant("demonstrativo_contabil", "1.01")
        catalog.plant("demonstrativo_contabil", "1.02")
        client = _client()
        anterior = _plant_decision(repo, client, catalog, start=JUN, target=antigo)

        await service.write_decisions(
            client, "demonstrativo_contabil", [_alvo("1.02")], author=_user(), today=HOJE
        )

        assert anterior.target_id == antigo.id, "a vigência anterior é imutável"
        assert sorted(d.effective_from for d in repo.decisions) == [JUN, SET]

    async def test_nao_mapear_e_decisao_persistida(self) -> None:
        service, repo, _ = _world()
        await service.write_decisions(
            _client(), "fluxo_de_caixa", [_nao()], author=_user(), today=HOJE
        )
        (decisao,) = repo.decisions
        assert decisao.decision_type == "nao_mapear"
        assert decisao.target_id is None

    async def test_alvo_inexistente_e_422_nomeando(self) -> None:
        service, repo, _ = _world()
        with pytest.raises(MappingTargetNotFoundError) as exc:
            await service.write_decisions(
                _client(), "demonstrativo_contabil", [_alvo("9.99")], author=_user(), today=HOJE
            )
        assert "9.99" in exc.value.user_message
        assert repo.decisions == []

    async def test_destino_nao_configurado_e_409_nomeando_o_tipo(self) -> None:
        service, _, catalog = _world()
        catalog.destinations["fluxo_de_caixa"].active = False
        for kind in ("natureza_fiscal", "fluxo_de_caixa"):
            with pytest.raises(MappingDestinationNotConfiguredError) as exc:
                await service.write_decisions(_client(), kind, [_nao()], author=_user(), today=HOJE)
            assert exc.value.status_code == 409
            assert kind in exc.value.user_message

    async def test_duplicada_confirmada_na_mesma_vigencia_e_409(self) -> None:
        service, repo, catalog = _world()
        a = catalog.plant("demonstrativo_contabil", "1.01")
        catalog.plant("demonstrativo_contabil", "1.02")
        client = _client()
        _plant_decision(repo, client, catalog, start=SET, target=a)
        with pytest.raises(MappingDecisionDuplicateError) as exc:
            await service.write_decisions(
                client, "demonstrativo_contabil", [_alvo("1.02")], author=_user(), today=HOJE
            )
        assert exc.value.status_code == 409
        assert len(repo.decisions) == 1

    async def test_mesma_decisao_confirmada_repetida_e_idempotente(self) -> None:
        service, repo, catalog = _world()
        a = catalog.plant("demonstrativo_contabil", "1.01")
        client = _client()
        _plant_decision(repo, client, catalog, start=JUN, target=a)
        result = await service.write_decisions(
            client, "demonstrativo_contabil", [_alvo("1.01")], author=_user(), today=HOJE
        )
        assert (result.created, result.unchanged) == (0, 1)

    async def test_herdada_na_mesma_competencia_e_resolvida_pela_pessoa(self) -> None:
        """A herdada é proposta do sistema; a pessoa decide no lugar dela."""
        service, repo, catalog = _world()
        a = catalog.plant("demonstrativo_contabil", "1.01")
        b = catalog.plant("demonstrativo_contabil", "1.02")
        client = _client()
        herdada = _plant_decision(
            repo, client, catalog, start=SET, target=a, origin=DecisionOrigin.HERDADA
        )
        result = await service.write_decisions(
            client, "demonstrativo_contabil", [_alvo("1.02")], author=_user(), today=HOJE
        )
        assert (result.created, result.resolved) == (0, 1)
        assert herdada.origin == DecisionOrigin.CONFIRMADA.value
        assert herdada.target_id == b.id

    async def test_categoria_repetida_no_lote_e_400(self) -> None:
        service, _, _ = _world()
        with pytest.raises(ValidationAppError):
            await service.write_decisions(
                _client(), "fluxo_de_caixa", [_nao(), _nao()], author=_user(), today=HOJE
            )

    async def test_cliente_encerrado_e_409(self) -> None:
        service, _, _ = _world()
        with pytest.raises(ClientClosedError):
            await service.write_decisions(
                _client(closed=True), "fluxo_de_caixa", [_nao()], author=_user(), today=HOJE
            )


class TestRetroatividade:
    async def test_sem_materializacao_a_1a_chamada_lista_e_so_a_confirmada_grava(self) -> None:
        service, repo, _ = _world()
        client = _client()
        with pytest.raises(RetroactiveMappingRequiresConfirmationError) as exc:
            await service.write_decisions(
                client, "fluxo_de_caixa", [_nao()], author=_user(), effective_from=JUN, today=HOJE
            )
        assert exc.value.details == {"competences": "2026-06,2026-07,2026-08"}
        assert "2026-06" in exc.value.user_message
        assert repo.decisions == []

        result = await service.write_decisions(
            client,
            "fluxo_de_caixa",
            [_nao()],
            author=_user(),
            effective_from=JUN,
            confirm_retroactive=True,
            today=HOJE,
        )
        assert result.created == 1
        assert repo.decisions[0].effective_from == JUN

    async def test_sobre_competencia_materializada_e_409_mesmo_confirmando(self) -> None:
        service, repo, _ = _world()
        repo.materialized = {JUL}
        with pytest.raises(RetroactiveOverMaterializedError) as exc:
            await service.write_decisions(
                _client(),
                "fluxo_de_caixa",
                [_nao()],
                author=_user(),
                effective_from=JUN,
                confirm_retroactive=True,
                today=HOJE,
            )
        assert exc.value.details == {"competences": "2026-07"}
        assert repo.decisions == []

    async def test_materializacao_depois_da_proxima_vigencia_nao_bloqueia(self) -> None:
        """Julho já é regido por uma vigência de julho: mudar junho não o atinge."""
        service, repo, catalog = _world()
        client = _client()
        _plant_decision(repo, client, catalog, kind="fluxo_de_caixa", start=JUL, target=None)
        repo.materialized = {JUL, AGO, SET}
        result = await service.write_decisions(
            client,
            "fluxo_de_caixa",
            [_nao()],
            author=_user(),
            effective_from=JUN,
            confirm_retroactive=True,
            today=HOJE,
        )
        assert result.created == 1

    async def test_corrente_e_futuro_nao_sao_retroativos(self) -> None:
        service, repo, _ = _world()
        repo.materialized = {SET}
        result = await service.write_decisions(
            _client(),
            "fluxo_de_caixa",
            [_nao()],
            author=_user(),
            effective_from=date(2026, 10, 1),
            today=HOJE,
        )
        assert result.created == 1


class TestConfirmacaoEmLote:
    async def test_sem_confirmacao_so_conta_e_com_ela_confirma(self) -> None:
        service, repo, catalog = _world()
        a = catalog.plant("demonstrativo_contabil", "1.01")
        client = _client()
        for cat in ("2.01", "2.02", "2.03"):
            _plant_decision(
                repo,
                client,
                catalog,
                category=cat,
                start=JUN,
                target=a,
                origin=DecisionOrigin.HERDADA,
            )
        affected, result = await service.confirm_inherited(
            client, "demonstrativo_contabil", author=_user(), confirm=False, today=HOJE
        )
        assert (affected, result) == (3, None)
        assert len(repo.decisions) == 3

        affected, result = await service.confirm_inherited(
            client, "demonstrativo_contabil", author=_user(), confirm=True, today=HOJE
        )
        assert affected == 3
        assert result is not None
        assert result.created == 3
        novas = [d for d in repo.decisions if d.effective_from == SET]
        assert {d.origin for d in novas} == {"confirmada"}
        assert {d.target_id for d in novas} == {a.id}


class TestHeranca:
    @staticmethod
    def _chart(code: str, dre: str | None, status: str = "ativa") -> Any:
        return SimpleNamespace(category_code=code, dre_code=dre, status=status)

    async def test_so_o_demonstrativo_contabil_herda(self) -> None:
        service, repo, catalog = _world()
        catalog.plant("demonstrativo_contabil", "1.01")
        repo.chart = [self._chart("2.01", "1.01")]
        client = _client()
        outro = await service.inherit(client, "fluxo_de_caixa", author=_user(), today=HOJE)
        assert outro.state == INHERIT_STATE_NOT_INHERITING
        assert repo.decisions == []

        herda = await service.inherit(client, "demonstrativo_contabil", author=_user(), today=HOJE)
        assert herda.state == INHERIT_STATE_OK
        assert herda.created == 1
        assert repo.decisions[0].origin == DecisionOrigin.HERDADA.value

    async def test_sem_destino_declarado_fica_sem_decisao_nunca_nao_mapear(self) -> None:
        service, repo, catalog = _world()
        catalog.plant("demonstrativo_contabil", "1.01")
        repo.chart = [self._chart("2.01", "1.01"), self._chart("9.01", None)]
        result = await service.inherit(
            _client(), "demonstrativo_contabil", author=_user(), today=HOJE
        )
        assert (result.created, result.without_dre) == (1, 1)
        assert [d.category_code for d in repo.decisions] == ["2.01"]
        assert all(d.decision_type != "nao_mapear" for d in repo.decisions)

    async def test_dre_sem_alvo_no_catalogo_e_reportado_e_nada_e_criado(self) -> None:
        service, repo, _ = _world()
        repo.chart = [self._chart("2.01", "7.77")]
        result = await service.inherit(
            _client(), "demonstrativo_contabil", author=_user(), today=HOJE
        )
        assert result.created == 0
        assert result.missing_target_categories == ["2.01"]
        assert result.missing_target_codes == ["7.77"]
        assert repo.decisions == []

    async def test_idempotente(self) -> None:
        service, repo, catalog = _world()
        catalog.plant("demonstrativo_contabil", "1.01")
        repo.chart = [self._chart("2.01", "1.01")]
        client = _client()
        await service.inherit(client, "demonstrativo_contabil", author=_user(), today=HOJE)
        again = await service.inherit(client, "demonstrativo_contabil", author=_user(), today=HOJE)
        assert (again.created, again.already_decided) == (0, 1)
        assert len(repo.decisions) == 1

    async def test_sem_plano_de_contas_e_estado_explicativo_sem_erro(self) -> None:
        service, _, _ = _world()
        result = await service.inherit(
            _client(), "demonstrativo_contabil", author=_user(), today=HOJE
        )
        assert result.state == INHERIT_STATE_NO_CHART
        assert result.created == 0

    async def test_resincronizacao_nao_reescreve_e_sinaliza_divergencia(self) -> None:
        """R7: `dre_code` mudou na origem → decisão confirmada INTACTA, leitura sinaliza."""
        service, repo, catalog = _world()
        a = catalog.plant("demonstrativo_contabil", "1.01")
        catalog.plant("demonstrativo_contabil", "1.02")
        client = _client()
        confirmada = _plant_decision(repo, client, catalog, category="2.01", start=JUN, target=a)
        repo.chart = [self._chart("2.01", "1.02")]  # a origem mudou 1.01 → 1.02

        await service.inherit(client, "demonstrativo_contabil", author=_user(), today=HOJE)
        vigentes = await service.vigentes(
            client, catalog.destinations["demonstrativo_contabil"], SET
        )

        assert confirmada.target_id == a.id, "decisão confirmada não é reescrita"
        view = vigentes[("omie", "2.01")]
        assert view.divergent is True
        assert (view.target_code, view.origin_dre_code) == ("1.01", "1.02")
        assert view.origin == "confirmada"


class TestBorda:
    @pytest.mark.parametrize(
        "body",
        [
            {"categoryCode": "2.04", "decision": "alvo"},
            {"categoryCode": "2.04", "decision": "nao_mapear", "targetCode": "1.01"},
            {"categoryCode": "2.04", "decision": "talvez"},
            {"categoryCode": "2 04", "decision": "nao_mapear"},
        ],
    )
    def test_forma_invalida_e_recusada_na_borda(self, body: dict[str, Any]) -> None:
        with pytest.raises(ValueError, match=r"."):
            DecisionItemRequest.model_validate(body)

    def test_vocabulario_da_borda_espelha_o_enum(self) -> None:
        from typing import get_args

        assert set(get_args(DecisionTypeName)) == {t.value for t in DecisionType}
