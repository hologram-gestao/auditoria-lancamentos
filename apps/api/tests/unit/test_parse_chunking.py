"""Divisão em blocos e junção (86e39xvxm) — funções puras do `parse_chunking`.

O arquivo sintético imita o export do Inter: preâmbulo de metadados, cabeçalho
`;`-separado, valores com vírgula decimal, uma descrição com quebra de linha e
`;` dentro de aspas, e um rodapé de saldo.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import AnthropicParseError
from app.integrations.anthropic.schemas import ExtractedStatement, ExtractedTransaction
from app.modules.reconciliations.parse_chunking import merge_statements, plan_blocks

PREAMBLE = "Extrato Conta Corrente\nConta: 1234-5\nPeríodo: 01/08/2026 a 31/08/2026\n\n"
HEADER = "Data Lançamento;Histórico;Descrição;Valor;Saldo\n"
FOOTER = "Saldo final;12.345,67\n"
QUOTED_ROW = '06/08/2026;Pix enviado;"PIX ENVIADO; NOME COM\nQUEBRA DE LINHA";-10,00;1,00\n'


def _rows(n: int) -> list[str]:
    return [
        f"05/08/2026;Pix enviado;PIX ENVIADO FORNECEDOR {i};-247,80;12.345,67\n" for i in range(n)
    ]


def _inter_csv(n: int, *, quoted_at: int | None = None, footer: bool = True) -> str:
    rows = _rows(n)
    if quoted_at is not None:
        rows.insert(quoted_at, QUOTED_ROW)
    return PREAMBLE + HEADER + "".join(rows) + (FOOTER if footer else "")


def _data_rows(block: str) -> list[str]:
    """Registros de dados de um bloco: linhas que começam com data."""
    return [line for line in block.splitlines() if line[:2].isdigit() and "/" in line[:10]]


@pytest.mark.unit
class TestPlanBlocks:
    def test_arquivo_pequeno_vai_inteiro(self) -> None:
        text = _inter_csv(120)

        plan = plan_blocks(text, chunk_rows=100, min_rows=150)

        assert not plan.is_split
        assert plan.blocks == [text]
        assert plan.data_records == 120
        assert plan.delimiter == ";"

    def test_arquivo_grande_divide_com_preambulo_e_cabecalho_em_todo_bloco(self) -> None:
        text = _inter_csv(350, quoted_at=200)

        plan = plan_blocks(text, chunk_rows=100, min_rows=150)

        assert plan.is_split
        assert len(plan.blocks) == 4  # ceil(351 / 100), equilibrados
        for block in plan.blocks:
            assert block.startswith(PREAMBLE + HEADER)
        # Nenhum registro perdido nem duplicado — o de aspas conta como 1.
        assert sum(len(_data_rows(block)) for block in plan.blocks) == 351
        assert plan.data_records == 351
        sizes = [len(_data_rows(block)) for block in plan.blocks]
        assert max(sizes) - min(sizes) <= 3  # 88, 88, 88, 87

    def test_registro_com_quebra_de_linha_dentro_de_aspas_nao_e_partido(self) -> None:
        # O registro de aspas cai exatamente na fronteira do 1º bloco.
        text = _inter_csv(199, quoted_at=99)

        plan = plan_blocks(text, chunk_rows=100, min_rows=150)

        assert plan.is_split
        holders = [block for block in plan.blocks if "NOME COM\nQUEBRA DE LINHA" in block]
        assert len(holders) == 1
        assert not any("NOME COM\n" in b and "QUEBRA DE LINHA" not in b for b in plan.blocks)

    def test_rodape_so_no_ultimo_bloco(self) -> None:
        plan = plan_blocks(_inter_csv(300), chunk_rows=100, min_rows=150)

        assert plan.is_split
        assert [FOOTER in block for block in plan.blocks] == [False, False, True]

    def test_virgula_decimal_nao_confunde_o_delimitador(self) -> None:
        """Com `,` o número de campos varia por linha; o `;` cobre todas e vence."""
        plan = plan_blocks(_inter_csv(200), chunk_rows=100, min_rows=150)

        assert plan.delimiter == ";"

    def test_sem_cabecalho_a_primeira_linha_de_dados_nao_e_repetida(self) -> None:
        text = PREAMBLE + "".join(_rows(300))

        plan = plan_blocks(text, chunk_rows=100, min_rows=150)

        assert plan.is_split
        assert sum(len(_data_rows(block)) for block in plan.blocks) == 300
        assert all(block.startswith(PREAMBLE) for block in plan.blocks)

    def test_texto_sem_estrutura_tabular_nao_divide(self) -> None:
        text = "\n".join(f"linha solta numero {i} sem separador" for i in range(400)) + "\n"

        plan = plan_blocks(text, chunk_rows=100, min_rows=150)

        assert not plan.is_split
        assert plan.delimiter is None

    def test_tsv_renderizado_do_xlsx_divide_por_tab(self) -> None:
        rows = "".join(
            f"2026-08-{i % 28 + 1:02d}\tPIX LINHA {i}\t-100\t{1000 * i}\n" for i in range(320)
        )
        text = "# Aba: Extrato\nData\tDescrição\tValor\tSaldo\n" + rows

        plan = plan_blocks(text, chunk_rows=100, min_rows=150)

        assert plan.is_split
        assert plan.delimiter == "\t"
        assert all(block.startswith("# Aba: Extrato\nData\t") for block in plan.blocks)
        total = sum(1 for b in plan.blocks for line in b.splitlines() if line.startswith("2026-"))
        assert total == 320

    def test_limiar_nunca_fica_abaixo_do_bloco(self) -> None:
        """`min_rows` menor que `chunk_rows` ainda divide em blocos do tamanho pedido."""
        plan = plan_blocks(_inter_csv(160), chunk_rows=100, min_rows=50)

        assert plan.is_split
        assert len(plan.blocks) == 2


def _statement(
    *,
    txs: list[tuple[str, str]],
    opening: str,
    closing: str,
    start: date,
    end: date,
    bank: str = "Banco Inter",
    account_type: str = "checking",
) -> ExtractedStatement:
    return ExtractedStatement(
        bank_name=bank,
        account_type=account_type,  # type: ignore[arg-type]
        period_start=start,
        period_end=end,
        opening_balance=Decimal(opening),
        closing_balance=Decimal(closing),
        transactions=[
            ExtractedTransaction(date=date(2026, 8, 5), description=desc, amount=Decimal(amount))
            for desc, amount in txs
        ],
    )


@pytest.mark.unit
class TestMergeStatements:
    def test_concatena_na_ordem_com_saldos_do_primeiro_e_do_ultimo(self) -> None:
        first = _statement(
            txs=[("A", "-1"), ("B", "-2")],
            opening="100",
            closing="97",
            start=date(2026, 8, 1),
            end=date(2026, 8, 15),
        )
        second = _statement(
            txs=[("C", "-3")],
            opening="97",
            closing="94",
            start=date(2026, 8, 16),
            end=date(2026, 8, 31),
        )

        merged = merge_statements([first, second])

        assert [tx.description for tx in merged.transactions] == ["A", "B", "C"]
        assert merged.opening_balance == Decimal("100")
        assert merged.closing_balance == Decimal("94")
        assert (merged.period_start, merged.period_end) == (date(2026, 8, 1), date(2026, 8, 31))

    def test_periodo_e_a_uniao_mesmo_fora_de_ordem(self) -> None:
        a = _statement(
            txs=[("A", "1")],
            opening="0",
            closing="1",
            start=date(2026, 8, 10),
            end=date(2026, 8, 20),
        )
        b = _statement(
            txs=[("B", "1")],
            opening="1",
            closing="2",
            start=date(2026, 8, 1),
            end=date(2026, 8, 12),
        )

        merged = merge_statements([a, b])

        assert (merged.period_start, merged.period_end) == (date(2026, 8, 1), date(2026, 8, 20))

    def test_banco_desconhecido_num_bloco_nao_apaga_o_identificado(self) -> None:
        a = _statement(
            txs=[("A", "1")],
            opening="0",
            closing="1",
            start=date(2026, 8, 1),
            end=date(2026, 8, 2),
            bank="Desconhecido",
        )
        b = _statement(
            txs=[("B", "1")],
            opening="1",
            closing="2",
            start=date(2026, 8, 3),
            end=date(2026, 8, 4),
            bank="Banco Inter",
        )

        assert merge_statements([a, b]).bank_name == "Banco Inter"

    def test_tipo_de_conta_divergente_falha_alto(self) -> None:
        a = _statement(
            txs=[("A", "1")], opening="0", closing="1", start=date(2026, 8, 1), end=date(2026, 8, 2)
        )
        b = _statement(
            txs=[("B", "-1")],
            opening="0",
            closing="1",
            start=date(2026, 8, 3),
            end=date(2026, 8, 4),
            account_type="credit_card",
        )

        with pytest.raises(AnthropicParseError) as exc_info:
            merge_statements([a, b])

        assert "período menor" in exc_info.value.user_message

    def test_lista_vazia_e_erro_de_programacao(self) -> None:
        with pytest.raises(ValueError, match="vazia"):
            merge_statements([])
