"""Cruzamento da fatura de cartão no modo "vencimento da fatura" (86e3n70p0).

Caso real anonimizado: a fatura Cora da PCTEX (cliente da Prospecta) que vence em
10/10/2026 conciliou 0 de 5 compras em dev (sessão 7ac383a9, 08/10/2026). Na
Prospecta, as compras do cartão entram no Omie em LOTE na data de vencimento da
fatura, com o pagamento da fatura como "Entrada de Transferência" positiva no
mesmo dia; as parcelas futuras ficam como título Previsto. Nada disso guarda a
data da compra — a parcela 3/6 do Anydesk, de 22/07, está no lote de 10/10.

Os valores e datas são os da tela de revisão; os ids são fictícios.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.reconciliations.processing.matcher import (
    AMOUNT_TOLERANCE,
    FileEntryForMatch,
    MatchResult,
    OmieMovement,
    match,
    match_invoice_lot,
)
from app.modules.reconciliations.processing.omie_window import omie_window_for_session

pytestmark = pytest.mark.unit

VENCIMENTO = date(2026, 10, 10)

# A fatura: 5 compras, sinal e valor como o parser extraiu.
FATURA = [
    FileEntryForMatch("f-anydesk", date(2026, 7, 22), Decimal("-199.84"), "ANYDESK 3/6"),
    FileEntryForMatch("f-anthropic", date(2026, 9, 8), Decimal("-591.91"), "ANTHROPIC"),
    FileEntryForMatch("f-fireflies", date(2026, 9, 13), Decimal("-88.79"), "FIREFLIES.AI"),
    FileEntryForMatch("f-microsoft", date(2026, 9, 29), Decimal("-87.68"), "MICROSOFT"),
    FileEntryForMatch("f-google", date(2026, 10, 1), Decimal("-196.00"), "GOOGLE WORKSPACE"),
]


def _realizado(omie_id: int, dia: date, valor: str, fornecedor: str | None) -> OmieMovement:
    return OmieMovement(omie_id, dia, Decimal(valor), "Conciliado", True, supplier=fornecedor)


def _previsto(omie_id: int, dia: date, valor: str) -> OmieMovement:
    # Título a pagar: o Omie não devolve nome (afinidade 0), só o valor com sinal.
    return OmieMovement(omie_id, dia, Decimal(valor), "Previsto", False)


LOTE_SETEMBRO = date(2026, 9, 10)
# O lote da fatura ANTERIOR (10/09): as mesmas recorrências, de outro mês.
LOTE_ANTERIOR = [
    _realizado(901, LOTE_SETEMBRO, "-87.68", "Microsoft"),
    _realizado(902, LOTE_SETEMBRO, "-199.84", "AnyDesk Software"),
    _realizado(903, LOTE_SETEMBRO, "-574.56", "Anthropic"),
    _realizado(904, LOTE_SETEMBRO, "1069.06", None),  # Entrada de Transferência
]
# O lote DESTA fatura (10/10).
LOTE_DA_FATURA = [
    _previsto(1001, VENCIMENTO, "-199.84"),  # Anydesk 4/6, título Previsto
    _realizado(1002, VENCIMENTO, "-196.00", "Google"),
    _realizado(1003, VENCIMENTO, "-87.68", "Microsoft"),
    _realizado(1004, VENCIMENTO, "-88.79", "Fireflies"),
    _realizado(1005, VENCIMENTO, "-591.91", "Anthropic"),
]
# Parcela seguinte (10/11): fora do lote por vencimento.
PARCELA_FUTURA = [_previsto(1101, date(2026, 11, 10), "-199.84")]

TUDO_NO_OMIE = [*LOTE_ANTERIOR, *LOTE_DA_FATURA, *PARCELA_FUTURA]


def _cruzar(mode: str) -> tuple[MatchResult, list[OmieMovement]]:
    """O que o job faz: recorta pela janela da sessão e chama o matcher do modo."""
    inicio, fim = omie_window_for_session(
        account_type="credit_card",
        card_posting_date_mode=mode,
        invoice_due_date=VENCIMENTO,
        period_start=min(f.transaction_date for f in FATURA),
        period_end=max(f.transaction_date for f in FATURA),
    )
    na_janela = [m for m in TUDO_NO_OMIE if inicio <= m.transaction_date <= fim]
    if mode == "invoice_due_date":
        return match_invoice_lot(FATURA, na_janela), na_janela
    return match(FATURA, na_janela), na_janela


def _pares(result: MatchResult) -> dict[str, int]:
    return dict(result.matches)


class TestCasoPctex:
    def test_modo_vencimento_fecha_as_cinco_com_o_lote_certo(self) -> None:
        result, na_janela = _cruzar("invoice_due_date")

        assert _pares(result) == {
            "f-anydesk": 1001,
            "f-anthropic": 1005,
            "f-fireflies": 1004,
            "f-microsoft": 1003,
            "f-google": 1002,
        }
        # Nenhuma recorrência do lote de 10/09 entra na janela, nem a parcela de 10/11.
        assert {m.omie_id for m in na_janela} == {1001, 1002, 1003, 1004, 1005}
        assert result.unmatched_omie_indices == []
        # Todo par é conciliado: a distância compra → vencimento é o processo.
        assert set(result.days_diff_by_file_id.values()) == {0}

    def test_modo_compra_nao_fecha_nenhum_par(self) -> None:
        # É o que acontece hoje: a janela pela data da compra puxa os lotes de
        # 10/08 e 10/09, e nenhuma compra fica a 3 dias de um lote.
        result, na_janela = _cruzar("purchase_date")

        assert result.matches == []
        assert {m.omie_id for m in na_janela} == {901, 902, 903, 904}

    def test_sem_a_janela_o_valor_sozinho_poderia_errar_de_mes(self) -> None:
        # Por que a janela do lote é parte do modo: o matcher do lote não olha
        # data, então recebendo os DOIS lotes ele tem dois Microsoft 87,68.
        result = match_invoice_lot(FATURA, [*LOTE_ANTERIOR, *LOTE_DA_FATURA])
        assert len(result.matches) == 5
        # Afinidade igual ("Microsoft" nos dois), mesma linha: decide a posição
        # na lista — o 901 do lote anterior. É por isso que o recorte vem antes.
        assert _pares(result)["f-microsoft"] == 901


class TestRegrasDoLote:
    def test_par_e_por_valor_com_tolerancia_de_um_centavo(self) -> None:
        linha = FileEntryForMatch("f", date(2026, 7, 1), Decimal("-100.00"))
        assert match_invoice_lot([linha], [_realizado(1, VENCIMENTO, "-100.01", None)]).matches
        assert not match_invoice_lot([linha], [_realizado(1, VENCIMENTO, "-100.02", None)]).matches
        assert Decimal("0.01") == AMOUNT_TOLERANCE

    def test_sinal_oposto_nao_casa(self) -> None:
        linha = FileEntryForMatch("f", date(2026, 9, 1), Decimal("-1069.06"))
        assert (
            match_invoice_lot([linha], [_realizado(1, VENCIMENTO, "1069.06", None)]).matches == []
        )

    def test_mesmo_valor_com_um_lancamento_casa_um_e_o_outro_fica_sem_omie(self) -> None:
        # Decisão do Pedro (08/10/2026): 1-para-1, como no modo compra (§5.4).
        primeira = FileEntryForMatch("a", date(2026, 9, 2), Decimal("-50.00"), "PADARIA")
        segunda = FileEntryForMatch("b", date(2026, 9, 20), Decimal("-50.00"), "UBER TRIP")
        result = match_invoice_lot(
            [primeira, segunda], [_realizado(7, VENCIMENTO, "-50.00", "Uber do Brasil")]
        )
        # A afinidade de fornecedor decide quem leva, mesmo sendo a linha posterior.
        assert result.matches == [("b", 7)]
        assert result.tie_stats.steals_prevented_by_supplier == 1

    def test_mesmo_valor_sem_sinal_de_nome_decide_a_ordem_da_linha(self) -> None:
        primeira = FileEntryForMatch("a", date(2026, 9, 2), Decimal("-50.00"))
        segunda = FileEntryForMatch("b", date(2026, 9, 20), Decimal("-50.00"))
        result = match_invoice_lot([segunda, primeira], [_realizado(7, VENCIMENTO, "-50.00", None)])
        assert result.matches == [("a", 7)]

    def test_nome_que_nao_bate_nunca_impede_o_par(self) -> None:
        linha = FileEntryForMatch("f", date(2026, 9, 2), Decimal("-10.00"), "XPTO LTDA")
        result = match_invoice_lot([linha], [_realizado(1, VENCIMENTO, "-10.00", "Outro Nome")])
        assert result.matches == [("f", 1)]

    def test_ordem_de_leitura_nao_muda_o_resultado(self) -> None:
        _, na_janela = _cruzar("invoice_due_date")
        direto = match_invoice_lot(FATURA, na_janela)
        invertido = match_invoice_lot(list(reversed(FATURA)), list(reversed(na_janela)))
        assert _pares(direto) == _pares(invertido)
        assert direto.matches == invertido.matches

    def test_empate_de_valor_desempatado_pelo_fornecedor_e_contado(self) -> None:
        linha = FileEntryForMatch("f", date(2026, 9, 5), Decimal("-87.68"), "MICROSOFT 365")
        result = match_invoice_lot(
            [linha],
            [
                _realizado(1, VENCIMENTO, "-87.68", "Fornecedor Qualquer"),
                _realizado(2, VENCIMENTO, "-87.68", "Microsoft"),
            ],
        )
        assert result.matches == [("f", 2)]
        assert result.tie_stats.ties == 1
        assert result.tie_stats.broken_by_supplier == 1

    def test_maximalidade_nenhum_par_possivel_fica_na_mesa(self) -> None:
        _, na_janela = _cruzar("invoice_due_date")
        extras = [
            FileEntryForMatch("x1", date(2026, 9, 3), Decimal("-12.00")),
            FileEntryForMatch("x2", date(2026, 9, 4), Decimal("-12.00")),
        ]
        omie = [*na_janela, _realizado(2001, VENCIMENTO, "-12.00", None)]
        files = [*FATURA, *extras]
        result = match_invoice_lot(files, omie)

        matched_files = {f for f, _ in result.matches}
        used = set(range(len(omie))) - set(result.unmatched_omie_indices)
        for fe in (f for f in files if f.id not in matched_files):
            for i, om in enumerate(omie):
                if i in used:
                    continue
                assert abs(fe.amount - om.amount) > AMOUNT_TOLERANCE
        assert len(result.matches) == 6
