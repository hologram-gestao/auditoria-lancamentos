"""Aplicação determinística, prévia e materialização — sem banco (BACK 12.6 — R3/R5).

A função `apply_mapping` é PURA: testada direto, inclusive com a entrada embaralhada.
O serviço roda sobre dublês que registram a ORDEM das escritas — é o que prova, sem
Postgres, que nada materializa sem a prévia confirmada, que a cobertura parcial exige
confirmação, que a versão é N+1 e que a métrica sai DEPOIS do commit.
"""

from __future__ import annotations

import inspect
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.authz import CurrentUser
from app.core.exceptions import (
    ClientClosedError,
    CompetenceBeforeFirstVigenciaError,
    MovementBaseNotSyncedError,
    NoMovementsToMapError,
    PartialCoverageRequiresConfirmationError,
    StaleMappingPreviewError,
)
from app.db.models import (
    Client,
    MappingDestination,
    MaterializedSituation,
    UserRole,
    UserScope,
)
from app.modules.client_mapping import apply as apply_module
from app.modules.client_mapping.apply import apply_mapping
from app.modules.client_mapping.materialization import ClientMappingApplyService
from app.modules.client_movements.repository import MovementSyncState

JUN = date(2026, 6, 1)
SET = date(2026, 9, 1)


@dataclass
class _Mv:
    source_movement_id: str
    amount: Decimal
    category_code: str | None
    status: str = "presente"
    source_type: str = "omie"
    source_account_id: str | None = "111"
    movement_date: date = date(2026, 6, 10)


@dataclass
class _Dec:
    category_code: str
    effective_from: date
    decision_type: str
    target_id: UUID | None = None
    source_type: str = "omie"
    id: UUID = field(default_factory=uuid4)


def _base() -> list[_Mv]:
    return [
        _Mv("1", Decimal("-100.00"), "2.01"),  # alvo
        _Mv("2", Decimal("50.00"), "2.01"),  # alvo (entrada conta pelo |valor|)
        _Mv("3", Decimal("-30.00"), "3.01"),  # nao_mapear
        _Mv("4", Decimal("-20.00"), "4.01"),  # sem decisão
        _Mv("5", Decimal("-5.00"), "5.01"),  # sem decisão (menor)
        _Mv("6", Decimal("-7.00"), None),  # sem categoria
        _Mv("7", Decimal("-999.00"), "2.01", status="ausente_na_origem"),  # fora
    ]


def _decs() -> list[_Dec]:
    return [
        _Dec("2.01", JUN, "alvo", target_id=uuid4()),
        _Dec("3.01", JUN, "nao_mapear"),
    ]


def _codes(decs: list[_Dec]) -> dict[tuple[str, str], str | None]:
    return {("omie", "2.01"): "1.01", ("omie", "3.01"): None}


class TestFuncaoPura:
    def test_quatro_situacoes_valores_e_quantidades(self) -> None:
        result = apply_mapping(_base(), _decs(), JUN, target_code_of=_codes(_decs()))
        t = result.totals
        assert (t[MaterializedSituation.ALVO].amount, t[MaterializedSituation.ALVO].count) == (
            Decimal("150.00"),
            2,
        )
        assert t[MaterializedSituation.NAO_MAPEAR].amount == Decimal("30.00")
        assert t[MaterializedSituation.SEM_DECISAO].amount == Decimal("25.00")
        assert t[MaterializedSituation.SEM_CATEGORIA].amount == Decimal("7.00")
        assert len(result.items) == 6, "ausente_na_origem fica FORA"

    def test_cobertura_e_contra_metrica(self) -> None:
        result = apply_mapping(_base(), _decs(), JUN, target_code_of=_codes(_decs()))
        # numerador 150 + 30 = 180; denominador 180 + 25 = 205 (sem categoria FORA)
        assert (result.numerator, result.denominator) == (Decimal("180.00"), Decimal("205.00"))
        assert result.coverage_pct == Decimal("87.80")
        assert result.nao_mapear_pct == Decimal("14.63")

    def test_categorias_sem_decisao_por_valor_decrescente(self) -> None:
        result = apply_mapping(_base(), _decs(), JUN, target_code_of=_codes(_decs()))
        assert [(u.category_code, u.amount) for u in result.undecided_categories] == [
            ("4.01", Decimal("20.00")),
            ("5.01", Decimal("5.00")),
        ]

    def test_so_movimentos_sem_categoria_da_denominador_zero_sem_erro(self) -> None:
        result = apply_mapping(
            [_Mv("1", Decimal("-10.00"), None), _Mv("2", Decimal("5.00"), None)],
            [],
            JUN,
            target_code_of={},
        )
        assert result.denominator == 0
        assert result.coverage_pct is None
        assert result.nao_mapear_pct is None
        assert result.totals[MaterializedSituation.SEM_CATEGORIA].count == 2

    def test_deterministica_duas_vezes_e_embaralhada(self) -> None:
        decs = _decs()
        primeira = apply_mapping(_base(), decs, JUN, target_code_of=_codes(decs))
        segunda = apply_mapping(_base(), decs, JUN, target_code_of=_codes(decs))
        assert primeira == segunda
        for _ in range(10):
            base = _base()
            random.shuffle(base)
            shuffled = list(decs)
            random.shuffle(shuffled)
            again = apply_mapping(base, shuffled, JUN, target_code_of=_codes(decs))
            assert again == primeira
            assert again.fingerprint(destination_id="d") == primeira.fingerprint(destination_id="d")

    def test_usa_a_vigencia_da_competencia_aplicada(self) -> None:
        """Materializar junho usa a regra de junho mesmo com uma vigência de setembro."""
        junho = _Dec("2.01", JUN, "alvo", target_id=uuid4())
        setembro = _Dec("2.01", SET, "nao_mapear")
        movs = [_Mv("1", Decimal("-10.00"), "2.01")]
        em_junho = apply_mapping(
            movs, [junho, setembro], JUN, target_code_of={("omie", "2.01"): "1.01"}
        )
        em_setembro = apply_mapping(movs, [junho, setembro], SET, target_code_of={})
        assert em_junho.items[0].situation is MaterializedSituation.ALVO
        assert em_junho.items[0].decision_effective_from == JUN
        assert em_setembro.items[0].situation is MaterializedSituation.NAO_MAPEAR

    def test_fingerprint_muda_com_a_base_ou_com_as_decisoes(self) -> None:
        decs = _decs()
        base = apply_mapping(_base(), decs, JUN, target_code_of=_codes(decs))
        outra_base = _base()
        outra_base[0] = _Mv("1", Decimal("-101.00"), "2.01")
        mudou_base = apply_mapping(outra_base, decs, JUN, target_code_of=_codes(decs))
        mudou_decisao = apply_mapping(
            _base(), [*decs, _Dec("4.01", JUN, "nao_mapear")], JUN, target_code_of=_codes(decs)
        )
        fp = base.fingerprint(destination_id="d")
        assert mudou_base.fingerprint(destination_id="d") != fp
        assert mudou_decisao.fingerprint(destination_id="d") != fp
        assert base.fingerprint(destination_id="outro") != fp

    def test_sem_ia_e_sem_rede(self) -> None:
        """A aplicação não importa integração nenhuma — nem IA, nem HTTP, nem banco."""
        source = inspect.getsource(apply_module)
        for proibido in ("httpx", "anthropic", "integrations", "AsyncSession", "sqlalchemy"):
            assert proibido not in source, proibido


# ---------------------------------------------------------------------------
# Serviço (dublês)
# ---------------------------------------------------------------------------


@dataclass
class _Ledger:
    calls: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    inserted: list[tuple[Any, list[dict[str, Any]]]] = field(default_factory=list)


class _Db:
    def __init__(self, ledger: _Ledger) -> None:
        self.ledger = ledger

    async def commit(self) -> None:
        self.ledger.calls.append("commit")

    async def refresh(self, obj: Any) -> None:
        obj.created_at = datetime.now(UTC)


class _Movements:
    def __init__(self, *, synced: bool = True, movs: list[_Mv] | None = None) -> None:
        self.synced = synced
        self.movs = _base() if movs is None else movs

    async def get_sync_state(self, client_id: UUID, competence: date) -> MovementSyncState:
        return MovementSyncState(
            synced_at=datetime.now(UTC) if self.synced else None, sync_failed_at=None
        )

    async def list_for_competence(self, client_id: UUID, competence: date, **_: Any) -> list[_Mv]:
        return [m for m in self.movs if m.status == "presente"]


class _Repo:
    def __init__(self, ledger: _Ledger, decisions: list[_Dec], *, latest: int = 0) -> None:
        self.ledger = ledger
        self.decisions = decisions
        self.latest = latest

    async def list_decisions(self, client_id: UUID, destination_id: UUID) -> list[_Dec]:
        return self.decisions

    async def target_codes(self, ids: Any) -> dict[UUID, str]:
        return dict.fromkeys(ids, "1.01")

    async def latest_version(self, *a: Any) -> int:
        return self.latest

    async def insert_materialization(self, mat: Any, items: list[dict[str, Any]]) -> None:
        mat.id = uuid4()
        self.ledger.calls.append("insert")
        self.ledger.inserted.append((mat, items))


class _Decisions:
    """O destino é o MESMO a cada resolução — como o do banco (o id entra no token)."""

    def __init__(self) -> None:
        self.destinations: dict[str, MappingDestination] = {}

    async def resolve_destination(self, client: Any, kind: str) -> MappingDestination:
        return self.destinations.setdefault(
            kind,
            MappingDestination(
                id=uuid4(), organization_id=uuid4(), destination_type=kind, name=kind, active=True
            ),
        )


class _Events:
    def __init__(self, ledger: _Ledger) -> None:
        self.ledger = ledger

    async def emit_depara_aplicado(self, **kwargs: Any) -> bool:
        self.ledger.calls.append("emit")
        self.ledger.events.append(kwargs)
        return True


def _service(
    *,
    decisions: list[_Dec] | None = None,
    synced: bool = True,
    movs: list[_Mv] | None = None,
    latest: int = 0,
) -> tuple[ClientMappingApplyService, _Ledger]:
    ledger = _Ledger()
    service = ClientMappingApplyService(
        _Db(ledger),  # type: ignore[arg-type]
        repository=_Repo(ledger, _decs() if decisions is None else decisions, latest=latest),  # type: ignore[arg-type]
        movements=_Movements(synced=synced, movs=movs),  # type: ignore[arg-type]
        decisions=_Decisions(),  # type: ignore[arg-type]
        usage_events=_Events(ledger),  # type: ignore[arg-type]
    )
    return service, ledger


def _client(*, closed: bool = False) -> Client:
    client = Client(id=uuid4(), name="C", active=True, created_by=uuid4())
    client.closed_at = datetime.now(UTC) if closed else None
    return client


def _user() -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="a@b.c",
        name="A",
        role=UserRole.ADMIN.value,
        scope=UserScope.SYSTEM.value,
        client_id=None,
        organization_id=uuid4(),
    )


class TestPrevia:
    async def test_nunca_sincronizada_e_409_orientando(self) -> None:
        service, _ = _service(synced=False)
        with pytest.raises(MovementBaseNotSyncedError) as exc:
            await service.preview(_client(), "demonstrativo_contabil", JUN)
        assert exc.value.code.value == "BASE_NAO_SINCRONIZADA"

    async def test_sincronizada_sem_movimento_e_409(self) -> None:
        service, _ = _service(movs=[])
        with pytest.raises(NoMovementsToMapError):
            await service.preview(_client(), "demonstrativo_contabil", JUN)

    async def test_anterior_a_primeira_vigencia_e_409_com_a_mais_antiga(self) -> None:
        service, _ = _service(decisions=[_Dec("2.01", SET, "nao_mapear")])
        with pytest.raises(CompetenceBeforeFirstVigenciaError) as exc:
            await service.preview(_client(), "demonstrativo_contabil", JUN)
        assert exc.value.details == {"earliestCompetence": "2026-09"}

    async def test_destino_sem_nenhuma_decisao_mostra_tudo_pendente(self) -> None:
        service, _ = _service(decisions=[])
        preview = await service.preview(_client(), "demonstrativo_contabil", JUN)
        assert preview.result.coverage_pct == Decimal("0.00")
        assert preview.result.totals[MaterializedSituation.SEM_DECISAO].count == 5

    async def test_so_sem_categoria_e_200_com_denominador_zero(self) -> None:
        service, _ = _service(movs=[_Mv("1", Decimal("-3.00"), None)])
        preview = await service.preview(_client(), "demonstrativo_contabil", JUN)
        assert preview.result.denominator == 0
        assert preview.result.coverage_pct is None


class TestMaterializacao:
    async def test_sem_token_da_previa_nada_materializa(self) -> None:
        service, ledger = _service()
        with pytest.raises(StaleMappingPreviewError):
            await service.materialize(
                _client(),
                "demonstrativo_contabil",
                JUN,
                preview_token="0" * 64,
                confirm_partial_coverage=True,
                author=_user(),
            )
        assert ledger.calls == []

    async def test_cobertura_parcial_sem_confirmacao_e_recusada(self) -> None:
        service, ledger = _service()
        preview = await service.preview(_client(), "demonstrativo_contabil", JUN)
        with pytest.raises(PartialCoverageRequiresConfirmationError) as exc:
            await service.materialize(
                _client(),
                "demonstrativo_contabil",
                JUN,
                preview_token=preview.token,
                confirm_partial_coverage=False,
                author=_user(),
            )
        assert exc.value.details == {"undecidedAmount": "25.00", "undecidedCount": "2"}
        assert ledger.calls == []

    async def test_versao_n_mais_1_registro_de_parcial_e_evento_depois_do_commit(self) -> None:
        service, ledger = _service(latest=1)
        client = _client()
        user = _user()
        preview = await service.preview(client, "demonstrativo_contabil", JUN)
        outcome = await service.materialize(
            client,
            "demonstrativo_contabil",
            JUN,
            preview_token=preview.token,
            confirm_partial_coverage=True,
            author=user,
        )
        assert ledger.calls == ["insert", "commit", "emit"]
        assert outcome.version == 2
        mat, items = ledger.inserted[0]
        assert mat.version == 2
        assert mat.partial_coverage_confirmed is True
        assert mat.undecided_amount == Decimal("25.00")
        assert str(mat.author_id) == user.id
        assert mat.input_hash == preview.token
        assert len(items) == 6
        assert {d["categoryCode"] for d in mat.decisions_used} == {"2.01", "3.01"}
        (evento,) = ledger.events
        assert evento["destino"] == "demonstrativo_contabil"
        assert evento["valor_com_decisao"] == Decimal("180.00")
        assert evento["valor_nao_mapear"] == Decimal("30.00")
        assert evento["valor_sem_decisao"] == Decimal("25.00")
        assert evento["categorias_sem_decisao"] == 2

    async def test_cobertura_total_nao_exige_confirmacao(self) -> None:
        movs = [_Mv("1", Decimal("-10.00"), "2.01"), _Mv("2", Decimal("-1.00"), None)]
        service, ledger = _service(movs=movs)
        preview = await service.preview(_client(), "demonstrativo_contabil", JUN)
        outcome = await service.materialize(
            _client(),
            "demonstrativo_contabil",
            JUN,
            preview_token=preview.token,
            confirm_partial_coverage=False,
            author=_user(),
        )
        assert outcome.partial_coverage_confirmed is False
        assert ledger.calls == ["insert", "commit", "emit"]

    async def test_cliente_encerrado_e_409(self) -> None:
        service, ledger = _service()
        with pytest.raises(ClientClosedError):
            await service.materialize(
                _client(closed=True),
                "demonstrativo_contabil",
                JUN,
                preview_token="0" * 64,
                confirm_partial_coverage=True,
                author=_user(),
            )
        assert ledger.calls == []

    def test_nao_existe_escrita_de_update_ou_delete_de_materializacao(self) -> None:
        from app.modules.client_mapping import repository as repo_module

        source = inspect.getsource(repo_module)
        assert "update(ClientMappingMaterialization" not in source
        assert "delete(ClientMappingMaterialization" not in source
