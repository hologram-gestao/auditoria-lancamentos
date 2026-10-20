"""A parcela `N/M` e as datas das parcelas futuras (86e3qhhtb).

Funções puras, sem banco: a leitura da parcela (colunas ou descrição), a chave do
título de cada parcela e o plano de datas das respostas do Gabriel. O caso da Cora
é o REAL medido em 19/10/2026: a linha `3/6` da fatura de 13/10 (período 05/09 a
05/10) vem com a data ORIGINAL da compra, 22/07.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.db.models import CardPostingDateMode
from app.integrations.anthropic.schemas import ExtractedTransaction
from app.modules.reconciliations.installments import (
    Installment,
    entry_installment,
    parse_installment,
    valid_pair,
)
from app.modules.reconciliations.omie_posting.installment_plan import (
    add_months,
    current_installment_date,
    plan_future_installments,
)
from app.modules.reconciliations.omie_posting.keys import (
    derive_installment_payable_key,
    derive_payable_key,
)

PURCHASE = CardPostingDateMode.PURCHASE_DATE.value
DUE = CardPostingDateMode.INVOICE_DUE_DATE.value


class TestLeituraPelaDescricao:
    @pytest.mark.parametrize(
        ("description", "expected"),
        [
            ("LOJA EXEMPLO 07/10", (7, 10)),
            ("LOJA EXEMPLO 7/10", (7, 10)),
            ("LOJA EXEMPLO PARC 07/10 SAO PAULO", (7, 10)),
            ("Loja exemplo - Parcela 3 de 6", (3, 6)),
            ("PARC.02/12 CURSO", (2, 12)),
            ("LOJA EXEMPLO 10/10", (10, 10)),
        ],
    )
    def test_le_o_par(self, description: str, expected: tuple[int, int]) -> None:
        pair = parse_installment(description)
        assert pair is not None
        assert (pair.number, pair.total) == expected

    @pytest.mark.parametrize(
        "description",
        [
            None,
            "",
            "UBER TRIP",
            "LOJA 10/09",  # número maior que o total: data, não parcela
            "LOJA 0/5",
            "LOJA 3/1",
            "LOJA 07/10 SAO PAULO",  # sem marcador e fora do fim
            "LOJA 3/60",  # acima do teto do fallback
        ],
    )
    def test_nao_inventa_parcela(self, description: str | None) -> None:
        assert parse_installment(description) is None

    def test_colunas_vencem_a_descricao(self) -> None:
        entry = SimpleNamespace(installment_number=2, installment_total=4)
        pair = entry_installment(entry, "LOJA 07/10")
        assert pair == Installment(2, 4)

    def test_sem_colunas_le_a_descricao(self) -> None:
        entry = SimpleNamespace(installment_number=None, installment_total=None)
        assert entry_installment(entry, "LOJA 07/10") == Installment(7, 10)

    def test_coluna_pela_metade_nao_adivinha(self) -> None:
        entry = SimpleNamespace(installment_number=3, installment_total=None)
        assert entry_installment(entry, "LOJA 07/10") is None

    def test_par_valido(self) -> None:
        assert valid_pair(1, 2) == Installment(1, 2)
        assert valid_pair(2, 1) is None
        assert valid_pair(1, 1) is None
        assert valid_pair(None, 3) is None


class TestExtracao:
    def test_par_coerente_fica(self) -> None:
        tx = ExtractedTransaction.model_validate(
            {
                "date": "2026-07-22",
                "description": "x",
                "amount": "-1",
                "installment_number": 3,
                "installment_total": 6,
            }
        )
        assert (tx.installment_number, tx.installment_total) == (3, 6)

    @pytest.mark.parametrize(
        ("number", "total"),
        [(3, None), (None, 6), (7, 6), (1, 1), (0, 6), (3, 100)],
    )
    def test_par_incoerente_vira_ausente_sem_recusar_o_arquivo(
        self, number: int | None, total: int | None
    ) -> None:
        tx = ExtractedTransaction.model_validate(
            {
                "date": "2026-07-22",
                "description": "x",
                "amount": "-1",
                "installment_number": number,
                "installment_total": total,
            }
        )
        assert (tx.installment_number, tx.installment_total) == (None, None)

    def test_compra_a_vista_omite(self) -> None:
        tx = ExtractedTransaction.model_validate(
            {"date": "2026-07-22", "description": "x", "amount": "-1"}
        )
        assert (tx.installment_number, tx.installment_total) == (None, None)


class TestChaveDaParcela:
    ENTRY = UUID("12345678-1234-5678-1234-567812345678")

    def test_e_a_chave_do_titulo_da_linha_mais_o_numero(self) -> None:
        key = derive_installment_payable_key(self.ENTRY, 8)
        assert key == f"{derive_payable_key(self.ENTRY)}P08"
        assert len(key) == 23

    def test_numeros_diferentes_chaves_diferentes(self) -> None:
        keys = {derive_installment_payable_key(self.ENTRY, n) for n in range(2, 13)}
        assert len(keys) == 11

    def test_linhas_iguais_em_conteudo_chaves_diferentes(self) -> None:
        other = UUID("87654321-1234-5678-1234-567812345678")
        assert derive_installment_payable_key(self.ENTRY, 8) != derive_installment_payable_key(
            other, 8
        )

    @pytest.mark.parametrize("number", [0, 1, 100])
    def test_recusa_numero_que_nao_e_parcela_futura(self, number: int) -> None:
        with pytest.raises(ValueError, match="entre 2 e 99"):
            derive_installment_payable_key(self.ENTRY, number)


class TestMeses:
    @pytest.mark.parametrize(
        ("day", "months", "expected"),
        [
            (date(2026, 9, 10), 1, date(2026, 10, 10)),
            (date(2026, 9, 10), 4, date(2027, 1, 10)),
            (date(2026, 1, 31), 1, date(2026, 2, 28)),
            (date(2028, 1, 31), 1, date(2028, 2, 29)),
            (date(2026, 8, 31), 3, date(2026, 11, 30)),
            (date(2026, 12, 15), 12, date(2027, 12, 15)),
        ],
    )
    def test_mesmo_dia_ou_ultimo_do_mes(self, day: date, months: int, expected: date) -> None:
        assert add_months(day, months) == expected


class TestPlano:
    def test_exemplo_do_gabriel_data_da_compra(self) -> None:
        """'comprou dia 10 de setembro: 10 de outubro, 10 de novembro, 10 de dezembro'."""
        plan = plan_future_installments(
            installment=Installment(7, 10),
            line_date=date(2026, 9, 10),
            invoice_anchor=date(2026, 9, 28),
            amount=Decimal("100.00"),
            process=PURCHASE,
            invoice_due_date=date(2026, 10, 5),
        )
        assert [(p.number, p.due_date) for p in plan] == [
            (8, date(2026, 10, 10)),
            (9, date(2026, 11, 10)),
            (10, date(2026, 12, 10)),
        ]
        # Resposta (c): emissão = a data da fatura em que a parcela cai.
        assert [p.issue_date for p in plan] == [
            date(2026, 11, 5),
            date(2026, 12, 5),
            date(2027, 1, 5),
        ]
        assert {p.amount for p in plan} == {Decimal("100.00")}

    def test_cora_linha_com_a_data_original_da_compra(self) -> None:
        """Cora 13/10/2026: `3/6` em 22/07, período até 05/10 → a 3 é de 22/09."""
        installment = Installment(3, 6)
        anchor = date(2026, 10, 4)
        assert current_installment_date(installment, date(2026, 7, 22), anchor) == date(2026, 9, 22)
        plan = plan_future_installments(
            installment=installment,
            line_date=date(2026, 7, 22),
            invoice_anchor=anchor,
            amount=Decimal("199.84"),
            process=DUE,
            invoice_due_date=date(2026, 10, 13),
        )
        assert [(p.number, p.due_date, p.issue_date) for p in plan] == [
            (4, date(2026, 10, 22), date(2026, 10, 22)),
            (5, date(2026, 11, 22), date(2026, 11, 22)),
            (6, date(2026, 12, 22), date(2026, 12, 22)),
        ]

    def test_linha_com_a_data_da_parcela(self) -> None:
        installment = Installment(3, 6)
        line = date(2026, 9, 22)
        assert current_installment_date(installment, line, date(2026, 10, 4)) == line
        plan = plan_future_installments(
            installment=installment,
            line_date=line,
            invoice_anchor=date(2026, 10, 4),
            amount=Decimal("1"),
            process=PURCHASE,
            invoice_due_date=None,
        )
        assert [p.due_date for p in plan] == [
            date(2026, 10, 22),
            date(2026, 11, 22),
            date(2026, 12, 22),
        ]
        # Sem vencimento da fatura, a emissão cai no vencimento da parcela.
        assert [p.issue_date for p in plan] == [p.due_date for p in plan]

    def test_dia_31_nao_encolhe_nos_meses_seguintes(self) -> None:
        plan = plan_future_installments(
            installment=Installment(1, 4),
            line_date=date(2026, 1, 31),
            invoice_anchor=date(2026, 2, 5),
            amount=Decimal("1"),
            process=DUE,
            invoice_due_date=None,
        )
        assert [p.due_date for p in plan] == [
            date(2026, 2, 28),
            date(2026, 3, 31),
            date(2026, 4, 30),
        ]

    def test_ultima_parcela_nao_tem_futuras(self) -> None:
        assert (
            plan_future_installments(
                installment=Installment(10, 10),
                line_date=date(2026, 9, 10),
                invoice_anchor=date(2026, 9, 28),
                amount=Decimal("1"),
                process=PURCHASE,
                invoice_due_date=None,
            )
            == []
        )
