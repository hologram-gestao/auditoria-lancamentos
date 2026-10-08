"""Resumo do cliente (86e3k1q3j): cada bloco com o repositório mockado.

O que é SQL (compra com posting confirmado fora, estorno fora, sessão apagada fora,
tenant no `WHERE`) é provado contra Postgres em
`tests/integration/test_client_summary_endpoint.py`. Aqui fica o que o service
decide: os zeros, as quatro chaves de status sempre presentes, a cobertura da
`apply_mapping`, o bloco da carteira `null` sem a permissão, e nenhum texto livre
no payload.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authz import CurrentUser
from app.db.models import DecisionOrigin, DecisionType, UserRole, UserScope
from app.modules.client_mapping.listing import SituationCounts, situation_counts, situation_of
from app.modules.client_summary.repository import AnomalyCount, CategoryTotal
from app.modules.client_summary.service import HABITUAL_MONTHS, ClientSummaryService
from app.modules.client_titles.aging import OVERDUE_BUCKETS
from app.modules.client_titles.repository import AgingTotals, TitlesSummary

MONTH = date(2026, 9, 1)
CLIENT_NAME = "Razao Social Sigilosa LTDA"


def _user(role: UserRole = UserRole.CLIENT_OPERATOR) -> CurrentUser:
    client_scoped = role in (UserRole.CLIENT_MANAGER, UserRole.CLIENT_OPERATOR)
    return CurrentUser(
        id=str(uuid4()),
        email="x@x.com",
        name="X",
        role=role.value,
        scope=(UserScope.CLIENT if client_scoped else UserScope.SYSTEM).value,
        client_id=uuid4() if client_scoped else None,
        organization_id=uuid4(),
        organization_name="Org",
    )


def _client() -> Any:
    return SimpleNamespace(id=uuid4(), organization_id=uuid4(), name=CLIENT_NAME)


def _aging(*, vencido: str, qtd: int) -> AgingTotals:
    return AgingTotals(
        total_em_aberto=Decimal(vencido),
        total_a_vencer=Decimal("0.00"),
        total_vencido=Decimal(vencido),
        baldes=dict.fromkeys(OVERDUE_BUCKETS, Decimal("0.00")),
        qtd_em_aberto=qtd,
        qtd_a_vencer=0,
        qtd_vencido=qtd,
        baldes_qtd=dict.fromkeys(OVERDUE_BUCKETS, 0),
    )


def _titles_summary(*, synced: bool = True) -> TitlesSummary:
    return TitlesSummary(
        a_pagar=_aging(vencido="4380.00", qtd=3),
        a_receber=_aging(vencido="82865.50", qtd=12),
        synced_at=datetime(2026, 10, 7, 9, 10, tzinfo=UTC) if synced else None,
        sync_failed_at=None,
        referencia=date(2026, 10, 7),
    )


def _decision(code: str, *, decision_type: DecisionType, origin: DecisionOrigin) -> Any:
    return SimpleNamespace(
        id=uuid4(),
        source_type="omie",
        category_code=code,
        effective_from=date(2026, 1, 1),
        decision_type=decision_type.value,
        origin=origin.value,
        target_id=None,
    )


def _service(
    *,
    repo: AsyncMock | None = None,
    destinations: list[Any] | None = None,
    decisions: list[Any] | None = None,
    chart_codes: list[str] | None = None,
    movement_keys: set[tuple[str, str]] | None = None,
    latest_version: int = 0,
    titles: TitlesSummary | None = None,
) -> tuple[ClientSummaryService, AsyncMock, AsyncMock]:
    repo = repo or _empty_repo()
    mapping = AsyncMock()
    mapping.list_decisions.return_value = decisions or []
    mapping.chart_category_codes.return_value = chart_codes or []
    mapping.movement_category_keys.return_value = movement_keys or set()
    mapping.latest_version.return_value = latest_version
    catalog = AsyncMock()
    catalog.list_destinations.return_value = [
        SimpleNamespace(destination=d, targets_count=0) for d in (destinations or [])
    ]
    titles_service = AsyncMock()
    titles_service.summary.return_value = titles or _titles_summary(synced=False)
    service = ClientSummaryService(repo, mapping=mapping, catalog=catalog, titles=titles_service)
    return service, mapping, titles_service


def _empty_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.session_status_counts.return_value = {}
    repo.accounts_total.return_value = 0
    repo.accounts_with_session.return_value = 0
    repo.habitual_account_ids.return_value = []
    repo.anomaly_counts.return_value = []
    repo.card_purchases_to_post.return_value = (0, Decimal("0.00"))
    repo.latest_session.return_value = None
    repo.movement_totals_by_category.return_value = []
    return repo


def _dump(response: Any) -> dict[str, Any]:
    return response.model_dump(mode="json", by_alias=True)  # type: ignore[no-any-return]


async def test_cliente_sem_nada_responde_zeros_e_nulos() -> None:
    service, _mapping, _titles = _service()
    data = _dump(await service.summary(_client(), _user(), MONTH))

    assert data["referenceMonth"] == "2026-09"
    assert data["reconciliations"] == {
        "accountsTotal": 0,
        "accountsWithSession": 0,
        "byStatus": {"processing": 0, "reviewing": 0, "done": 0, "error": 0},
        "habitualAccountIds": [],
    }
    assert data["anomalies"] == {"openTotal": 0, "byType": [], "resolvedInMonth": 0}
    assert data["cardPurchasesToPost"] == {"count": 0, "totalAmount": "0.00"}
    assert data["mapping"] == []
    assert data["latestSession"] is None
    # Carteira nunca sincronizada: zeros COM o sinal de que nunca houve consulta.
    assert data["titles"]["neverSynced"] is True
    assert data["titles"]["overdueCount"] == 15


async def test_status_ausente_vira_zero_e_as_quatro_chaves_existem() -> None:
    repo = _empty_repo()
    repo.session_status_counts.return_value = {"reviewing": 1, "done": 2}
    repo.accounts_total.return_value = 3
    repo.accounts_with_session.return_value = 2
    service, _m, _t = _service(repo=repo)

    data = _dump(await service.summary(_client(), _user(), MONTH))

    assert data["reconciliations"]["byStatus"] == {
        "processing": 0,
        "reviewing": 1,
        "done": 2,
        "error": 0,
    }
    assert data["reconciliations"]["accountsTotal"] == 3
    assert data["reconciliations"]["accountsWithSession"] == 2


async def test_contas_habituais_pedem_a_janela_de_tres_meses_e_saem_so_numeros() -> None:
    repo = _empty_repo()
    repo.habitual_account_ids.return_value = [101, 202, 303]
    service, _m, _t = _service(repo=repo)
    user = _user()
    client = _client()

    data = _dump(await service.summary(client, user, MONTH))

    # A janela é o mês de referência e os três anteriores, inclusivos.
    assert HABITUAL_MONTHS == 3
    repo.habitual_account_ids.assert_awaited_once_with(client.id, date(2026, 6, 1), MONTH, user)
    assert data["reconciliations"]["habitualAccountIds"] == [101, 202, 303]
    assert all(isinstance(i, int) for i in data["reconciliations"]["habitualAccountIds"])


async def test_janela_das_contas_habituais_atravessa_a_virada_do_ano() -> None:
    repo = _empty_repo()
    service, _m, _t = _service(repo=repo)
    user = _user()
    client = _client()

    await service.summary(client, user, date(2026, 2, 1))

    repo.habitual_account_ids.assert_awaited_once_with(
        client.id, date(2025, 11, 1), date(2026, 2, 1), user
    )


async def test_anomalias_abertas_por_codigo_e_resolvidas_no_mes() -> None:
    repo = _empty_repo()
    repo.anomaly_counts.return_value = [
        AnomalyCount(code="missing_in_omie", open=4, resolved=10),
        AnomalyCount(code="wrong_date", open=3, resolved=9),
        # Tipo só com resolvidas: não entra em `byType` (lista de PENDÊNCIA).
        AnomalyCount(code="duplicate", open=0, resolved=0),
    ]
    service, _m, _t = _service(repo=repo)

    data = _dump(await service.summary(_client(), _user(), MONTH))

    assert data["anomalies"] == {
        "openTotal": 7,
        "byType": [
            {"code": "missing_in_omie", "count": 4},
            {"code": "wrong_date", "count": 3},
        ],
        "resolvedInMonth": 19,
    }


async def test_compras_a_lancar_saem_do_repositorio_como_string_decimal() -> None:
    repo = _empty_repo()
    repo.card_purchases_to_post.return_value = (4, Decimal("-2318.40"))
    service, _m, _t = _service(repo=repo)

    data = _dump(await service.summary(_client(), _user(), MONTH))

    assert data["cardPurchasesToPost"] == {"count": 4, "totalAmount": "-2318.40"}


async def test_titulos_somam_os_dois_lados_e_trazem_o_relogio() -> None:
    service, _m, _t = _service(titles=_titles_summary())
    data = _dump(await service.summary(_client(), _user(), MONTH))

    assert data["titles"] == {
        "overdueCount": 15,
        "overdueTotal": "87245.50",
        "aPagar": {"overdueCount": 3, "overdueTotal": "4380.00"},
        "aReceber": {"overdueCount": 12, "overdueTotal": "82865.50"},
        "syncedAt": "2026-10-07T09:10:00Z",
        "neverSynced": False,
    }


async def test_sem_a_celula_da_carteira_o_bloco_vem_nulo_e_nem_consulta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Hoje os cinco papéis têm `view_client_receivables`; o dia em que a célula
    # fechar para algum, o resumo devolve `null` em vez de 403.
    monkeypatch.setattr(
        "app.modules.client_summary.service.has_permission", lambda _user, _perm: False
    )
    service, _m, titles_service = _service(titles=_titles_summary())

    data = _dump(await service.summary(_client(), _user(), MONTH))

    assert data["titles"] is None
    titles_service.summary.assert_not_awaited()


async def test_de_para_por_destino_ativo_com_cobertura_da_apply_mapping() -> None:
    repo = _empty_repo()
    repo.movement_totals_by_category.return_value = [
        CategoryTotal("omie", "1.01", Decimal("100.00"), MONTH),
        CategoryTotal("omie", "2.02", Decimal("300.00"), MONTH),
        # Sem categoria: buraco de ingestão, fora do denominador.
        CategoryTotal("omie", None, Decimal("999.00"), MONTH),
    ]
    ativo = SimpleNamespace(id=uuid4(), destination_type="demonstrativo_contabil", active=True)
    inativo = SimpleNamespace(id=uuid4(), destination_type="fluxo_de_caixa", active=False)
    service, mapping, _t = _service(
        repo=repo,
        destinations=[ativo, inativo],
        decisions=[
            _decision(
                "1.01", decision_type=DecisionType.NAO_MAPEAR, origin=DecisionOrigin.CONFIRMADA
            )
        ],
        movement_keys={("omie", "1.01"), ("omie", "2.02")},
        chart_codes=["3.03"],
        latest_version=2,
    )

    data = _dump(await service.summary(_client(), _user(), MONTH))

    # Inativo fica de fora; "2.02" e "3.03" (do plano) estão sem decisão; a
    # cobertura é 100 / (100 + 300) = 25%, a MESMA conta da prévia.
    assert data["mapping"] == [
        {
            "destinationCode": "demonstrativo_contabil",
            "withoutDecision": 2,
            "coveragePct": "25.0",
            "materialized": True,
        }
    ]
    mapping.list_decisions.assert_awaited_once()


async def test_de_para_sem_movimento_tem_cobertura_nula_nunca_zero() -> None:
    destino = SimpleNamespace(id=uuid4(), destination_type="conta_contabil", active=True)
    service, _m, _t = _service(destinations=[destino], chart_codes=["1.01"])

    data = _dump(await service.summary(_client(), _user(), MONTH))

    assert data["mapping"] == [
        {
            "destinationCode": "conta_contabil",
            "withoutDecision": 1,
            "coveragePct": None,
            "materialized": False,
        }
    ]


async def test_cobertura_tem_uma_casa() -> None:
    repo = _empty_repo()
    repo.movement_totals_by_category.return_value = [
        CategoryTotal("omie", "1", Decimal("1.00"), MONTH),
        CategoryTotal("omie", "2", Decimal("2.00"), MONTH),
    ]
    destino = SimpleNamespace(id=uuid4(), destination_type="demonstrativo_contabil", active=True)
    service, _m, _t = _service(
        repo=repo,
        destinations=[destino],
        decisions=[_decision("1", decision_type=DecisionType.ALVO, origin=DecisionOrigin.HERDADA)],
        movement_keys={("omie", "1"), ("omie", "2")},
    )

    data = _dump(await service.summary(_client(), _user(), MONTH))

    # 1/3 = 33.33% na prévia; no resumo, uma casa.
    assert data["mapping"][0]["coveragePct"] == "33.3"


async def test_ultima_sessao_so_com_ids_e_enums() -> None:
    repo = _empty_repo()
    session_id = uuid4()
    repo.latest_session.return_value = SimpleNamespace(
        id=session_id,
        reference_month=date(2026, 9, 1),
        status="reviewing",
        account_type="credit_card",
        created_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    )
    service, _m, _t = _service(repo=repo)

    data = _dump(await service.summary(_client(), _user(), MONTH))

    assert data["latestSession"] == {
        "id": str(session_id),
        "referenceMonth": "2026-09",
        "status": "reviewing",
        "accountType": "credit_card",
        "createdAt": "2026-10-01T12:00:00Z",
    }


async def test_payload_nao_carrega_nome_do_cliente() -> None:
    repo = _empty_repo()
    repo.anomaly_counts.return_value = [AnomalyCount(code="wrong_date", open=1, resolved=0)]
    service, _m, _t = _service(repo=repo, titles=_titles_summary())

    response = await service.summary(_client(), _user(), MONTH)

    assert CLIENT_NAME not in response.model_dump_json(by_alias=True)


async def test_situation_counts_igual_a_contagem_da_lista() -> None:
    """A contagem sem view bate com `SituationCounts.of` sobre as mesmas situações."""
    decisions = [
        _decision("1", decision_type=DecisionType.ALVO, origin=DecisionOrigin.HERDADA),
        _decision("2", decision_type=DecisionType.ALVO, origin=DecisionOrigin.CONFIRMADA),
        _decision("3", decision_type=DecisionType.NAO_MAPEAR, origin=DecisionOrigin.CONFIRMADA),
    ]
    mapping = AsyncMock()
    mapping.chart_category_codes.return_value = ["4"]
    mapping.movement_category_keys.return_value = {("omie", "5")}

    counts = await situation_counts(mapping, uuid4(), decisions, MONTH)

    by_key = {(d.source_type, d.category_code): d for d in decisions}
    keys = [("omie", "1"), ("omie", "2"), ("omie", "3"), ("omie", "4"), ("omie", "5")]
    expected = SituationCounts.of_situations([situation_of(by_key.get(k)) for k in keys])
    assert counts == expected
    assert (counts.herdada, counts.confirmada, counts.nao_mapear, counts.sem_decisao) == (
        1,
        1,
        1,
        2,
    )
