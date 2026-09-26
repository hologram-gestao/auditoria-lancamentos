"""O padrão ÚNICO da competência recusa na borda o que o `date` não representa.

Retrabalho da BACK 12.2: `^\\d{4}-…` aceitava `0000-06` (`date(0, 6, 1)` → 500) e
`0999-06` (o `strftime` devolve `'999-06'`, que as props do evento recusam → 500
DEPOIS de gravar a base). Todas as rotas da sprint que recebem competência herdam o
padrão, então o teste de forma é aqui e nos schemas de borda.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.modules.client_mapping.schemas import DecisionWriteRequest, MaterializationRequest
from app.modules.client_movements.competence import (
    COMPETENCE_PATTERN,
    current_competence,
    format_competence,
    parse_competence,
    today_brt,
)
from app.modules.client_movements.schemas import MovementsSyncRequest

_INVALIDAS = ["0000-06", "0001-06", "0999-12", "2026-13", "2026-00", "26-06", "2026-6", ""]
_VALIDAS = ["1000-01", "2026-06", "9999-12"]
_ANOS_FORA = ["0000-06", "0999-06"]


@pytest.mark.parametrize("valor", _INVALIDAS)
def test_padrao_recusa_forma_invalida(valor: str) -> None:
    assert re.fullmatch(COMPETENCE_PATTERN, valor) is None


@pytest.mark.parametrize("valor", _VALIDAS)
def test_tudo_que_o_padrao_aceita_faz_ida_e_volta_sem_erro(valor: str) -> None:
    assert re.fullmatch(COMPETENCE_PATTERN, valor) is not None
    assert format_competence(parse_competence(valor)) == valor


def test_parse_devolve_o_dia_1() -> None:
    assert parse_competence("2026-06") == date(2026, 6, 1)


class TestCompetenciaCorrenteNoFusoDoBrasil:
    """Retrabalho 12.4: das 21h às 23h59 BRT do último dia, o UTC já está no mês seguinte."""

    def test_30_09_23h30_brt_ainda_e_setembro(self) -> None:
        agora = datetime(2026, 10, 1, 2, 30, tzinfo=UTC)  # = 30/09 23:30 BRT
        assert today_brt(agora) == date(2026, 9, 30)
        assert current_competence(today_brt(agora)) == date(2026, 9, 1)

    def test_01_10_00h00_brt_ja_e_outubro(self) -> None:
        agora = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)  # = 01/10 00:00 BRT
        assert current_competence(today_brt(agora)) == date(2026, 10, 1)

    def test_sem_argumento_usa_o_relogio(self) -> None:
        assert current_competence().day == 1


@pytest.mark.parametrize("valor", _ANOS_FORA)
def test_corpo_da_sincronizacao_recusa_ano_fora_da_faixa(valor: str) -> None:
    with pytest.raises(ValidationError):
        MovementsSyncRequest.model_validate({"competence": valor})


@pytest.mark.parametrize("valor", _ANOS_FORA)
def test_effective_from_da_decisao_recusa_ano_fora_da_faixa(valor: str) -> None:
    with pytest.raises(ValidationError):
        DecisionWriteRequest.model_validate(
            {"categoryCode": "2.10.01", "decision": "nao_mapear", "effectiveFrom": valor}
        )


@pytest.mark.parametrize("valor", _ANOS_FORA)
def test_competencia_da_materializacao_recusa_ano_fora_da_faixa(valor: str) -> None:
    with pytest.raises(ValidationError):
        MaterializationRequest.model_validate({"competence": valor, "previewToken": "a" * 64})
