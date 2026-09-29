"""Leitura da planilha do plano contábil — o MODELO DA PLATAFORMA (Sprint 16, BACK 16.1 — R1).

**O modelo** (decisão do planejador, ADR-086-BE; é o formato de `plano_contabil.csv`
da amostra anonimizada):

    - CSV UTF-8 (com ou sem BOM) separado por `;`, ou XLSX (primeira aba);
    - cabeçalho na linha 1, com as colunas obrigatórias `codigo_reduzido`, `nome` e
      `tipo`, e a opcional `classificacao` — em qualquer ordem, sem outras colunas;
    - `tipo` ∈ {`analitica`, `sintetica`} (maiúscula e acento indiferentes:
      `Analítica` vale);
    - uma linha por conta, sem código repetido, e pelo menos uma conta.

    Exemplo::

        codigo_reduzido;nome;tipo;classificacao
        649;Banco conta movimento;analitica;1.1.1.02.001
        662;Alugueis a receber - Inquilino D;analitica;1.1.2.01.004

O export nativo do sistema contábil é CONVERTIDO para este modelo (aceitá-lo direto
fica para quando houver amostra, S-1 do PRD).

**Reaproveita o leitor da Sprint 14** (`client_file_ingestion/reader.py`): tipo pelos
magic bytes, nunca pela extensão; limites checados ANTES de iterar (zip, colunas,
linhas percorridas); qualquer falha de abertura ou iteração vira o MESMO 422
`ARQUIVO_INVALIDO` com `raise … from None`. O parse é CPU síncrona: quem está num
handler `async` chama via `run_in_threadpool`.

**Tudo ou nada:** o arquivo INTEIRO é validado antes de qualquer gravação, e a
recusa lista linha x motivo de vocabulário FECHADO (`ChartLineReason`) — nunca o
conteúdo da célula (nome de conta é dado do cliente, §4.5).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from app.core.exceptions import (
    FileHeaderMismatchError,
    FileInvalidError,
    FileLinesInvalidError,
)
from app.db.models.client_accounting_account import (
    MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS,
    MAX_ACCOUNTING_ACCOUNT_CODE_CHARS,
    MAX_ACCOUNTING_ACCOUNT_NAME_CHARS,
    AccountingAccountType,
)
from app.modules.client_file_ingestion.reader import (
    MAX_INVALID_LINES_REPORTED,
    ReadOptions,
    detect_format,
    read_table,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

#: Colunas do modelo. Casadas pelo NOME (aparado, sem distinção de caixa), nunca
#: pela posição.
COLUMN_CODE = "codigo_reduzido"
COLUMN_NAME = "nome"
COLUMN_TYPE = "tipo"
COLUMN_CLASSIFICATION = "classificacao"
REQUIRED_COLUMNS: tuple[str, ...] = (COLUMN_CODE, COLUMN_NAME, COLUMN_TYPE)
OPTIONAL_COLUMNS: tuple[str, ...] = (COLUMN_CLASSIFICATION,)

#: CSV do modelo: `;` e UTF-8 (o `-sig` aceita o BOM que o Excel grava). DECLARADOS,
#: nunca farejados (mesma regra do mapeamento de entrada da S14).
CHART_CSV_DELIMITER = ";"
CHART_CSV_ENCODING = "utf-8-sig"

#: Forma do código reduzido: letras e dígitos, com `.` e `-` no meio. Nada de `;`,
#: aspas nem espaço — o código vai CRU para o arquivo contábil da Sprint 13, e um
#: separador dentro dele quebraria a linha.
_CODE_PATTERN = re.compile(r"[0-9A-Za-z](?:[0-9A-Za-z.\-]*[0-9A-Za-z])?")

#: Motivos de linha inválida — vocabulário FECHADO (a resposta nunca traz a célula).
type ChartLineReason = Literal[
    "codigo_vazio",
    "codigo_longo",
    "codigo_invalido",
    "codigo_repetido",
    "nome_vazio",
    "nome_longo",
    "tipo_invalido",
    "classificacao_longa",
]

_INVALID_FILE_MESSAGE = (
    "Não foi possível ler a planilha do plano de contas. Envie um CSV (UTF-8, separado "
    "por ponto e vírgula) ou um XLSX, no modelo da plataforma."
)
_EMPTY_MESSAGE = (
    "A planilha não tem nenhuma conta. Importar um plano vazio inativaria o plano "
    "inteiro do cliente — confira o arquivo e envie de novo."
)
_HEADER_MESSAGE = (
    "O cabeçalho da planilha não segue o modelo do plano de contas: as colunas são "
    "codigo_reduzido, nome e tipo (obrigatórias) e classificacao (opcional)."
)
_LINES_MESSAGE = (
    "A planilha tem linhas inválidas. Corrija as linhas apontadas e envie de novo — "
    "nenhuma conta foi gravada."
)


@dataclass(frozen=True, slots=True)
class ChartSheetRow:
    """Uma conta VÁLIDA da planilha. O nome só existe em memória (e cifrado no banco)."""

    line: int
    code: str
    name: str
    account_type: AccountingAccountType
    classification: str | None

    def __repr__(self) -> str:
        # O nome é dado do cliente final: nunca num repr que acabe em log.
        return f"<ChartSheetRow line={self.line} code={self.code!r} type={self.account_type}>"


@dataclass(frozen=True, slots=True)
class ChartLineProblem:
    line: int
    reason: ChartLineReason


def _normalized_header(name: str) -> str:
    return name.strip().lower()


def check_header(columns: Sequence[str]) -> None:
    """O cabeçalho tem de ser o do modelo: as 3 obrigatórias, a opcional, e nada mais.

    Coluna desconhecida também recusa (e não é ignorada): `Nome ` digitado como
    `Nome da conta` sumiria em silêncio e a importação falharia adiante com
    `nome_vazio` em TODA linha — o motivo certo é o cabeçalho.

    ⚠️ `details` só NOMEIA coluna do nosso vocabulário (as constantes do modelo).
    Planilha enviada sem cabeçalho tem DADO na linha 1, e devolvê-la crua ecoava
    o nome da conta — que é do cliente final e nasce cifrado (§4.1/§4.5). O que
    veio da planilha e não é do modelo sai como CONTAGEM, nunca como texto: é
    seguro por construção, sem heurística de "parece cabeçalho" para revisar.
    """
    normalized = [_normalized_header(c) for c in columns]
    allowed = set(REQUIRED_COLUMNS) | set(OPTIONAL_COLUMNS)
    missing = [c for c in REQUIRED_COLUMNS if c not in normalized]
    unexpected = [c for c in columns if _normalized_header(c) not in allowed]
    repeated = sorted({c for c in normalized if normalized.count(c) > 1})
    if missing or unexpected or repeated:
        raise FileHeaderMismatchError(
            f"cabeçalho do plano contábil: faltam {len(missing)}, sobram {len(unexpected)}, "
            f"repetidas {len(repeated)}",
            user_message=_HEADER_MESSAGE,
            details={
                "missingColumns": missing,
                # Só a repetição de coluna DO MODELO é nomeada: `nome;nome` é o
                # erro que quem enviou conserta lendo o nome. Repetição de dado
                # (planilha sem cabeçalho) entra na contagem de inesperadas.
                "repeatedColumns": [c for c in repeated if c in allowed],
                "unexpectedColumnCount": len(unexpected),
                "foundColumnCount": len(columns),
                "expectedColumns": [*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS],
            },
        )


def _cell_text(value: Any) -> str | None:
    """Célula como texto aparado; número inteiro do XLSX (`649.0`) vira `649`."""
    if value is None:
        return None
    if isinstance(value, bool):
        text = str(value)
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value).strip()
    return text or None


def _fold(text: str) -> str:
    """Minúsculas e sem acento — só para casar o `tipo` (`Analítica` → `analitica`)."""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def parse_account_type(value: Any) -> AccountingAccountType | None:
    """`analitica`/`sintetica`, indiferente a caixa e acento. Fora disso, `None`."""
    text = _cell_text(value)
    if text is None:
        return None
    try:
        return AccountingAccountType(_fold(text))
    except ValueError:
        return None


def _validate_row(
    line: int, cells: dict[str, Any]
) -> tuple[ChartSheetRow | None, ChartLineReason | None]:
    code = _cell_text(cells.get(COLUMN_CODE))
    if code is None:
        return None, "codigo_vazio"
    if len(code) > MAX_ACCOUNTING_ACCOUNT_CODE_CHARS:
        # Recusado, nunca truncado: truncado casaria com um código-prefixo.
        return None, "codigo_longo"
    if not _CODE_PATTERN.fullmatch(code):
        return None, "codigo_invalido"
    name = _cell_text(cells.get(COLUMN_NAME))
    if name is None:
        return None, "nome_vazio"
    if len(name) > MAX_ACCOUNTING_ACCOUNT_NAME_CHARS:
        return None, "nome_longo"
    account_type = parse_account_type(cells.get(COLUMN_TYPE))
    if account_type is None:
        return None, "tipo_invalido"
    classification = _cell_text(cells.get(COLUMN_CLASSIFICATION))
    if (
        classification is not None
        and len(classification) > MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS
    ):
        return None, "classificacao_longa"
    return (
        ChartSheetRow(
            line=line,
            code=code,
            name=name,
            account_type=account_type,
            classification=classification,
        ),
        None,
    )


def validate_rows(
    columns: Sequence[str], rows: Sequence[tuple[int, Sequence[Any]]]
) -> tuple[list[ChartSheetRow], list[ChartLineProblem]]:
    """Valida TODAS as linhas, acumulando problemas — função PURA.

    Nunca para na primeira falha: a pessoa precisa da lista completa para corrigir
    de uma vez. Código repetido marca a SEGUNDA ocorrência em diante (a primeira é
    a que valeria); a comparação é exata (`1.01` e `1.010` são códigos distintos).
    """
    index = {_normalized_header(name): pos for pos, name in enumerate(columns)}
    valid: list[ChartSheetRow] = []
    problems: list[ChartLineProblem] = []
    seen_codes: set[str] = set()
    for line, values in rows:
        cells = {name: values[pos] if pos < len(values) else None for name, pos in index.items()}
        row, reason = _validate_row(line, cells)
        if reason is not None or row is None:
            problems.append(ChartLineProblem(line, reason or "codigo_vazio"))
            continue
        if row.code in seen_codes:
            problems.append(ChartLineProblem(line, "codigo_repetido"))
            continue
        seen_codes.add(row.code)
        valid.append(row)
    return valid, problems


def parse_chart_sheet(content: bytes) -> list[ChartSheetRow]:
    """Lê e valida a planilha INTEIRA; devolve as contas ou levanta a recusa tipada.

    Ordem das recusas (todas 422, nenhuma grava nada): formato pelo conteúdo
    (`FORMATO_NAO_SUPORTADO`) → arquivo que não abre/não itera (`ARQUIVO_INVALIDO`)
    → cabeçalho, ANTES da primeira linha (`CABECALHO_DIVERGENTE`) → linhas
    (`LINHAS_INVALIDAS`, `details.lines=[{line, reason}]` limitado + `total`) →
    planilha sem nenhuma conta (`ARQUIVO_INVALIDO`, `details.reason=sem_contas`).
    """
    # CSV ou XLSX pelo CONTEÚDO; PDF, XLS e o resto viram `FORMATO_NAO_SUPORTADO`.
    options = ReadOptions(
        file_format=detect_format(content),
        csv_delimiter=CHART_CSV_DELIMITER,
        encoding=CHART_CSV_ENCODING,
    )
    try:
        table = read_table(content, options, on_header=check_header)
    except FileInvalidError:
        # A mensagem do leitor fala do MAPEAMENTO da S14; aqui é o modelo do plano.
        raise FileInvalidError(
            "planilha do plano contábil ilegível", user_message=_INVALID_FILE_MESSAGE
        ) from None

    valid, problems = validate_rows(table.columns, table.rows)
    if problems:
        raise FileLinesInvalidError(
            f"planilha do plano contábil: {len(problems)} linha(s) inválida(s)",
            user_message=_LINES_MESSAGE,
            details={
                "lines": [
                    {"line": p.line, "reason": p.reason}
                    for p in problems[:MAX_INVALID_LINES_REPORTED]
                ],
                "total": len(problems),
            },
        )
    if not valid:
        raise FileInvalidError(
            "planilha do plano contábil sem nenhuma conta",
            user_message=_EMPTY_MESSAGE,
            details={"reason": "sem_contas"},
        )
    return valid
