"""Testes unitários do algoritmo de cruzamento (S10 / BACK 8.4).

Função pura — sem DB, sem mock. Cobre:
    - Match básico de mesmo dia + valor.
    - Tolerância de data (limite e fora do limite).
    - Tolerância de valor 0.01 BRL (limite e fora).
    - Sinal aritmético: débito não casa crédito.
    - Desempate: passadas por |days_diff| crescente → menor |amount_diff| →
      date asc.
    - Passadas: uma linha distante não rouba o par exato de outra linha.
    - Greedy dentro da passada: 2 file_entries disputando o mesmo Omie — só o
      primeiro em (data, id) vence.
    - Lista vazia (matcher tolera os dois lados vazios).
    - `unmatched_omie_indices` preserva ordem original.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.modules.reconciliations.processing.matcher import (
    AMOUNT_TOLERANCE,
    DATE_DIVERGENCE_RANGE,
    FileEntryForMatch,
    OmieMovement,
    match,
)


def _file(id_: str, d: date, amount: str) -> FileEntryForMatch:
    return FileEntryForMatch(id=id_, transaction_date=d, amount=Decimal(amount))


def _omie(
    omie_id: int,
    d: date,
    amount: str,
    status: str = "Conciliado",
    supplier: str | None = None,
) -> OmieMovement:
    return OmieMovement(
        omie_id=omie_id,
        transaction_date=d,
        amount=Decimal(amount),
        status=status,
        is_realized=True,
        supplier=supplier,
    )


@pytest.mark.unit
class TestMatcherBasic:
    def test_empty_inputs_returns_empty_result(self) -> None:
        result = match([], [], tolerance_days=3)
        assert result.matches == []
        assert result.unmatched_omie_indices == []

    def test_perfect_match_same_day_same_amount(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]

        result = match(files, omie, tolerance_days=3)

        assert result.matches == [("F1", 1)]
        assert result.unmatched_omie_indices == []

    def test_no_candidates_leaves_omie_unmatched(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "999.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]

        result = match(files, omie, tolerance_days=3)

        assert result.matches == []
        assert result.unmatched_omie_indices == [0]


@pytest.mark.unit
class TestMatcherAmountTolerance:
    def test_amount_within_tolerance_matches(self) -> None:
        # |100.00 - 100.01| = 0.01 → exatamente no limite.
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.01")]
        result = match(files, omie, tolerance_days=3)
        assert result.matches == [("F1", 1)]

    def test_amount_just_outside_tolerance_does_not_match(self) -> None:
        # 0.02 > AMOUNT_TOLERANCE (0.01) → não casa.
        assert Decimal("0.01") == AMOUNT_TOLERANCE
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.02")]
        result = match(files, omie, tolerance_days=3)
        assert result.matches == []
        assert result.unmatched_omie_indices == [0]

    def test_opposite_sign_does_not_match(self) -> None:
        # Débito do arquivo (-100) vs crédito do Omie (+100) — diferença de 200.
        files = [_file("F1", date(2026, 4, 15), "-100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]
        result = match(files, omie, tolerance_days=3)
        assert result.matches == []


@pytest.mark.unit
class TestMatcherDateTolerance:
    def test_date_diff_within_tolerance_matches(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 18), "100.00")]  # +3 dias = limite
        result = match(files, omie, tolerance_days=3)
        assert result.matches == [("F1", 1)]

    def test_date_diff_outside_tolerance_does_not_match(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 19), "100.00")]  # +4 dias
        result = match(files, omie, tolerance_days=3)
        assert result.matches == []

    def test_zero_tolerance_only_same_day(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 16), "100.00"),
            _omie(2, date(2026, 4, 15), "100.00"),
        ]
        result = match(files, omie, tolerance_days=0)
        assert result.matches == [("F1", 2)]
        assert result.unmatched_omie_indices == [0]


@pytest.mark.unit
class TestMatcherTieBreaking:
    def test_smaller_days_diff_wins(self) -> None:
        """Mesmo amount, datas diferentes → mais próximo vence."""
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 18), "100.00"),  # +3 dias
            _omie(2, date(2026, 4, 16), "100.00"),  # +1 dia ← vence
            _omie(3, date(2026, 4, 17), "100.00"),  # +2 dias
        ]
        result = match(files, omie, tolerance_days=3)
        assert result.matches == [("F1", 2)]

    def test_smaller_amount_diff_wins_when_days_tied(self) -> None:
        """Mesmo days_diff, valores diferentes → mais próximo vence."""
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 15), "100.01"),  # diff 0.01
            _omie(2, date(2026, 4, 15), "100.00"),  # diff 0.00 ← vence
        ]
        result = match(files, omie, tolerance_days=3)
        assert result.matches == [("F1", 2)]

    def test_earliest_date_wins_when_days_and_amount_tied(self) -> None:
        """Mesmo days_diff e amount_diff exato → menor date vence."""
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(2, date(2026, 4, 16), "100.00"),  # +1 dia
            _omie(1, date(2026, 4, 14), "100.00"),  # -1 dia ← mesmo days_diff, date menor
        ]
        result = match(files, omie, tolerance_days=3)
        # |days_diff| = 1 em ambos; amount_diff = 0 em ambos; date menor → 14/04
        assert result.matches == [("F1", 1)]


@pytest.mark.unit
class TestMatcherGreedyConsumption:
    def test_two_file_entries_compete_for_same_omie_first_wins(self) -> None:
        """F1 e F2 com mesmo valor/data — só F1 (primeiro em (data, id)) consome."""
        files = [
            _file("F1", date(2026, 4, 15), "100.00"),
            _file("F2", date(2026, 4, 15), "100.00"),
        ]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]

        result = match(files, omie, tolerance_days=3)

        assert result.matches == [("F1", 1)]
        assert result.unmatched_omie_indices == []  # 1 consumido
        # F2 fica sem match, mas isso é codificado pela ausência em `matches` —
        # o caller infere `sem_omie` para todo file_entry não presente.

    def test_two_file_entries_pegam_omies_diferentes_quando_disponiveis(self) -> None:
        """Confirma que o set de consumidos não bloqueia matches legítimos."""
        files = [
            _file("F1", date(2026, 4, 15), "100.00"),
            _file("F2", date(2026, 4, 20), "200.00"),
        ]
        omie = [
            _omie(1, date(2026, 4, 15), "100.00"),
            _omie(2, date(2026, 4, 20), "200.00"),
        ]
        result = match(files, omie, tolerance_days=3)
        assert sorted(result.matches) == [("F1", 1), ("F2", 2)]
        assert result.unmatched_omie_indices == []


@pytest.mark.unit
class TestMatcherDaysDiff:
    """FASE 1 (BACK 1.6): o matcher expõe `days_diff_by_file_id` para o caller
    classificar conciliado (exato) vs conciliado_data_divergente (1-3 dias)."""

    def test_range_default_is_three(self) -> None:
        assert DATE_DIVERGENCE_RANGE == 3

    def test_exact_match_records_zero(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]
        result = match(files, omie)  # sem tolerance_days → usa o default fixo
        assert result.matches == [("F1", 1)]
        assert result.days_diff_by_file_id == {"F1": 0}

    def test_divergent_match_records_days_diff(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 17), "100.00")]  # +2 dias
        result = match(files, omie)
        assert result.matches == [("F1", 1)]
        assert result.days_diff_by_file_id == {"F1": 2}

    def test_default_range_matches_up_to_three_days(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 18), "100.00")]  # +3 dias = limite
        result = match(files, omie)
        assert result.matches == [("F1", 1)]
        assert result.days_diff_by_file_id == {"F1": 3}

    def test_unmatched_file_absent_from_days_diff(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 25), "100.00")]  # +10 dias → não casa
        result = match(files, omie)
        assert result.matches == []
        assert result.days_diff_by_file_id == {}


@pytest.mark.unit
class TestMatcherPassadasPorProximidadeDeData:
    """O defeito reportado pela Bruna em 04/08/2026 (cliente Romilson Carpintaria).

    O matcher casava percorrendo as linhas do arquivo e deixando cada uma pegar
    seu melhor candidato livre. Uma linha cuja contraparte real NÃO casa por
    valor levava o lançamento de outra linha, desde que estivesse dentro dos 3
    dias; a linha roubada virava `sem_omie` e a IA de qualificação acusava
    incoerência na primeira, por comparar fornecedores diferentes. UM
    pareamento errado, DUAS anomalias falsas.
    """

    def test_cenario_bruna_linha_distante_nao_rouba_par_exato(self) -> None:
        """Os números do report: dois pagamentos de R$ 2.800 a 3 dias de distância.

        No Omie o pagamento de 07/07 está DIVIDIDO em duas saídas de R$ 1.400 —
        o matcher é 1-para-1, então essa linha não tem par legítimo. O único
        candidato de R$ 2.800 é o de 10/07, do outro fornecedor.
        """
        files = [
            _file("07-07-maiane", date(2026, 7, 7), "-2800.00"),
            _file("10-07-cleidson", date(2026, 7, 10), "-2800.00"),
        ]
        omie = [
            _omie(901, date(2026, 7, 7), "-1400.00"),
            _omie(902, date(2026, 7, 7), "-1400.00"),
            _omie(903, date(2026, 7, 10), "-2800.00"),
        ]

        result = match(files, omie)

        # A linha de 10/07 fica com o SEU lançamento, por data exata.
        assert result.matches == [("10-07-cleidson", 903)]
        assert result.days_diff_by_file_id == {"10-07-cleidson": 0}
        # A de 07/07 fica sem par — anomalia VERDADEIRA (pagamento dividido),
        # em vez das duas falsas que o algoritmo antigo produzia.
        assert "07-07-maiane" not in dict(result.matches)
        # As duas metades de R$ 1.400 sobram para `missing_in_file`.
        assert result.unmatched_omie_indices == [0, 1]

    def test_par_exato_vence_mesmo_chegando_depois_no_arquivo(self) -> None:
        """Versão mínima: a passada 0 fecha antes de qualquer passada distante."""
        files = [
            _file("A", date(2026, 4, 15), "100.00"),  # 3 dias do único candidato
            _file("B", date(2026, 4, 18), "100.00"),  # data exata
        ]
        omie = [_omie(1, date(2026, 4, 18), "100.00")]

        result = match(files, omie)

        assert result.matches == [("B", 1)]
        assert result.days_diff_by_file_id == {"B": 0}

    def test_ordem_da_lista_do_arquivo_nao_altera_resultado(self) -> None:
        """O resultado deixou de depender de o parser entregar o extrato ordenado."""
        files = [
            _file("A", date(2026, 4, 15), "100.00"),
            _file("B", date(2026, 4, 18), "100.00"),
        ]
        omie = [_omie(1, date(2026, 4, 18), "100.00")]

        direto = match(files, omie)
        invertido = match(list(reversed(files)), omie)

        assert direto.matches == invertido.matches
        assert direto.days_diff_by_file_id == invertido.days_diff_by_file_id
        assert direto.unmatched_omie_indices == invertido.unmatched_omie_indices

    def test_passada_distante_ainda_casa_o_que_sobrou(self) -> None:
        """A tolerância de 3 dias continua valendo — só perde para a data exata."""
        files = [_file("A", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 18), "100.00")]

        result = match(files, omie)

        assert result.matches == [("A", 1)]
        assert result.days_diff_by_file_id == {"A": 3}

    def test_empate_na_mesma_passada_resolve_por_data_da_linha(self) -> None:
        """Duas linhas a 1 dia do mesmo candidato → a de data menor leva."""
        files = [
            _file("depois", date(2026, 4, 17), "100.00"),
            _file("antes", date(2026, 4, 15), "100.00"),
        ]
        omie = [_omie(1, date(2026, 4, 16), "100.00")]

        result = match(files, omie)

        assert result.matches == [("antes", 1)]

    def test_empate_total_resolve_por_id(self) -> None:
        """Mesma data e mesmo valor: sobra o id como critério estável."""
        files = [
            _file("F2", date(2026, 4, 15), "100.00"),
            _file("F1", date(2026, 4, 15), "100.00"),
        ]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]

        result = match(files, omie)

        assert result.matches == [("F1", 1)]

    def test_valores_iguais_na_mesma_data_casam_um_para_um(self) -> None:
        """Duas linhas e dois lançamentos idênticos — ninguém fica sem par."""
        files = [
            _file("F1", date(2026, 4, 15), "-2800.00"),
            _file("F2", date(2026, 4, 15), "-2800.00"),
        ]
        omie = [
            _omie(1, date(2026, 4, 15), "-2800.00"),
            _omie(2, date(2026, 4, 15), "-2800.00"),
        ]

        result = match(files, omie)

        assert sorted(result.matches) == [("F1", 1), ("F2", 2)]
        assert result.unmatched_omie_indices == []

    def test_priorizar_data_exata_pode_fechar_um_par_a_menos(self) -> None:
        """Contrapartida CONHECIDA e ACEITA da mudança — não é regressão a consertar.

        Duas linhas de R$ 500 (01/07 e 04/07) e dois lançamentos (04/07 e 07/07).
        O algoritmo antigo encadeava tudo com 3 dias de atraso e fechava DOIS
        pares. O novo fecha o par exato de 04/07 e deixa a linha de 01/07 sem
        candidato dentro do range.

        É a mesma troca que corrige o caso da Bruna, aplicada de forma
        consistente: o sistema para de inventar um pareamento distante quando
        existe um pareamento exato disputando o mesmo lançamento. Um par a menos,
        porém nenhum par errado. Medido em 20 mil cenários sintéticos com forte
        colisão de valores: 0,4% de pares a menos no total, e o novo NUNCA fecha
        menos pares de data exata que o antigo.
        """
        files = [
            _file("A", date(2026, 7, 1), "-500.00"),
            _file("B", date(2026, 7, 4), "-500.00"),
        ]
        omie = [
            _omie(801, date(2026, 7, 4), "-500.00"),
            _omie(802, date(2026, 7, 7), "-500.00"),
        ]

        result = match(files, omie)

        assert result.matches == [("B", 801)]
        assert result.days_diff_by_file_id == {"B": 0}
        # 802 sobra: vira `missing_in_file`, e a linha A vira `sem_omie`.
        assert result.unmatched_omie_indices == [1]

    def test_matches_saem_em_ordem_de_data_do_arquivo_nao_de_passada(self) -> None:
        """O consumidor não enxerga o detalhe das passadas na ordem da saída."""
        files = [
            _file("A", date(2026, 4, 10), "100.00"),  # casa na passada 2
            _file("B", date(2026, 4, 20), "200.00"),  # casa na passada 0
        ]
        omie = [
            _omie(1, date(2026, 4, 12), "100.00"),
            _omie(2, date(2026, 4, 20), "200.00"),
        ]

        result = match(files, omie)

        assert result.matches == [("A", 1), ("B", 2)]
        assert result.days_diff_by_file_id == {"A": 2, "B": 0}


@pytest.mark.unit
class TestMatcherDesempatePorFornecedor:
    """Defesa em profundidade da sugestão 1 da Bruna.

    As passadas de data resolvem a disputa entre datas diferentes. Dois
    lançamentos de mesmo valor E mesma data continuavam indistinguíveis — o
    matcher só conhecia valor e data. Agora ele também recebe o fornecedor do
    Omie e a descrição do extrato, e usa a afinidade entre os dois como
    DESEMPATE (nunca como exclusão).
    """

    def test_escolhe_o_candidato_do_fornecedor_compativel(self) -> None:
        files = [
            FileEntryForMatch(
                id="F1",
                transaction_date=date(2026, 7, 7),
                amount=Decimal("-2800.00"),
                description="Pix enviado: Cp:18236120-Maiane Medrado Silva",
            )
        ]
        omie = [
            _omie(901, date(2026, 7, 7), "-2800.00", supplier="Cleidson Quiteria de Souza"),
            _omie(902, date(2026, 7, 7), "-2800.00", supplier="Maiane Medrado Silva"),
        ]

        result = match(files, omie)

        # Sem fornecedor, o desempate seria a data — empatada — e o primeiro da
        # lista (901) levaria. O nome inverte isso.
        assert result.matches == [("F1", 902)]
        assert result.tie_stats.ties == 1
        assert result.tie_stats.broken_by_supplier == 1

    def test_nome_que_nao_bate_nunca_impede_o_match(self) -> None:
        """O limite mais importante da feature (§ da task): nunca excluir.

        Descrição de extrato é texto sujo e abreviado. Transformar ruído de
        texto em `sem_omie` trocaria um falso positivo por outro pior.
        """
        files = [
            FileEntryForMatch(
                id="F1",
                transaction_date=date(2026, 7, 7),
                amount=Decimal("-2800.00"),
                description="COMPRA CARTAO 4432 XYZ",
            )
        ]
        omie = [_omie(901, date(2026, 7, 7), "-2800.00", supplier="Maiane Medrado Silva")]

        result = match(files, omie)

        assert result.matches == [("F1", 901)]
        # Candidato único: não houve empate a registrar.
        assert result.tie_stats.ties == 0

    def test_valor_manda_mais_que_nome(self) -> None:
        """Valor é fato, nome é indício — a ordem dos critérios reflete isso."""
        files = [
            FileEntryForMatch(
                id="F1",
                transaction_date=date(2026, 7, 7),
                amount=Decimal("-100.00"),
                description="Pix Maiane Medrado Silva",
            )
        ]
        omie = [
            # Valor exato, fornecedor sem relação.
            _omie(901, date(2026, 7, 7), "-100.00", supplier="Outra Pessoa Qualquer"),
            # Fornecedor compatível, mas 1 centavo de diferença.
            _omie(902, date(2026, 7, 7), "-100.01", supplier="Maiane Medrado Silva"),
        ]

        result = match(files, omie)

        assert result.matches == [("F1", 901)]

    def test_sem_fornecedor_cai_no_criterio_anterior(self) -> None:
        """Títulos a pagar/receber não trazem nome — o comportamento é o antigo."""
        files = [
            FileEntryForMatch(
                id="F1",
                transaction_date=date(2026, 7, 7),
                amount=Decimal("-100.00"),
                description="Pix Maiane Medrado Silva",
            )
        ]
        omie = [
            _omie(901, date(2026, 7, 8), "-100.00"),  # sem supplier
            _omie(902, date(2026, 7, 6), "-100.00"),  # sem supplier, data menor
        ]

        result = match(files, omie)

        # Ambos a 1 dia; sem sinal de nome, vence a data menor (06/07).
        assert result.matches == [("F1", 902)]
        assert result.tie_stats.broken_by_supplier == 0

    def test_contadores_ficam_zerados_quando_nao_ha_disputa(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [_omie(1, date(2026, 4, 15), "100.00")]

        result = match(files, omie)

        assert result.tie_stats.ties == 0
        assert result.tie_stats.broken_by_supplier == 0

    def test_empate_contado_mesmo_quando_o_nome_nao_muda_a_escolha(self) -> None:
        """`ties` é o denominador da métrica — conta mesmo sem sinal de nome."""
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 15), "100.00"),
            _omie(2, date(2026, 4, 15), "100.00"),
        ]

        result = match(files, omie)

        assert result.tie_stats.ties == 1
        assert result.tie_stats.broken_by_supplier == 0


@pytest.mark.unit
class TestMatcherUnmatchedOrder:
    def test_unmatched_omie_indices_preserve_input_order(self) -> None:
        files = [_file("F1", date(2026, 4, 15), "100.00")]
        omie = [
            _omie(1, date(2026, 4, 1), "999.00"),  # idx 0 — sem match
            _omie(2, date(2026, 4, 15), "100.00"),  # idx 1 — vai casar
            _omie(3, date(2026, 4, 28), "777.00"),  # idx 2 — sem match
        ]
        result = match(files, omie, tolerance_days=3)
        assert result.matches == [("F1", 2)]
        # Ordem dos não-consumidos preservada: 0, 2 (não [2, 0])
        assert result.unmatched_omie_indices == [0, 2]
