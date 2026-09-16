"""Divisão de arquivo tabular em blocos e junção dos resultados (86e39xvxm).

O parsing manda o arquivo inteiro numa única chamada à Anthropic, sem streaming,
com teto de tempo fixo (`ANTHROPIC_TIMEOUT_SECONDS`). Como o prompt exige
descrição exata e saldo por linha, o tempo de geração cresce com o número de
linhas — e um extrato grande (report da Camila, 16/09/2026: CSV do Inter com
300 a 400 linhas) estoura o teto de forma determinística. Tentar de novo não
ajuda.

Para entrada em TEXTO (CSV e XLSX já renderizado em TSV) o arquivo é dividido em
blocos de linhas, cada bloco vira uma chamada, e os resultados são juntados. PDF
fica fora: não dá para cortar por linha com segurança.

Duas funções puras, sem I/O:

- `plan_blocks`: decide se divide e monta os blocos. Corta por REGISTRO lógico
  (uma célula com quebra de linha dentro de aspas nunca é partida), repete o
  preâmbulo e o cabeçalho em todo bloco, e deixa o rodapé (linhas depois da
  última linha de dados, ex.: "Saldo final") só no último.
- `merge_statements`: concatena as movimentações na ordem dos blocos; saldo
  inicial do primeiro, final do último; período pela união. `account_type`
  divergente entre blocos é erro acionável, nunca escolha silenciosa.

Nada aqui é logado: o conteúdo é dado do cliente final (§3.3, §4.5).
"""

from __future__ import annotations

import csv
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.exceptions import AnthropicParseError
from app.integrations.anthropic.schemas import ExtractedStatement

# Delimitadores reconhecidos. O CSV do Inter usa `;` (valores com vírgula decimal);
# a renderização de XLSX (`_xlsx_to_text`) usa `\t`.
_DELIMITERS: tuple[str, ...] = (";", ",", "\t", "|")

# Uma linha de cabeçalho não traz data nem valor. Se a primeira linha "tabular"
# tiver cara de dado, o arquivo não tem cabeçalho — repeti-la em todo bloco
# duplicaria uma transação.
_DATE_LIKE = re.compile(r"^\s*(\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})\s*$")
_NUMBER_LIKE = re.compile(r"^\s*[-+]?(R\$\s*)?[\d.]+(,\d+)?\s*$|^\s*[-+]?\d+(\.\d+)?\s*$")

_UNKNOWN_BANK = "Desconhecido"


@dataclass(frozen=True, slots=True)
class BlockPlan:
    """Resultado de `plan_blocks`.

    `blocks` tem UM elemento (o texto original) quando não vale dividir —
    arquivo pequeno, ou texto sem estrutura tabular reconhecível.
    """

    blocks: list[str]
    data_records: int
    delimiter: str | None

    @property
    def is_split(self) -> bool:
        return len(self.blocks) > 1


def plan_blocks(text: str, *, chunk_rows: int, min_rows: int) -> BlockPlan:
    """Divide `text` em blocos de até ~`chunk_rows` registros de dados.

    Args:
        text: CSV decodificado ou TSV renderizado do XLSX.
        chunk_rows: tamanho-alvo do bloco. Os blocos saem EQUILIBRADOS
            (`ceil(registros / chunk_rows)` blocos de tamanho parecido) para não
            sobrar um bloco minúsculo no fim.
        min_rows: abaixo disto (inclusive) o arquivo vai inteiro numa chamada só
            — dividir teria só o overhead.

    Returns:
        `BlockPlan`. Sem divisão quando não há delimitador consistente (texto
        não tabular) ou quando os registros de dados cabem em `min_rows`.
    """
    records = _logical_records(text)
    layout = _detect_layout(records)
    if layout is None:
        return BlockPlan(blocks=[text], data_records=0, delimiter=None)
    delimiter, modal_fields = layout

    is_data = [_field_count(record, delimiter) == modal_fields for record in records]
    first = is_data.index(True)
    last = len(is_data) - 1 - is_data[::-1].index(True)

    header_fields = next(csv.reader([records[first]], delimiter=delimiter))
    has_header = _looks_like_header(header_fields)
    data_start = first + 1 if has_header else first
    preamble = "".join(records[:data_start])
    data = records[data_start : last + 1]
    footer = "".join(records[last + 1 :])

    data_records = sum(1 for record in data if record.strip())
    if data_records <= min_rows:
        return BlockPlan(blocks=[text], data_records=data_records, delimiter=delimiter)

    n_blocks = math.ceil(data_records / chunk_rows)
    size = math.ceil(len(data) / n_blocks)
    slices = [data[i : i + size] for i in range(0, len(data), size)]
    blocks = [
        preamble + "".join(chunk) + (footer if index == len(slices) - 1 else "")
        for index, chunk in enumerate(slices)
    ]
    return BlockPlan(blocks=blocks, data_records=data_records, delimiter=delimiter)


def merge_statements(parts: Sequence[ExtractedStatement]) -> ExtractedStatement:
    """Junta os statements dos blocos, na ordem, num único `ExtractedStatement`.

    Regras:
        - `transactions`: concatenação na ordem dos blocos.
        - `opening_balance`: do primeiro bloco; `closing_balance`: do último —
          cada bloco vê o mesmo preâmbulo (onde o saldo declarado costuma estar)
          ou o saldo por linha das próprias linhas; a identidade
          `inicial + movimentações = final` é conferida depois pelo checksum.
        - período: união (`min` dos inícios, `max` dos fins).
        - `bank_name`: o primeiro identificado; "Desconhecido" só se todos.
        - `account_type` divergente: `AnthropicParseError` acionável.

    Raises:
        ValueError: lista vazia (erro de programação, não de dado).
        AnthropicParseError: blocos discordam do tipo de conta.
    """
    if not parts:
        raise ValueError("merge_statements recebeu lista vazia")

    account_types = {part.account_type for part in parts}
    if len(account_types) > 1:
        raise AnthropicParseError(
            f"Blocos discordam do account_type: {sorted(account_types)}.",
            user_message=(
                "Não foi possível identificar o tipo de conta de forma consistente "
                "ao longo do arquivo. Envie um período menor e tente novamente."
            ),
        )

    first, last = parts[0], parts[-1]
    bank_name = next(
        (part.bank_name for part in parts if part.bank_name != _UNKNOWN_BANK),
        first.bank_name,
    )
    return ExtractedStatement(
        bank_name=bank_name,
        account_type=first.account_type,
        period_start=min(part.period_start for part in parts),
        period_end=max(part.period_end for part in parts),
        opening_balance=first.opening_balance,
        closing_balance=last.closing_balance,
        transactions=[tx for part in parts for tx in part.transactions],
    )


# ----------------------------------------------------------------------
# Internos
# ----------------------------------------------------------------------


def _logical_records(text: str) -> list[str]:
    """Agrupa linhas físicas em registros: um registro só fecha com aspas balanceadas.

    Mantém os finais de linha originais — os blocos são montados por
    concatenação e o modelo vê exatamente o texto do arquivo.
    """
    records: list[str] = []
    pending: list[str] = []
    quotes = 0
    for line in text.splitlines(keepends=True):
        pending.append(line)
        quotes += line.count('"')
        if quotes % 2 == 0:
            records.append("".join(pending))
            pending = []
            quotes = 0
    if pending:  # aspas desbalanceadas até o fim: fecha como está
        records.append("".join(pending))
    return records


def _field_count(record: str, delimiter: str) -> int:
    if not record.strip():
        return 0
    try:
        return len(next(csv.reader([record], delimiter=delimiter)))
    except (csv.Error, StopIteration):
        return 0


def _detect_layout(records: list[str]) -> tuple[str, int] | None:
    """Escolhe o delimitador cujo número modal de campos cobre mais registros.

    Vírgula decimal ("-247,80") faz o `,` variar de linha para linha; o `;` do
    Inter dá o mesmo número em todas — e vence pela cobertura.
    """
    best: tuple[tuple[int, int], str, int] | None = None
    for delimiter in _DELIMITERS:
        counts = [count for r in records if (count := _field_count(r, delimiter)) >= 2]
        if not counts:
            continue
        modal_fields, coverage = Counter(counts).most_common(1)[0]
        key = (coverage, modal_fields)
        if best is None or key > best[0]:
            best = (key, delimiter, modal_fields)
    if best is None:
        return None
    _, delimiter, modal_fields = best
    return delimiter, modal_fields


def _looks_like_header(fields: list[str]) -> bool:
    """Cabeçalho não tem campo com cara de data nem de valor."""
    return not any(_DATE_LIKE.match(f) or _NUMBER_LIKE.match(f) for f in fields if f.strip())
