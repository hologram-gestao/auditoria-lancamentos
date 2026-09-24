"""Unit — o que dá para provar do relatório de recebíveis SEM banco (BACK 15.2).

A classificação em si (`ClientTitlesRepository.receivables_report`) é uma query
SQL com `DISTINCT ON` + `CASE` + agregação — só a integração prova o
comportamento real. Aqui ficam as duas peças PURAS que sustentam a métrica:

    - `_to_cents`: dinheiro em CENTAVOS no sink (§3.4), nunca `Decimal`/float;
    - o vocabulário fechado dos dois grupos e o zero de um grupo sem título.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.db.models.title_context import CONTEXT_TYPES_NOT_DELINQUENT, TitleContextType
from app.modules.client_titles.aging import OVERDUE_BUCKETS
from app.modules.client_titles.repository import (
    RECEIVABLES_GROUP_INADIMPLENCIA,
    RECEIVABLES_GROUP_VENCIDO_COM_CONTEXTO,
    ReceivablesGroupTotals,
    _zeroed_group_totals,
)
from app.modules.client_titles.service import _to_cents

pytestmark = pytest.mark.unit


class TestToCents:
    @pytest.mark.parametrize(
        ("valor", "centavos"),
        [
            (Decimal("0.00"), 0),
            (Decimal("1.00"), 100),
            (Decimal("107413.10"), 10_741_310),
            (Decimal("0.01"), 1),
            (Decimal("99999999.99"), 9_999_999_999),
        ],
    )
    def test_conversao_exata(self, valor: Decimal, centavos: int) -> None:
        assert _to_cents(valor) == centavos

    def test_devolve_int_nunca_decimal(self) -> None:
        assert isinstance(_to_cents(Decimal("42.50")), int)


class TestVocabularioDosGrupos:
    def test_os_dois_grupos_sao_strings_distintas(self) -> None:
        assert RECEIVABLES_GROUP_INADIMPLENCIA != RECEIVABLES_GROUP_VENCIDO_COM_CONTEXTO

    def test_perda_provavel_fora_do_conjunto_nao_delinquente(self) -> None:
        """R4: `perda_provavel` classifica como INADIMPLÊNCIA, não como acordo."""
        assert TitleContextType.PERDA_PROVAVEL not in CONTEXT_TYPES_NOT_DELINQUENT

    def test_os_outros_cinco_tipos_sao_vencido_com_contexto(self) -> None:
        esperado = {
            TitleContextType.ACORDO_DE_PAGAMENTO,
            TitleContextType.PAGAMENTO_ANTECIPADO,
            TitleContextType.NOTA_A_CANCELAR,
            TitleContextType.COBRANCA_SUSPENSA,
            TitleContextType.OUTRO,
        }
        assert esperado == CONTEXT_TYPES_NOT_DELINQUENT


class TestGrupoZerado:
    def test_grupo_sem_titulo_e_zero_com_os_quatro_baldes_presentes(self) -> None:
        zerado = _zeroed_group_totals()
        assert isinstance(zerado, ReceivablesGroupTotals)
        assert zerado.total == Decimal("0.00")
        assert zerado.qtd == 0
        assert set(zerado.baldes) == set(OVERDUE_BUCKETS)
        assert all(v == Decimal("0.00") for v in zerado.baldes.values())
        assert set(zerado.baldes_qtd) == set(OVERDUE_BUCKETS)
        assert all(v == 0 for v in zerado.baldes_qtd.values())
