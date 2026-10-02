"""Instrumentação do outcome da Sprint 16 sem banco (BACK 16.4).

- completude de partida: função PURA sobre as linhas, reusando o predicado ÚNICO da
  16.3 e o `_pct` da S12 (tudo completo = 100,00; uma sem histórico = a fração certa
  pelo Σ|valor|; sem linha com alvo = `None`, nunca "0%");
- `plano_contabil_importado`: as QUATRO chaves exatas (só id e contagens), fora da
  dedup e fora dos eventos que o browser pode mandar; emitido UMA vez depois do
  commit da importação; recusa 422 não emite; falha do emissor não desfaz nada.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, get_args
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.authz import CurrentUser
from app.core.config import get_settings
from app.core.crypto_service import new_client_dek
from app.core.exceptions import FileLinesInvalidError
from app.db.models import UserRole, UserScope
from app.db.models.usage_event import DEDUPED_EVENT_NAMES
from app.modules.client_accounting_chart.service import AccountingChartService
from app.modules.client_accounting_chart.sheet import ChartLayout
from app.modules.client_mapping import completeness as completeness_module
from app.modules.client_mapping.completeness import partida_completeness
from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    ChartImportLayout,
    PlanoContabilImportadoProps,
    UsageEventName,
)
from app.modules.usage_events.service import UsageEventService

pytestmark = pytest.mark.unit


@dataclass(frozen=True)
class _Line:
    amount: Decimal
    situation: str = "alvo"
    source_type: str = "arquivo"
    source_account_id: str | None = None
    accounting_account_code: str | None = "662"
    bank_account_code: str | None = "649"
    history_present: bool | None = True


class TestCompletude:
    def test_tudo_completo_e_100(self) -> None:
        result = partida_completeness([_Line(Decimal("10.00")), _Line(Decimal("-5.00"))])
        assert result.pct == Decimal("100.00")
        assert result.complete_amount == result.target_amount == Decimal("15.00")

    def test_sem_historico_numa_linha_e_a_fracao_pelo_valor_absoluto(self) -> None:
        result = partida_completeness(
            [_Line(Decimal("75.00")), _Line(Decimal("-25.00"), history_present=False)]
        )
        assert result.pct == Decimal("75.00")
        assert (result.complete_amount, result.target_amount) == (
            Decimal("75.00"),
            Decimal("100.00"),
        )

    def test_quantizacao_half_even(self) -> None:
        result = partida_completeness(
            [_Line(Decimal("1.00")), _Line(Decimal("2.00"), bank_account_code=None)]
        )
        assert result.pct == Decimal("33.33")

    def test_sem_linha_com_alvo_e_none(self) -> None:
        assert partida_completeness([]).pct is None
        so_nao_mapear = partida_completeness(
            [_Line(Decimal("10.00"), situation="nao_mapear", accounting_account_code=None)]
        )
        assert so_nao_mapear.pct is None
        assert so_nao_mapear.target_amount == Decimal("0.00")

    def test_linhas_fora_do_alvo_nao_entram_no_denominador(self) -> None:
        result = partida_completeness(
            [
                _Line(Decimal("10.00")),
                _Line(Decimal("90.00"), situation="sem_decisao", accounting_account_code=None),
            ]
        )
        assert result.target_amount == Decimal("10.00")
        assert result.pct == Decimal("100.00")

    def test_legado_do_catalogo_conta_como_incompleto(self) -> None:
        result = partida_completeness(
            [_Line(Decimal("10.00")), _Line(Decimal("10.00"), accounting_account_code=None)]
        )
        assert result.pct == Decimal("50.00")

    def test_reusa_o_predicado_unico_e_o_pct_da_s12(self) -> None:
        source = inspect.getsource(completeness_module)
        assert "is_partida_completa(line)" in source
        assert "_pct(complete, target)" in source
        assert "history_present" not in source.split("def partida_completeness", 1)[1]


class TestEventoProps:
    def test_as_cinco_chaves_exatas(self) -> None:
        assert set(PlanoContabilImportadoProps.model_fields) == {
            "client_id",
            "contas",
            "contas_novas",
            "contas_inativadas",
            "layout",
        }

    def test_layout_e_o_mesmo_vocabulario_do_leitor(self) -> None:
        # Duas listas (a da métrica e a do leitor) só não envelhecem separadas se
        # um teste as amarrar.
        assert get_args(ChartImportLayout) == get_args(ChartLayout.__value__)

    def test_layout_fora_do_vocabulario_e_recusado(self) -> None:
        with pytest.raises(ValidationError):
            PlanoContabilImportadoProps(
                client_id=uuid4(),
                contas=1,
                contas_novas=1,
                contas_inativadas=0,
                layout="Contas importação.xlsx",  # type: ignore[arg-type]
            )

    def test_nao_aceita_codigo_nem_nome(self) -> None:
        with pytest.raises(ValidationError):
            PlanoContabilImportadoProps(
                client_id=uuid4(),
                contas=1,
                contas_novas=1,
                contas_inativadas=0,
                layout="modelo",
                codigo="649",  # type: ignore[call-arg]
            )

    def test_sem_dedup_e_fora_do_browser(self) -> None:
        event = UsageEventName.PLANO_CONTABIL_IMPORTADO
        assert event.value == "plano_contabil_importado"
        assert event.value not in DEDUPED_EVENT_NAMES
        assert event not in CLIENT_EMITTED_EVENTS


class _Events:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    async def emit_plano_contabil_importado(self, **kwargs: Any) -> bool:
        if self.fail:
            raise RuntimeError("sink fora do ar")
        self.calls.append(kwargs)
        return True


class _Db:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Repo:
    async def lock_client_chart(self, client_id: UUID) -> None:
        return None

    async def list_all(self, client_id: UUID) -> list[Any]:
        return []

    async def insert_accounts(self, rows: Any) -> None:
        return None

    async def update_accounts(self, rows: Any) -> None:
        return None

    async def deactivate_absent(self, client_id: UUID, **_: Any) -> int:
        return 0


class _Client:
    def __init__(self) -> None:
        self.id = uuid4()
        self.closed_at = None
        self.dek_wrapped: bytes | None = None


def _actor() -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="staff@hologram.com.br",
        name="Staff",
        role=UserRole.ADMIN.value,
        scope=UserScope.SYSTEM.value,
        client_id=None,
        organization_id=uuid4(),
    )


async def _import(events: _Events, content: bytes) -> tuple[Any, _Db, _Client]:
    client = _Client()
    _cipher, client.dek_wrapped = await new_client_dek(client.id, settings=get_settings())
    db = _Db()
    service = AccountingChartService(
        db,  # type: ignore[arg-type]
        settings=get_settings(),
        repository=_Repo(),  # type: ignore[arg-type]
        usage_events=events,  # type: ignore[arg-type]
    )
    result = await service.import_sheet(client, actor=_actor(), content=content)  # type: ignore[arg-type]
    return result, db, client


_PLANO = b"codigo_reduzido;nome;tipo\n649;Banco;analitica\n650;Rendimentos;analitica\n"


class TestEmissao:
    async def test_importacao_emite_uma_vez_depois_do_commit_com_as_contagens(self) -> None:
        events = _Events()
        result, db, client = await _import(events, _PLANO)
        assert db.commits == 1
        assert events.calls == [
            {
                "client_id": client.id,
                "contas": 2,
                "contas_novas": 2,
                "contas_inativadas": 0,
                "layout": "modelo",
            }
        ]
        assert (result.accounts, result.new, result.inactivated) == (2, 2, 0)

    async def test_recusa_nao_emite(self) -> None:
        events = _Events()
        with pytest.raises(FileLinesInvalidError):
            await _import(events, b"codigo_reduzido;nome;tipo\n649;A;analitica\n649;B;analitica\n")
        assert events.calls == []

    async def test_falha_do_emissor_nao_desfaz_a_importacao(self) -> None:
        result, db, _client = await _import(_Events(fail=True), _PLANO)
        assert db.commits == 1
        assert result.accounts == 2

    async def test_props_invalidas_viram_warning_sem_500(self) -> None:
        """O emissor real monta as props no caminho fail-soft (`_props_or_none`)."""

        class _Repo2:
            async def insert_ignore_duplicate(self, **_: Any) -> bool:  # pragma: no cover
                return True

        service = UsageEventService(_Repo2())  # type: ignore[arg-type]
        ok = await service.emit_plano_contabil_importado(
            client_id=uuid4(), contas=-1, contas_novas=0, contas_inativadas=0, layout="modelo"
        )
        assert ok is False
