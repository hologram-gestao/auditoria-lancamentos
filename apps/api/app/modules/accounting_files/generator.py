"""Gerador GENÉRICO e determinístico do arquivo contábil — núcleo PURO (Sprint 13, BACK 13.3 — R2).

Transforma UMA materialização do de-para (destino `conta_contabil`) + UMA versão de
layout em bytes, ou recusa. Sem rotas, sem banco, sem relógio, sem log: a rota, o
registro da geração e a trilha são da 13.4.

**Genérico:** interpreta a definição do layout (`export_layouts.definition`), nunca um
gerador por sistema de destino. O vocabulário de campos é a MESMA enum da validação.

**Fontes ÚNICAS, nada recalculado:**
  - as linhas vêm de `ClientMappingApplyService.materialized_lines` (snapshot do item +
    histórico lido PELA VIGÊNCIA), convertidas em `ExportLine` pela 13.4 — o gerador
    nunca relê movimento cru nem reaplica o de-para;
  - débito/crédito vêm de `partida.derive_partida` (pelo SINAL do valor);
  - "partida completa" é `partida.is_partida_completa` e a completude é
    `completeness.partida_completeness`.

**Só linhas com conta decidida entram** (situação `alvo` com `accounting_account_code`);
`nao_mapear`, sem decisão e sem categoria ficam fora. Linha de valor ZERO não é
lançamento (`derive_partida` devolve `None`) e também fica fora.

**ORDEM ESTÁVEL E DOCUMENTADA:** data do movimento, depois `source_movement_id` (texto,
ordem lexicográfica), depois o id do item. A mesma materialização com a mesma versão de
layout produz os MESMOS bytes (e o mesmo SHA-256).

**Recusas — todas 409 tipadas, levantadas ANTES de montar o arquivo, nesta ordem:**
  a. destino ≠ `conta_contabil`;
  b. materialização confirmada com cobertura parcial → nomeia as categorias sem decisão;
  c. completude de partida < 100% (sem histórico, sem conta do banco, decisão legada, ou
     histórico que não se lê mais — `None`/`[indecifrável]`) → nomeia as categorias;
  d. identidade de partição que não fecha → devolve as parcelas;
  e. texto que não cabe (fora da codificação, contém o separador, contém quebra de
     linha, começa com `=`, `+`, `-` ou `@`), checado sobre os valores textuais
     DISTINTOS → nomeia a categoria.
As recusas nomeiam SÓ CÓDIGOS (§4.5): nunca histórico, descrição ou nome. E nenhum texto
é alterado em silêncio — nada de substituir, escapar, truncar ou `quotePrefix` (o
`neutralize_formula_injection` do Excel NÃO serve aqui: mudaria o que entra na
contabilidade do cliente).

**Dinheiro em `Decimal` do começo ao fim** (§3.4); o valor sai absoluto, no formato do
layout, e nunca é arredondado (o layout exige 2+ casas; mais casas completam com zeros).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.core.exceptions import (
    AccountingFileIncompletePartidaError,
    AccountingFilePartialCoverageError,
    AccountingFilePartitionError,
    AccountingFileTextDoesNotFitError,
    AccountingFileWrongDestinationError,
)
from app.db.models import ACCOUNTING_DESTINATION_TYPE, MaterializedSituation
from app.modules.client_mapping.accounting import HISTORY_UNDECIPHERABLE
from app.modules.client_mapping.completeness import partida_completeness
from app.modules.client_mapping.partida import Partida, derive_partida, is_partida_completa
from app.modules.client_movements.competence import format_competence
from app.modules.export_layouts.definition import AmountFormat, LayoutDefinition, LayoutField

_ZERO = Decimal("0.00")
_HUNDRED = Decimal("100")
_ALVO = MaterializedSituation.ALVO.value
_SEM_DECISAO = MaterializedSituation.SEM_DECISAO.value

#: Caracteres que, no INÍCIO de uma célula, planilhas interpretam como fórmula.
FORMULA_PREFIXES = ("=", "+", "-", "@")

#: Campos cujo conteúdo é TEXTO vindo do dado (checados na recusa "e"). Data e valor são
#: formatados pelo gerador a partir do layout, já validado na 13.2.
_TEXT_FIELDS = frozenset(
    {
        LayoutField.CONTA_DEBITO,
        LayoutField.CONTA_CREDITO,
        LayoutField.HISTORICO,
        LayoutField.COMPETENCIA,
        LayoutField.CODIGO_CATEGORIA_ORIGEM,
    }
)


class TextRefusalReason(StrEnum):
    """Por que um texto não cabe no arquivo — vocabulário FECHADO (vai em `details`)."""

    QUEBRA_DE_LINHA = "quebra_de_linha"
    CONTEM_SEPARADOR = "contem_separador"
    INICIO_DE_FORMULA = "inicio_de_formula"
    FORA_DA_CODIFICACAO = "fora_da_codificacao"


@dataclass(frozen=True, slots=True)
class ExportLine:
    """Uma linha da materialização, como `materialized_lines` a devolve (snapshot + histórico).

    Satisfaz o protocolo de linha de `partida`/`completeness` — o predicado e a
    completude são os ÚNICOS, chamados sobre ESTE objeto. `history` é o texto da
    VIGÊNCIA decifrado na leitura; fica fora do `repr` (nunca vai a log).
    """

    item_id: UUID
    source_type: str
    source_movement_id: str
    source_account_id: str | None
    movement_date: date
    amount: Decimal
    category_code: str | None
    situation: str
    accounting_account_code: str | None
    bank_account_code: str | None
    history_present: bool | None
    history: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class MaterializationTotals:
    """O que o gerador lê do REGISTRO da materialização (imutável)."""

    destination_type: str
    competence: date
    partial_coverage_confirmed: bool
    not_mapped_amount: Decimal
    undecided_amount: Decimal
    uncategorized_amount: Decimal


@dataclass(frozen=True, slots=True)
class GeneratedFile:
    """Os bytes e os metadados que a 13.4 registra (o conteúdo nunca persiste)."""

    content: bytes = field(repr=False)
    lines: int
    total_amount: Decimal
    sha256: str


def sort_key(line: ExportLine) -> tuple[date, str, str]:
    """A ordem DOCUMENTADA das linhas: data, `source_movement_id`, id do item."""
    return (line.movement_date, line.source_movement_id, str(line.item_id))


def format_amount(value: Decimal, fmt: AmountFormat) -> str:
    """Valor ABSOLUTO no formato do layout: prefixo, milhar, decimal e casas.

    `Decimal` do começo ao fim (nunca `float`, nunca notação científica: `format(…, "f")`).
    Não arredonda: o layout exige 2+ casas e o dinheiro do sistema tem 2, então o
    `quantize` só completa com zeros — se mudasse o valor, é defeito de quem chamou.
    """
    absolute = abs(value)
    quantized = absolute.quantize(Decimal(1).scaleb(-fmt.decimal_places))
    if quantized != absolute:
        msg = f"valor com mais casas que o layout ({fmt.decimal_places})"
        raise ValueError(msg)
    integer, _, fraction = format(quantized, "f").partition(".")
    if fmt.thousands_separator:
        groups: list[str] = []
        while len(integer) > 3:
            groups.insert(0, integer[-3:])
            integer = integer[:-3]
        groups.insert(0, integer)
        integer = fmt.thousands_separator.join(groups)
    return f"{fmt.prefix}{integer}{fmt.decimal_separator}{fraction}"


def _is_file_line(line: ExportLine) -> bool:
    """Só entra no arquivo a linha com CONTA DECIDIDA."""
    return line.situation == _ALVO and line.accounting_account_code is not None


def _history_unreadable(line: ExportLine) -> bool:
    return line.history is None or line.history == HISTORY_UNDECIPHERABLE


def _codes(lines: Iterable[ExportLine]) -> list[str]:
    return sorted({line.category_code or "" for line in lines})


def _check_destination(totals: MaterializationTotals) -> None:
    if totals.destination_type != ACCOUNTING_DESTINATION_TYPE:
        raise AccountingFileWrongDestinationError(
            f"Materialização do destino {totals.destination_type!r}, não conta_contabil."
        )


def _check_partial_coverage(totals: MaterializationTotals, lines: Sequence[ExportLine]) -> None:
    if not totals.partial_coverage_confirmed:
        return
    codes = _codes(line for line in lines if line.situation == _SEM_DECISAO)
    raise AccountingFilePartialCoverageError(
        f"Materialização com cobertura parcial: {len(codes)} categoria(s) sem decisão.",
        user_message=(
            "Esta aplicação do de-para foi confirmada com categorias sem decisão ("
            + ", ".join(codes)
            + "). Decida todas e aplique de novo antes de gerar o arquivo."
        ),
        details={"categoryCodes": codes},
    )


def _check_completeness(lines: Sequence[ExportLine]) -> None:
    completeness = partida_completeness(lines)
    incomplete = _codes(
        line
        for line in lines
        if line.situation == _ALVO and (not is_partida_completa(line) or _history_unreadable(line))
    )
    below = completeness.pct is not None and completeness.pct < _HUNDRED
    if not incomplete and not below:
        return
    raise AccountingFileIncompletePartidaError(
        f"Completude de partida {completeness.pct}% com {len(incomplete)} categoria(s) "
        "incompleta(s).",
        user_message=(
            "Estas categorias estão com a partida incompleta (sem histórico, sem conta do "
            "banco ou com decisão antiga): " + ", ".join(incomplete) + ". Complete o "
            "de-para e aplique de novo."
        ),
        details={
            "categoryCodes": incomplete,
            "completenessPct": None if completeness.pct is None else str(completeness.pct),
        },
    )


def partition(totals: MaterializationTotals, lines: Sequence[ExportLine]) -> dict[str, Decimal]:
    """As parcelas da identidade de partição — de ONDE sai cada uma (ADR-092-BE):

    - `competenceAmount`: Σ|valor| de TODOS os itens do snapshot (a competência inteira
      como foi materializada);
    - `withAccountAmount`: Σ|valor| dos itens `alvo` COM conta decidida — recomputada dos
      itens (é exatamente o que o arquivo leva);
    - `notMappedAmount`, `undecidedAmount`, `uncategorizedAmount`: as colunas
      `not_mapped_amount`, `undecided_amount`, `uncategorized_amount` do REGISTRO.
    """
    return {
        "competenceAmount": sum((abs(line.amount) for line in lines), _ZERO),
        "withAccountAmount": sum(
            (abs(line.amount) for line in lines if _is_file_line(line)), _ZERO
        ),
        "notMappedAmount": totals.not_mapped_amount,
        "undecidedAmount": totals.undecided_amount,
        "uncategorizedAmount": totals.uncategorized_amount,
    }


def _check_partition(totals: MaterializationTotals, lines: Sequence[ExportLine]) -> None:
    parcels = partition(totals, lines)
    closing = (
        parcels["withAccountAmount"]
        + parcels["notMappedAmount"]
        + parcels["undecidedAmount"]
        + parcels["uncategorizedAmount"]
    )
    if closing == parcels["competenceAmount"]:
        return
    raise AccountingFilePartitionError(
        f"Partição não fecha: {closing} ≠ {parcels['competenceAmount']}.",
        details={name: str(value) for name, value in parcels.items()},
    )


def _text_reason(value: str, definition: LayoutDefinition) -> TextRefusalReason | None:
    if "\r" in value or "\n" in value:
        return TextRefusalReason.QUEBRA_DE_LINHA
    if definition.separator in value:
        return TextRefusalReason.CONTEM_SEPARADOR
    if value.startswith(FORMULA_PREFIXES):
        return TextRefusalReason.INICIO_DE_FORMULA
    try:
        value.encode(definition.encoding, errors="strict")
    except UnicodeEncodeError:
        return TextRefusalReason.FORA_DA_CODIFICACAO
    return None


def _text_values(
    line: ExportLine, text_field: LayoutField, competence: str
) -> tuple[str | None, ...]:
    if text_field in (LayoutField.CONTA_DEBITO, LayoutField.CONTA_CREDITO):
        # Os dois lados da partida aparecem nas duas colunas conforme o sinal: os dois
        # códigos da linha são checados, qualquer que seja a coluna.
        return (line.accounting_account_code, line.bank_account_code)
    if text_field is LayoutField.HISTORICO:
        return (line.history,)
    if text_field is LayoutField.COMPETENCIA:
        return (competence,)
    return (line.category_code,)


def _check_text(
    totals: MaterializationTotals, lines: Sequence[ExportLine], definition: LayoutDefinition
) -> None:
    """Checa os valores textuais DISTINTOS de cada coluna de texto do layout."""
    competence = format_competence(totals.competence)
    text_fields = sorted({c.field for c in definition.columns if c.field in _TEXT_FIELDS}, key=str)
    verdicts: dict[str, TextRefusalReason | None] = {}
    problems: set[tuple[str, str, str]] = set()
    for line in lines:
        if not _is_file_line(line):
            continue
        for text_field in text_fields:
            for value in _text_values(line, text_field, competence):
                if value is None:
                    continue
                if value not in verdicts:
                    verdicts[value] = _text_reason(value, definition)
                reason = verdicts[value]
                if reason is not None:
                    problems.add((line.category_code or "", text_field.value, reason.value))
    if not problems:
        return
    ordered = sorted(problems)
    codes = sorted({code for code, _, _ in ordered})
    raise AccountingFileTextDoesNotFitError(
        f"{len(ordered)} texto(s) não cabem no layout, em {len(codes)} categoria(s).",
        user_message=(
            "O histórico destas categorias tem caractere que o arquivo não aceita (fora da "
            "codificação, o separador, quebra de linha ou início com =, +, - ou @): "
            + ", ".join(codes)
            + ". Edite o histórico e aplique o de-para de novo."
        ),
        details={
            "categories": [
                {"categoryCode": code, "field": text_field, "reason": reason}
                for code, text_field, reason in ordered
            ]
        },
    )


def _cell(
    column: LayoutField,
    line: ExportLine,
    partida: Partida,
    definition: LayoutDefinition,
    competence: str,
) -> str:
    if column is LayoutField.DATA:
        return line.movement_date.strftime(definition.strftime)
    if column is LayoutField.CONTA_DEBITO:
        return partida.debit
    if column is LayoutField.CONTA_CREDITO:
        return partida.credit
    if column is LayoutField.VALOR:
        return format_amount(line.amount, definition.amount_format)
    if column is LayoutField.HISTORICO:
        return line.history or ""
    if column is LayoutField.COMPETENCIA:
        return competence
    return line.category_code or ""


def generate_accounting_file(
    totals: MaterializationTotals,
    lines: Sequence[ExportLine],
    definition: LayoutDefinition,
) -> GeneratedFile:
    """Recusa (a → e) ou devolve os bytes do arquivo, determinísticos, e o SHA-256."""
    _check_destination(totals)
    _check_partial_coverage(totals, lines)
    _check_completeness(lines)
    _check_partition(totals, lines)
    _check_text(totals, lines, definition)

    competence = format_competence(totals.competence)
    rows: list[str] = []
    if definition.has_header:
        rows.append(definition.separator.join(c.header_text for c in definition.columns))
    written = 0
    total = _ZERO
    for line in sorted((ln for ln in lines if _is_file_line(ln)), key=sort_key):
        partida = derive_partida(
            line.amount, line.accounting_account_code or "", line.bank_account_code or ""
        )
        if partida is None:
            continue
        rows.append(
            definition.separator.join(
                _cell(c.field, line, partida, definition, competence) for c in definition.columns
            )
        )
        written += 1
        total += abs(line.amount)
    eol = definition.line_ending.chars
    try:
        content = "".join(row + eol for row in rows).encode(definition.encoding, errors="strict")
    except UnicodeEncodeError:
        # Inalcançável: os textos do dado passaram pela recusa "e" e os do layout pela
        # validação da 13.2. Se acontecer, é recusa — nunca um arquivo alterado.
        raise AccountingFileTextDoesNotFitError(
            "Texto fora da codificação do layout depois das checagens.",
            details={"categories": []},
        ) from None
    return GeneratedFile(
        content=content,
        lines=written,
        total_amount=total,
        sha256=hashlib.sha256(content).hexdigest(),
    )
