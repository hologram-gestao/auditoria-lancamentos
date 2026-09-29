"""Gerador do arquivo contábil — núcleo puro (Sprint 13, BACK 13.3 — R2), provado contra a amostra REAL.

**Teste-ouro:** as 32 linhas de `extrato_cliente.csv` passam pela MESMA aplicação do
de-para da materialização (`apply_mapping`, com as decisões de `decisoes_depara.csv` e o
banco `649` como conta PADRÃO), viram as linhas que `materialized_lines` devolveria
(snapshot + histórico da vigência), passam pelo `derive_partida` real e pelo modelo
Domínio da 13.2 — e o resultado tem de ser, byte a byte, `lancamentos_esperados.csv`:
cada linha idêntica a uma linha do esperado (multiconjunto) e o arquivo INTEIRO igual ao
esperado reordenado pela ordem documentada (Latin-1, CRLF inclusive no fim, sem
cabeçalho). O esperado é aberto em modo binário (`.gitattributes -text`).

Depois: determinismo (SHA-256), só linhas com conta, a partida pelo sinal, as cinco
recusas 409 (nenhuma produz bytes nem ecoa texto) e o formatador de valor por
propriedade (hypothesis).
"""

from __future__ import annotations

import csv
import dataclasses
import inspect
import io
import random
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from hypothesis import given
from hypothesis import strategies as st
from structlog.testing import capture_logs

from app.core.exceptions import (
    AccountingFileIncompletePartidaError,
    AccountingFilePartialCoverageError,
    AccountingFilePartitionError,
    AccountingFileTextDoesNotFitError,
    AccountingFileWrongDestinationError,
    AppError,
)
from app.db.models import DecimalSeparator, MaterializedSituation
from app.modules.accounting_files import generator as generator_module
from app.modules.accounting_files.generator import (
    ExportLine,
    MaterializationTotals,
    format_amount,
    generate_accounting_file,
    sort_key,
)
from app.modules.client_file_ingestion.reader import parse_amount
from app.modules.client_mapping.apply import AppliedItem, apply_mapping
from app.modules.export_layouts.definition import (
    DOMINIO_TEMPLATE,
    AmountFormat,
    LayoutField,
    parse_definition,
)

pytestmark = pytest.mark.unit

_MONTH = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "accounting_sample"
    / "cliente_exemplo_2026_08"
)
_BANK = "649"
AGO = date(2026, 8, 1)
DOMINIO = DOMINIO_TEMPLATE.definition


# ---------------------------------------------------------------------------
# A amostra, pelo MESMO caminho da materialização
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Mv:
    source_movement_id: str
    movement_date: date
    amount: Decimal
    category_code: str | None
    source_type: str = "arquivo"
    source_account_id: str | None = None
    status: str = "presente"


@dataclass(frozen=True)
class _Dec:
    id: UUID
    category_code: str
    decision_type: str = "alvo"
    source_type: str = "arquivo"
    effective_from: date = AGO


def _extrato() -> list[tuple[date, str, Decimal]]:
    text = (_MONTH / "extrato_cliente.csv").read_text(encoding="utf-8")
    return [
        (
            datetime.strptime(r["Data"], "%d/%m/%Y").date(),
            r["Descricao"].strip(),
            parse_amount(r["Valor"], DecimalSeparator.COMMA),
        )
        for r in csv.DictReader(io.StringIO(text), delimiter=";")
    ]


def _decisoes() -> dict[str, tuple[str, str]]:
    text = (_MONTH / "decisoes_depara.csv").read_text(encoding="utf-8")
    return {
        r["categoria_origem"].strip(): (r["conta_contabil"], r["historico_padrao"])
        for r in csv.DictReader(io.StringIO(text), delimiter=";")
    }


def _esperado() -> bytes:
    return (_MONTH / "lancamentos_esperados.csv").read_bytes()


def _smid(index: int) -> str:
    """Identidade da linha na origem — o índice da linha no arquivo, com zeros à esquerda."""
    return f"L{index:04d}"


def _line(item: AppliedItem, history: str | None) -> ExportLine:
    """O que `materialized_lines` devolve para um item (a 13.4 converte igual)."""
    return ExportLine(
        item_id=uuid4(),
        source_type=item.source_type,
        source_movement_id=item.source_movement_id,
        source_account_id=item.source_account_id,
        movement_date=item.movement_date,
        amount=item.amount,
        category_code=item.category_code,
        situation=item.situation.value,
        accounting_account_code=item.accounting_account_code,
        bank_account_code=item.bank_account_code,
        history_present=item.history_present,
        history=history,
    )


def _amostra() -> tuple[MaterializationTotals, list[ExportLine]]:
    extrato = _extrato()
    decisoes = _decisoes()
    # Código de categoria como o da origem por arquivo: derivado, estável, sem o texto.
    code_of = {desc: f"CAT{n:02d}" for n, desc in enumerate(sorted(decisoes), start=1)}
    movements = [
        _Mv(_smid(i), d, amount, code_of[desc]) for i, (d, desc, amount) in enumerate(extrato)
    ]
    decisions = {code: _Dec(uuid4(), code) for code in code_of.values()}
    keys = {("arquivo", code): desc for desc, code in code_of.items()}
    result = apply_mapping(
        movements,
        list(decisions.values()),
        AGO,
        target_code_of=dict.fromkeys(keys),
        accounting_code_of={key: decisoes[desc][0] for key, desc in keys.items()},
        bank_bindings={("arquivo", None): _BANK},
        history_present_of=dict.fromkeys(keys, True),
    )
    history_of = {code: decisoes[desc][1] for desc, code in code_of.items()}
    lines = [_line(item, history_of.get(item.category_code or "")) for item in result.items]
    totals = MaterializationTotals(
        destination_type="conta_contabil",
        competence=AGO,
        partial_coverage_confirmed=False,
        not_mapped_amount=result.totals[MaterializedSituation.NAO_MAPEAR].amount,
        undecided_amount=result.totals[MaterializedSituation.SEM_DECISAO].amount,
        uncategorized_amount=result.totals[MaterializedSituation.SEM_CATEGORIA].amount,
    )
    return totals, lines


class TestAmostraOuro:
    def test_32_de_32_linhas_identicas_byte_a_byte_multiconjunto(self) -> None:
        totals, lines = _amostra()
        generated = generate_accounting_file(totals, lines, DOMINIO)
        esperado = _esperado()
        assert generated.lines == 32
        got = generated.content.split(b"\r\n")
        want = esperado.split(b"\r\n")
        assert got[-1] == b"", "CRLF também depois da última linha"
        assert want[-1] == b""
        assert sorted(got[:-1]) == sorted(want[:-1])
        batem = sum(1 for line in got[:-1] if line in want[:-1])
        assert batem == 32

    def test_arquivo_inteiro_igual_ao_esperado_reordenado_pela_ordem_documentada(self) -> None:
        totals, lines = _amostra()
        generated = generate_accounting_file(totals, lines, DOMINIO)
        want = _esperado().split(b"\r\n")[:-1]
        extrato = _extrato()
        # A linha i do esperado é o movimento i do extrato (a amostra está alinhada, S16).
        order = sorted(range(32), key=lambda i: (extrato[i][0], _smid(i)))
        reordenado = b"".join(want[i] + b"\r\n" for i in order)
        assert generated.content == reordenado
        assert not generated.content.startswith(b"data"), "sem cabeçalho"

    def test_latin1_e_os_totais(self) -> None:
        totals, lines = _amostra()
        generated = generate_accounting_file(totals, lines, DOMINIO)
        assert "FÁCIL".encode("latin-1") in generated.content  # acento da amostra, em Latin-1
        assert generated.total_amount == Decimal("53570.99")  # 29.377,33 + 24.193,66

    def test_duas_geracoes_mesmo_sha256_mesmo_com_a_entrada_embaralhada(self) -> None:
        totals, lines = _amostra()
        primeira = generate_accounting_file(totals, lines, DOMINIO)
        segunda = generate_accounting_file(totals, lines, DOMINIO)
        assert primeira.sha256 == segunda.sha256
        assert len(primeira.sha256) == 64
        for _ in range(5):
            shuffled = list(lines)
            random.shuffle(shuffled)
            assert generate_accounting_file(totals, shuffled, DOMINIO).content == (primeira.content)

    def test_o_historico_sai_exatamente_como_na_vigencia(self) -> None:
        totals, lines = _amostra()
        exotico = "Pgto  Á vista (duplo espaço)  "
        lines = [
            dataclasses.replace(line, history=exotico) if i == 0 else line
            for i, line in enumerate(sorted(lines, key=sort_key))
        ]
        content = generate_accounting_file(totals, lines, DOMINIO).content.decode("latin-1")
        first = content.split("\r\n")[0]
        assert first.endswith(";" + exotico)


# ---------------------------------------------------------------------------
# Linhas sintéticas (as quatro situações, partida, recusas)
# ---------------------------------------------------------------------------


def _l(
    smid: str,
    amount: str,
    *,
    situation: str = "alvo",
    code: str | None = "C1",
    account: str | None = "662",
    bank: str | None = "649",
    history: str | None = "HISTORICO PADRAO",
    history_present: bool | None = True,
    day: int = 5,
) -> ExportLine:
    alvo = situation == "alvo"
    return ExportLine(
        item_id=uuid4(),
        source_type="arquivo",
        source_movement_id=smid,
        source_account_id=None,
        movement_date=date(2026, 8, day),
        amount=Decimal(amount),
        category_code=code,
        situation=situation,
        accounting_account_code=account if alvo else None,
        bank_account_code=bank if alvo else None,
        history_present=history_present if alvo else None,
        history=history if alvo else None,
    )


def _totals(lines: list[ExportLine], **over: Any) -> MaterializationTotals:
    def soma(situation: str) -> Decimal:
        return sum((abs(x.amount) for x in lines if x.situation == situation), Decimal("0.00"))

    base: dict[str, Any] = {
        "destination_type": "conta_contabil",
        "competence": AGO,
        "partial_coverage_confirmed": False,
        "not_mapped_amount": soma("nao_mapear"),
        "undecided_amount": soma("sem_decisao"),
        "uncategorized_amount": soma("sem_categoria"),
    }
    return MaterializationTotals(**(base | over))


def _refused(lines: list[ExportLine], totals: MaterializationTotals | None = None) -> AppError:
    with capture_logs() as logs, pytest.raises(AppError) as exc:
        generate_accounting_file(totals or _totals(lines), lines, DOMINIO)
    assert exc.value.status_code == 409
    for line in lines:
        if line.history:
            dump = repr(logs) + exc.value.message + exc.value.user_message + repr(exc.value.details)
            assert line.history not in dump, "a recusa nunca ecoa o histórico"
    return exc.value


class TestSoLinhasComConta:
    def test_nao_mapear_sem_decisao_e_sem_categoria_ficam_fora(self) -> None:
        lines = [
            _l("1", "10.00"),
            _l("2", "-20.00", situation="nao_mapear", code="C2"),
            _l("3", "-30.00", situation="sem_decisao", code="C3"),
            _l("4", "-40.00", situation="sem_categoria", code=None),
        ]
        generated = generate_accounting_file(_totals(lines), lines, DOMINIO)
        assert generated.lines == 1
        assert generated.content == b"05/08/2026;649;662;R$ 10,00;HISTORICO PADRAO\r\n"
        assert generated.total_amount == Decimal("10.00")

    def test_entrada_debita_o_banco_e_saida_credita(self) -> None:
        lines = [_l("1", "1234.56"), _l("2", "-0.13", day=6)]
        text = generate_accounting_file(_totals(lines), lines, DOMINIO).content.decode("latin-1")
        assert text == (
            "05/08/2026;649;662;R$ 1.234,56;HISTORICO PADRAO\r\n"
            "06/08/2026;662;649;R$ 0,13;HISTORICO PADRAO\r\n"
        )

    def test_valor_zero_nao_e_lancamento(self) -> None:
        lines = [_l("1", "0.00"), _l("2", "5.00")]
        assert generate_accounting_file(_totals(lines), lines, DOMINIO).lines == 1

    def test_ordem_data_depois_identificador_depois_item(self) -> None:
        lines = [_l("B", "1.00", day=6), _l("B", "2.00", day=5), _l("A", "3.00", day=5)]
        text = generate_accounting_file(_totals(lines), lines, DOMINIO).content.decode("latin-1")
        assert [row.split(";")[3] for row in text.split("\r\n")[:-1]] == [
            "R$ 3,00",
            "R$ 2,00",
            "R$ 1,00",
        ]

    def test_layout_generico_com_cabecalho_lf_e_outras_colunas(self) -> None:
        raw = DOMINIO.to_json()
        raw.update(
            {
                "hasHeader": True,
                "lineEnding": "lf",
                "separator": "|",
                "encoding": "utf-8",
                "dateFormat": "aaaa-mm-dd",
                "columns": [
                    {"field": "competencia", "header": "Comp"},
                    {"field": "codigo_categoria_origem", "header": None},
                    {"field": "valor", "header": "Valor"},
                ],
            }
        )
        raw["amountFormat"] = {
            "prefix": "",
            "thousandsSeparator": "",
            "decimalSeparator": ".",
            "decimalPlaces": 2,
        }
        lines = [_l("1", "-1500.50")]
        content = generate_accounting_file(_totals(lines), lines, parse_definition(raw)).content
        assert content == b"Comp|codigo_categoria_origem|Valor\n2026-08|C1|1500.50\n"


class TestRecusas:
    def test_a_destino_diferente_de_conta_contabil(self) -> None:
        lines = [_l("1", "10.00")]
        error = _refused(lines, _totals(lines, destination_type="demonstrativo_contabil"))
        assert isinstance(error, AccountingFileWrongDestinationError)

    def test_b_cobertura_parcial_nomeia_as_categorias_sem_decisao(self) -> None:
        lines = [
            _l("1", "10.00"),
            _l("2", "-5.00", situation="sem_decisao", code="Z9"),
            _l("3", "-6.00", situation="sem_decisao", code="A1"),
        ]
        error = _refused(lines, _totals(lines, partial_coverage_confirmed=True))
        assert isinstance(error, AccountingFilePartialCoverageError)
        assert error.details == {"categoryCodes": ["A1", "Z9"]}

    @pytest.mark.parametrize(
        "over",
        [
            {"history": None, "history_present": False},
            {"bank": None},
            {"account": None},  # decisão LEGADA do catálogo: alvo sem conta do plano
            {"history": "[indecifrável]"},
            {"history": None},  # encerramento purgou a vigência
        ],
        ids=["sem_historico", "sem_banco", "legada", "indecifravel", "vigencia_purgada"],
    )
    def test_c_completude_abaixo_de_100_nomeia_as_categorias(self, over: dict[str, Any]) -> None:
        lines = [_l("1", "10.00"), _l("2", "-7.00", code="C7", **over)]
        error = _refused(lines)
        assert isinstance(error, AccountingFileIncompletePartidaError)
        assert error.details["categoryCodes"] == ["C7"]

    def test_d_particao_que_nao_fecha_devolve_as_parcelas(self) -> None:
        lines = [_l("1", "10.00"), _l("2", "-4.00", situation="nao_mapear", code="C2")]
        error = _refused(lines, _totals(lines, not_mapped_amount=Decimal("5.00")))
        assert isinstance(error, AccountingFilePartitionError)
        assert error.details == {
            "competenceAmount": "14.00",
            "withAccountAmount": "10.00",
            "notMappedAmount": "5.00",
            "undecidedAmount": "0.00",
            "uncategorizedAmount": "0.00",
        }

    @pytest.mark.parametrize(
        ("history", "reason"),
        [
            ("ALUGUEL; LOJA 4", "contem_separador"),
            ("ALUGUEL\nLOJA 4", "quebra_de_linha"),
            ("ALUGUEL\rLOJA 4", "quebra_de_linha"),
            ("ALUGUEL 10 €", "fora_da_codificacao"),
            ("ALUGUEL — LOJA 4", "fora_da_codificacao"),
            ("=SOMA(A1)", "inicio_de_formula"),
            ("+55 11", "inicio_de_formula"),
            ("-DESCONTO", "inicio_de_formula"),
            ("@usuario", "inicio_de_formula"),
        ],
    )
    def test_e_texto_que_nao_cabe_nomeia_a_categoria(self, history: str, reason: str) -> None:
        lines = [_l("1", "10.00"), _l("2", "-3.00", code="C9", history=history)]
        error = _refused(lines)
        assert isinstance(error, AccountingFileTextDoesNotFitError)
        assert error.details == {
            "categories": [{"categoryCode": "C9", "field": "historico", "reason": reason}]
        }
        assert history not in error.user_message

    def test_e_checa_os_valores_distintos_e_nomeia_cada_categoria_uma_vez(self) -> None:
        lines = [
            _l("1", "1.00", code="C1", history="A;B"),
            _l("2", "2.00", code="C1", history="A;B"),
            _l("3", "3.00", code="C2", history="A;B"),
        ]
        error = _refused(lines)
        assert [c["categoryCode"] for c in error.details["categories"]] == ["C1", "C2"]

    def test_nada_e_alterado_em_silencio(self) -> None:
        """Não existe caminho de substituir/escapar/truncar/quotePrefix no gerador."""
        # O CÓDIGO, sem o docstring do módulo (que explica por que nada disso existe).
        source = inspect.getsource(generator_module).split('"""', 2)[2]
        for proibido in (
            "neutralize_formula_injection",
            "quotePrefix",
            'errors="replace"',
            'errors="ignore"',
            ".replace(",
            "[:MAX",
        ):
            assert proibido not in source, proibido


class TestFontesUnicas:
    def test_partida_e_completude_vem_das_funcoes_da_s16(self) -> None:
        source = inspect.getsource(generator_module)
        assert "derive_partida(" in source
        assert "partida_completeness(" in source
        assert "is_partida_completa(" in source
        # Nenhuma reimplementação da regra de sinal nem releitura de movimento cru.
        for proibido in (
            "amount > 0",
            "amount < 0",
            "client_movements.repository",
            "apply_mapping",
            "sqlalchemy",
            "httpx",
            "AsyncSession",
        ):
            assert proibido not in source, proibido

    def test_o_vocabulario_e_o_da_validacao(self) -> None:
        assert generator_module.LayoutField is LayoutField

    def test_repr_da_linha_nao_mostra_o_historico(self) -> None:
        assert "HISTORICO PADRAO" not in repr(_l("1", "1.00"))


# ---------------------------------------------------------------------------
# Formatador de valor — propriedade
# ---------------------------------------------------------------------------

_MONEY = st.decimals(
    min_value=Decimal("-999999999999.99"),
    max_value=Decimal("999999999999.99"),
    places=2,
    allow_nan=False,
    allow_infinity=False,
)


def _parse(text: str, fmt: AmountFormat) -> Decimal:
    body = text.removeprefix(fmt.prefix)
    if fmt.thousands_separator:
        body = body.replace(fmt.thousands_separator, "")
    return Decimal(body.replace(fmt.decimal_separator, "."))


class TestFormatadorDeValor:
    @given(_MONEY)
    def test_ida_e_volta_no_modelo_dominio(self, value: Decimal) -> None:
        fmt = DOMINIO.amount_format
        text = format_amount(value, fmt)
        assert _parse(text, fmt) == abs(value)
        assert "E" not in text
        assert "e" not in text
        assert text.startswith("R$ ")
        integer = text.removeprefix("R$ ").split(",")[0]
        groups = integer.split(".")
        assert 1 <= len(groups[0]) <= 3
        assert all(len(g) == 3 for g in groups[1:])
        assert text.split(",")[1].isdigit()
        assert len(text.split(",")[1]) == 2

    @given(_MONEY, st.sampled_from([2, 3, 4]))
    def test_mais_casas_so_completam_com_zeros(self, value: Decimal, places: int) -> None:
        fmt = AmountFormat(
            prefix="", thousands_separator="", decimal_separator=".", decimal_places=places
        )
        text = format_amount(value, fmt)
        assert Decimal(text) == abs(value)
        assert len(text.split(".")[1]) == places

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("5466.87", "R$ 5.466,87"),
            ("0.13", "R$ 0,13"),
            ("-24193.66", "R$ 24.193,66"),
            ("1000000.00", "R$ 1.000.000,00"),
            ("0.00", "R$ 0,00"),
            ("100.00", "R$ 100,00"),
            ("1E+2", "R$ 100,00"),
        ],
    )
    def test_casos_da_amostra(self, value: str, expected: str) -> None:
        assert format_amount(Decimal(value), DOMINIO.amount_format) == expected
