"""Testes da sonda de pagamento dividido (Fatia 1 da 86e2n4r6p).

A sonda existe para MEDIR, não para decidir. Os dois riscos que estes testes
cobrem: contar errado (a decisão sobre a task estrutural sairia de um número
falso) e, pior, alterar o matching sem querer.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.reconciliations.processing.matcher import (
    FileEntryForMatch,
    OmieMovement,
    match,
)
from app.modules.reconciliations.processing.split_payment_probe import (
    MAX_CANDIDATOS_POR_LINHA,
    probe_split_payments,
)


def _file(id_: str, d: date, amount: str) -> FileEntryForMatch:
    return FileEntryForMatch(id=id_, transaction_date=d, amount=Decimal(amount))


def _omie(
    omie_id: int,
    d: date,
    amount: str,
    related_launch_id: int | None = None,
) -> OmieMovement:
    return OmieMovement(
        omie_id=omie_id,
        transaction_date=d,
        amount=Decimal(amount),
        status="Conciliado",
        is_realized=True,
        related_launch_id=related_launch_id,
    )


@pytest.mark.unit
class TestSplitPaymentProbe:
    def test_caso_da_bruna_duas_parcelas_que_somam(self) -> None:
        """R$ 2.800 no extrato contra R$ 1.500 + R$ 1.300 no Omie."""
        sem_par = [_file("F1", date(2026, 7, 7), "-2800.00")]
        omie = [
            _omie(901, date(2026, 7, 7), "-1500.00"),
            _omie(902, date(2026, 7, 7), "-1300.00"),
        ]

        probe = probe_split_payments(sem_par, omie, tolerance_days=3)

        assert probe.sem_omie == 1
        assert probe.fechariam_por_soma == 1
        assert probe.omie_sem_par == 2

    def test_tres_parcelas_tambem_contam(self) -> None:
        sem_par = [_file("F1", date(2026, 7, 7), "-3000.00")]
        omie = [
            _omie(901, date(2026, 7, 7), "-1000.00"),
            _omie(902, date(2026, 7, 7), "-1000.00"),
            _omie(903, date(2026, 7, 7), "-1000.00"),
        ]

        assert probe_split_payments(sem_par, omie, tolerance_days=3).fechariam_por_soma == 1

    def test_soma_fora_da_tolerancia_nao_conta(self) -> None:
        """A tolerância de R$ 0,01 (§5.1) vale para a SOMA."""
        sem_par = [_file("F1", date(2026, 7, 7), "-2800.00")]
        omie = [
            _omie(901, date(2026, 7, 7), "-1500.00"),
            _omie(902, date(2026, 7, 7), "-1299.90"),  # soma dá 2799.90
        ]

        assert probe_split_payments(sem_par, omie, tolerance_days=3).fechariam_por_soma == 0

    def test_fora_da_janela_de_datas_nao_conta(self) -> None:
        sem_par = [_file("F1", date(2026, 7, 7), "-2800.00")]
        omie = [
            _omie(901, date(2026, 7, 7), "-1500.00"),
            _omie(902, date(2026, 7, 20), "-1300.00"),  # 13 dias
        ]

        assert probe_split_payments(sem_par, omie, tolerance_days=3).fechariam_por_soma == 0

    def test_agrupamento_do_omie_e_contado_quando_compartilhado(self) -> None:
        """Se o `nCodLancRelac` vier igual, a implementação futura fica barata."""
        sem_par = [_file("F1", date(2026, 7, 7), "-2800.00")]
        omie = [
            _omie(901, date(2026, 7, 7), "-1500.00", related_launch_id=555),
            _omie(902, date(2026, 7, 7), "-1300.00", related_launch_id=555),
        ]

        probe = probe_split_payments(sem_par, omie, tolerance_days=3)

        assert probe.fechariam_por_soma == 1
        assert probe.com_agrupamento_omie == 1
        assert probe.omie_com_lanc_relac == 2

    def test_sem_agrupamento_conta_a_soma_mas_nao_o_agrupamento(self) -> None:
        """Distinção que decide o custo da Fatia 2: somar é fácil, AGRUPAR não."""
        sem_par = [_file("F1", date(2026, 7, 7), "-2800.00")]
        omie = [
            _omie(901, date(2026, 7, 7), "-1500.00"),
            _omie(902, date(2026, 7, 7), "-1300.00"),
        ]

        probe = probe_split_payments(sem_par, omie, tolerance_days=3)

        assert probe.fechariam_por_soma == 1
        assert probe.com_agrupamento_omie == 0
        assert probe.omie_com_lanc_relac == 0

    def test_linha_acima_do_teto_entra_em_nao_avaliadas(self) -> None:
        """Teto NUNCA vira "não fecha" — isso faria a métrica mentir para baixo.

        Truncar em silêncio é pior do que não medir: a sessão grande, que é
        justamente onde o pagamento dividido tem mais chance de aparecer, sairia
        contada como se não tivesse nenhum caso.
        """
        sem_par = [_file("F1", date(2026, 7, 7), "-2800.00")]
        omie = [
            _omie(900 + i, date(2026, 7, 7), "-1400.00")
            for i in range(MAX_CANDIDATOS_POR_LINHA + 1)
        ]

        probe = probe_split_payments(sem_par, omie, tolerance_days=3)

        assert probe.nao_avaliadas == 1
        assert probe.fechariam_por_soma == 0

    def test_entradas_vazias_nao_quebram(self) -> None:
        probe = probe_split_payments([], [], tolerance_days=3)
        assert probe.sem_omie == 0
        assert probe.fechariam_por_soma == 0

    def test_a_sonda_nao_altera_o_matching(self) -> None:
        """A garantia central: rodar a sonda não muda nenhum par.

        Ela recebe listas e devolve números. Se algum dia alguém a fizer mutar
        as entradas — ordenar in-place, consumir candidatos — este teste quebra.
        """
        files = [
            _file("F1", date(2026, 7, 7), "-2800.00"),
            _file("F2", date(2026, 7, 8), "-1500.00"),
        ]
        omie = [
            _omie(901, date(2026, 7, 7), "-1500.00"),
            _omie(902, date(2026, 7, 7), "-1300.00"),
        ]
        antes = match(files, omie)

        probe_split_payments(files, omie, tolerance_days=3)

        depois = match(files, omie)
        assert depois.matches == antes.matches
        assert depois.unmatched_omie_indices == antes.unmatched_omie_indices
