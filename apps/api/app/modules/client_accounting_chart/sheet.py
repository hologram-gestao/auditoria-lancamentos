"""Leitura da planilha do plano contábil — o MODELO DA PLATAFORMA (Sprint 16, BACK 16.1 — R1).

**O modelo** (decisão do planejador, ADR-086-BE; é o formato de `plano_contabil.csv`
da amostra anonimizada):

    - CSV UTF-8 (com ou sem BOM) separado por `;`, ou planilha do Excel, XLSX ou XLS
      (primeira aba);
    - cabeçalho na linha 1, com as colunas obrigatórias `codigo_reduzido`, `nome` e
      `tipo`, e a opcional `classificacao` — em qualquer ordem, sem outras colunas.
      O nome da coluna casa por GRAFIA NORMALIZADA (86e3n70p9): maiúsculas, acentos,
      espaços e hífens no lugar do `_`, e `*` ou `:` no fim não importam (`Código
      Reduzido`, `codigo reduzido` e `Classificação` valem). Sinônimo NÃO vale
      (`codigo` sozinho ou `conta` é coluna desconhecida): a regra é de grafia, não
      de vocabulário, para a recusa continuar previsível;
    - `tipo` ∈ {`analitica`, `sintetica`}, aceitando também `analitico`/`sintetico` e
      as iniciais `a`/`s` (maiúscula e acento indiferentes: `Analítica`, `S` valem);
    - uma linha por conta, sem código repetido, e pelo menos uma conta.

    Exemplo::

        codigo_reduzido;nome;tipo;classificacao
        649;Banco conta movimento;analitica;1.1.1.02.001
        662;Alugueis a receber - Inquilino D;analitica;1.1.2.01.004

**O export nativo do plano de contas do Domínio também é aceito**, no `.xlsx`
(86e3gkd7y) e no `.xls` que o Domínio grava (86e3n70p6), com a MESMA regra: o leitor
compartilhado entrega as duas como linhas cruas, e `parse_chart_sheet` reconhece o layout pelo cabeçalho
(`dominio.find_dominio_header`, nas primeiras linhas) e o CONVERTE para as linhas deste
modelo (`dominio.convert_dominio`), que passam pelo MESMO `validate_rows`. Sem a
assinatura do Domínio, o caminho do modelo é o de sempre, intocado. O CSV nativo do
Domínio não é aceito (não há amostra): cai na recusa de cabeçalho.

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
from typing import TYPE_CHECKING, Any, Literal, NoReturn

from app.core.exceptions import (
    ChartMappingRequiredError,
    FileHeaderMismatchError,
    FileInvalidError,
    FileLinesInvalidError,
    ValidationAppError,
)
from app.db.models.client_accounting_account import (
    MAX_ACCOUNTING_ACCOUNT_CLASSIFICATION_CHARS,
    MAX_ACCOUNTING_ACCOUNT_CODE_CHARS,
    MAX_ACCOUNTING_ACCOUNT_NAME_CHARS,
    AccountingAccountType,
)
from app.db.models.client_input_mapping import InputFileFormat
from app.modules.client_accounting_chart.dominio import (
    DOMINIO_HEADER_SEARCH_ROWS,
    DominioConversion,
    convert_dominio,
    find_dominio_header,
)
from app.modules.client_accounting_chart.recognizer import (
    REQUIRED_ROLES,
    ColumnMapping,
    ColumnRecognition,
    ColumnRole,
    as_integer,
    convert_by_columns,
    recognize_columns,
)
from app.modules.client_file_ingestion.reader import (
    MAX_INVALID_LINES_REPORTED,
    ReadOptions,
    detect_format,
    read_sheet_raw_rows,
    read_table,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

#: Colunas do modelo. Casadas pelo NOME normalizado (`_normalized_header`), nunca
#: pela posição.
COLUMN_CODE = "codigo_reduzido"
COLUMN_NAME = "nome"
COLUMN_TYPE = "tipo"
COLUMN_CLASSIFICATION = "classificacao"
REQUIRED_COLUMNS: tuple[str, ...] = (COLUMN_CODE, COLUMN_NAME, COLUMN_TYPE)
OPTIONAL_COLUMNS: tuple[str, ...] = (COLUMN_CLASSIFICATION,)
#: As colunas do modelo numa ordem fixa: é nela que o conversor do Domínio monta as
#: linhas que entram em `validate_rows`.
MODEL_COLUMNS: tuple[str, ...] = (COLUMN_CODE, COLUMN_NAME, COLUMN_TYPE, COLUMN_CLASSIFICATION)

#: De onde veio a planilha — vocabulário FECHADO, vai para a métrica da importação.
#: `reconhecido` é a planilha lida pelo CONTEÚDO das colunas (86e3ydjyq); `mapeado`, a
#: lida pelo mapeamento que a pessoa escolheu na tela (86e3ydjz4).
type ChartLayout = Literal["modelo", "dominio", "reconhecido", "mapeado"]

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
    "por ponto e vírgula), um XLSX ou um XLS, no modelo da plataforma ou como o Domínio "
    "exporta."
)
_EMPTY_MESSAGE = (
    "A planilha não tem nenhuma conta. Importar um plano vazio inativaria o plano "
    "inteiro do cliente — confira o arquivo e envie de novo."
)
_HEADER_MESSAGE = (
    "O cabeçalho da planilha não segue o modelo do plano de contas: as colunas são "
    "codigo_reduzido, nome e tipo (obrigatórias) e classificacao (opcional). Maiúsculas, "
    "acentos, espaços e hífens não importam; outros nomes não são aceitos."
)
#: Como a recusa nomeia cada papel (o vocabulário NOSSO, nunca um texto do arquivo).
ROLE_LABELS: dict[ColumnRole, str] = {
    "codigo": "código",
    "classificacao": "classificação",
    "tipo": "tipo (S ou A)",
    "grau": "grau",
    "nome": "nome",
    "natureza": "natureza",
    "documento": "CNPJ/CPF",
    "desconhecida": "outra",
}
_RECOGNITION_TAIL = (
    " Envie no modelo da plataforma (codigo_reduzido, nome, tipo e classificacao) ou com "
    "uma coluna de código, uma de nome e uma de tipo."
)
_LINES_MESSAGE = (
    "A planilha tem linhas inválidas. Corrija as linhas apontadas e envie de novo — "
    "nenhuma conta foi gravada."
)
#: Acima desta fração das linhas do bloco com problema, a leitura provavelmente está
#: errada (coluna trocada, layout que não é o que parece): o arquivo é recusado inteiro,
#: nunca importado pela metade (86e3ydjze).
MAX_PROBLEM_LINE_RATIO = 0.20
#: Motivos que recusam o arquivo inteiro mesmo abaixo do teto: código repetido é
#: ambiguidade de chave (qual das duas contas vale?), não uma linha a deixar de fora.
WHOLE_FILE_REASONS: frozenset[str] = frozenset({"codigo_repetido"})
_TOO_MANY_PROBLEMS_MESSAGE = (
    "Mais de 20% das linhas da planilha têm problema: o arquivo provavelmente não foi lido "
    "como você espera. Confira as linhas apontadas (ou o arquivo) — nenhuma conta foi gravada."
)
_REPEATED_CODE_MESSAGE = (
    "A planilha tem código reduzido repetido: não dá para saber qual das contas vale. "
    "Corrija as linhas apontadas e envie de novo — nenhuma conta foi gravada."
)
_DOMINIO_LINES_MESSAGE = (
    "O arquivo foi reconhecido como o plano de contas exportado do Domínio, mas há "
    "linhas que não puderam ser lidas com segurança. Confira as linhas apontadas (ou "
    "exporte o plano de novo) — nenhuma conta foi gravada."
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


#: O que a normalização do cabeçalho apaga: espaços e hífens INTERNOS viram `_`
#: (`Código Reduzido` → `codigo_reduzido`); `*` e `:` no FIM somem (`Nome*`, `Tipo:`,
#: marcas de "obrigatório" que planilha de gente traz).
_HEADER_SEPARATORS = re.compile(r"[\s\-]+")
_HEADER_TRAILING_MARKS = re.compile(r"[*:]+$")


def _normalized_header(name: str) -> str:
    """A grafia normalizada de um nome de coluna — só grafia, nunca sinônimo.

    Minúsculas sem acento (`_fold`), espaços e hífens internos como `_`, e `*` ou
    `:` finais removidos. É a MESMA função que o `validate_rows` usa para achar a
    coluna: o que `check_header` aceita, `validate_rows` encontra.
    """
    stripped = _HEADER_TRAILING_MARKS.sub("", name.strip()).strip()
    return _HEADER_SEPARATORS.sub("_", _fold(stripped))


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


def fold_text(text: str) -> str:
    """Minúsculas e sem acento — para casar o `tipo` (`Analítica` → `analitica`) e o
    nome de coluna (`Classificação` → `classificacao`). Pública: o leitor das relações
    de clientes e fornecedores (`parties.py`) usa a MESMA dobra, nunca uma cópia."""
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


_fold = fold_text


#: As grafias aceitas do `tipo`, já normalizadas por `_fold`: o nome do enum, a forma
#: no masculino e a inicial (86e3n70p9: "pode ser S/A?" — pode). Qualquer outra coisa
#: é `tipo_invalido`: `sint` não é abreviação aceita, de propósito.
_ACCOUNT_TYPE_SPELLINGS: dict[str, AccountingAccountType] = {
    "analitica": AccountingAccountType.ANALITICA,
    "analitico": AccountingAccountType.ANALITICA,
    "a": AccountingAccountType.ANALITICA,
    "sintetica": AccountingAccountType.SINTETICA,
    "sintetico": AccountingAccountType.SINTETICA,
    "s": AccountingAccountType.SINTETICA,
}


def parse_account_type(value: Any) -> AccountingAccountType | None:
    """`analitica`/`sintetica` (também `analitico`/`sintetico` e `a`/`s`), indiferente
    a caixa e acento. Fora disso, `None`."""
    text = _cell_text(value)
    if text is None:
        return None
    return _ACCOUNT_TYPE_SPELLINGS.get(_fold(text))


#: O começo de texto que o Excel lê como FÓRMULA ao abrir um CSV. A exportação do
#: plano (86e3qhhrm) prefixa o nome que começa assim com um apóstrofo, e a leitura da
#: planilha o tira de volta: exportar e reimportar o mesmo arquivo não muda o nome.
FORMULA_START = ("=", "+", "-", "@")
#: O prefixo que neutraliza a fórmula na exportação (e só ele é retirado na leitura).
FORMULA_GUARD = "'"


def neutralize_formula(text: str) -> str:
    """O nome como vai para o CSV exportado: com o apóstrofo se começaria fórmula."""
    return FORMULA_GUARD + text if text.startswith(FORMULA_START) else text


def restore_formula(text: str) -> str:
    """O inverso de `neutralize_formula`: tira o apóstrofo SÓ se ele guarda uma fórmula.

    O apóstrofo de um nome que não começaria fórmula (`'Caixa`) é do nome e fica.
    """
    if text.startswith(FORMULA_GUARD) and text[1:].startswith(FORMULA_START):
        return text[1:]
    return text


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
    if name is not None:
        name = restore_formula(name)
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


#: Os campos de UMA conta, como a inclusão e a edição manual (86e3nb816) os nomeiam.
type AccountField = Literal["code", "name", "type", "classification"]

#: Que campo cada motivo de linha aponta: a recusa manual cai no campo certo do
#: formulário, com o MESMO motivo que a planilha daria.
REASON_FIELD: dict[ChartLineReason, AccountField] = {
    "codigo_vazio": "code",
    "codigo_longo": "code",
    "codigo_invalido": "code",
    "codigo_repetido": "code",
    "nome_vazio": "name",
    "nome_longo": "name",
    "tipo_invalido": "type",
    "classificacao_longa": "classification",
}


def validate_account(
    *,
    code: str,
    name: str,
    account_type: AccountingAccountType,
    classification: str | None,
) -> tuple[ChartSheetRow | None, ChartLineReason | None]:
    """UMA conta pela MESMA regra de linha da planilha — a porta manual (86e3nb816).

    Função PURA: o plano tem duas portas de entrada (planilha e formulário) e uma
    regra só. Aparar espaço, recusar código com separador, nome vazio ou longo:
    tudo vem de `_validate_row`, nunca de uma segunda cópia.
    """
    return _validate_row(
        0,
        {
            COLUMN_CODE: code,
            COLUMN_NAME: name,
            COLUMN_TYPE: account_type.value,
            COLUMN_CLASSIFICATION: classification,
        },
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


@dataclass(frozen=True, slots=True)
class ParsedChartSheet:
    """As contas válidas e o layout reconhecido (`modelo`, `dominio` ou `reconhecido`).

    `recognition` só existe quando a planilha foi lida pelo conteúdo: é dele que saem
    as contagens do log (papéis reconhecidos e colunas ignoradas, nunca um texto).
    """

    rows: list[ChartSheetRow]
    layout: ChartLayout
    recognition: ColumnRecognition | None = None
    #: Só com o tipo derivado da hierarquia (escolha explícita no mapeamento): quantas
    #: contas ficaram sintéticas e quantas analíticas, para a prévia mostrar ANTES.
    derived_types: tuple[int, int] | None = None
    #: Só com `allow_problems` (86e3ydjze): as linhas com problema, FORA de `rows`, em
    #: ordem de linha, `(linha, motivo)` no vocabulário fechado. A importação só grava
    #: sem elas quando a pessoa confirmou exatamente esta lista.
    problems: tuple[tuple[int, str], ...] = ()
    #: Só quando o reconhecimento achou a coluna de CNPJ/CPF (86e3ydjzy): `(linha, código
    #: da conta, valor cru da célula)` das contas VÁLIDAS com algo nela. Em memória: é o
    #: insumo do vínculo por escolha, nunca vai para resposta nem log.
    documents: tuple[tuple[int, str, Any], ...] = ()

    @property
    def documents_found(self) -> int | None:
        """Quantas contas trazem um documento (fora o `0`), ou `None` sem a coluna."""
        if self.recognition is None or "documento" not in self.recognition.roles:
            return None
        return sum(1 for _, _, raw in self.documents if as_integer(raw) != 0)

    def __repr__(self) -> str:
        return f"<ParsedChartSheet rows={len(self.rows)} layout={self.layout}>"


def _raise_invalid_file() -> NoReturn:
    # A mensagem do leitor fala do MAPEAMENTO da S14; aqui é o modelo do plano.
    raise FileInvalidError(
        "planilha do plano contábil ilegível", user_message=_INVALID_FILE_MESSAGE
    ) from None


def _raise_lines(problems: Sequence[tuple[int, str]], *, user_message: str) -> NoReturn:
    ordered = sorted(problems)
    raise FileLinesInvalidError(
        f"planilha do plano contábil: {len(ordered)} linha(s) inválida(s)",
        user_message=user_message,
        details={
            "lines": [
                {"line": line, "reason": reason}
                for line, reason in ordered[:MAX_INVALID_LINES_REPORTED]
            ],
            "total": len(ordered),
        },
    )


_UNCONFIRMED_MESSAGE = (
    "As linhas com problema desta planilha não são as que você confirmou na revisão (o "
    "arquivo mudou, ou a revisão é de outro arquivo). Revise de novo — nenhuma conta foi "
    "gravada."
)


def refuse_unconfirmed_lines(
    problems: Sequence[tuple[int, str]],
    *,
    confirmed: Collection[int] | None,
    layout: ChartLayout = "modelo",
) -> None:
    """A importação só grava SEM as linhas com problema que a pessoa confirmou (86e3ydjze).

    Sem confirmação e com problema, a recusa de sempre (cliente da API anterior à prévia
    com linhas). Com confirmação, o conjunto confirmado tem de ser IGUAL ao calculado
    agora sobre o mesmo arquivo: qualquer diferença é a mesma recusa `LINHAS_INVALIDAS`,
    e nada é gravado. Perda nunca silenciosa.
    """
    computed = {line for line, _ in problems}
    if confirmed is None:
        if computed:
            message = _DOMINIO_LINES_MESSAGE if layout == "dominio" else _LINES_MESSAGE
            _raise_lines(problems, user_message=message)
        return
    if set(confirmed) != computed:
        _raise_lines(problems, user_message=_UNCONFIRMED_MESSAGE)


def _require_accounts(valid: list[ChartSheetRow]) -> list[ChartSheetRow]:
    if not valid:
        raise FileInvalidError(
            "planilha do plano contábil sem nenhuma conta",
            user_message=_EMPTY_MESSAGE,
            details={"reason": "sem_contas"},
        )
    return valid


def _dominio_header_line(content: bytes, file_format: InputFileFormat) -> int | None:
    """A linha do cabeçalho do Domínio, se a planilha tiver um nas primeiras linhas.

    Só planilha, XLSX ou XLS (o CSV nativo do Domínio não tem amostra). A espiada lê no máximo
    `DOMINIO_HEADER_SEARCH_ROWS` linhas, com os guardas do leitor.
    """
    if file_format is not InputFileFormat.XLSX:
        return None
    try:
        peek = read_sheet_raw_rows(content, limit=DOMINIO_HEADER_SEARCH_ROWS)
    except FileInvalidError:
        _raise_invalid_file()
    return find_dominio_header(peek)


def _raw_rows(content: bytes) -> list[tuple[int, list[Any]]]:
    try:
        return read_sheet_raw_rows(content)
    except FileInvalidError:
        _raise_invalid_file()


def _settle(
    valid: list[ChartSheetRow],
    problems: Sequence[tuple[int, str]],
    *,
    user_message: str,
    allow_problems: bool,
) -> tuple[list[ChartSheetRow], tuple[tuple[int, str], ...]]:
    """As contas válidas e as linhas com problema — ou a recusa do arquivo inteiro.

    Sem `allow_problems` (o comportamento de sempre), qualquer problema recusa. Com ele
    (86e3ydjze), as linhas com problema voltam para a prévia, salvo dois casos que
    continuam recusando tudo: código repetido (`WHOLE_FILE_REASONS`) e problemas acima de
    `MAX_PROBLEM_LINE_RATIO` das linhas lidas.
    """
    ordered = tuple(sorted(dict(problems).items()))
    if ordered:
        if not allow_problems:
            _raise_lines(ordered, user_message=user_message)
        if any(reason in WHOLE_FILE_REASONS for _, reason in ordered):
            _raise_lines(ordered, user_message=_REPEATED_CODE_MESSAGE)
        if len(ordered) > MAX_PROBLEM_LINE_RATIO * (len(valid) + len(ordered)):
            _raise_lines(ordered, user_message=_TOO_MANY_PROBLEMS_MESSAGE)
    return _require_accounts(valid), ordered


def _validated(
    conversion: DominioConversion, *, user_message: str, allow_problems: bool
) -> tuple[list[ChartSheetRow], tuple[tuple[int, str], ...]]:
    """As linhas convertidas pelo MESMO `validate_rows` do modelo, e os problemas."""
    valid, problems = validate_rows(MODEL_COLUMNS, conversion.rows)
    all_problems: list[tuple[int, str]] = [(p.line, p.reason) for p in problems]
    all_problems += [(p.line, p.reason) for p in conversion.problems]
    return _settle(valid, all_problems, user_message=user_message, allow_problems=allow_problems)


def _parse_dominio(content: bytes, header_line: int, *, allow_problems: bool) -> ParsedChartSheet:
    conversion = convert_dominio(_raw_rows(content), header_line)
    rows, problems = _validated(
        conversion, user_message=_DOMINIO_LINES_MESSAGE, allow_problems=allow_problems
    )
    return ParsedChartSheet(rows=rows, layout="dominio", problems=problems)


def _labels(roles: Sequence[ColumnRole]) -> str:
    labels = [ROLE_LABELS[role] for role in roles]
    if len(labels) <= 1:
        return "".join(labels)
    return ", ".join(labels[:-1]) + " e " + labels[-1]


def recognition_message(recognition: ColumnRecognition) -> str:
    """A frase da recusa a partir dos PAPÉIS — nenhum texto do arquivo entra nela."""
    parts: list[str] = []
    recognized = recognition.recognized_roles
    if recognized:
        noun = "a coluna" if len(recognized) == 1 else "as colunas"
        parts.append(f"Reconhecemos {noun} de {_labels(recognized)}")
    missing = [r for r in recognition.open_roles if r in recognition.missing]
    if missing:
        noun = "a coluna" if len(missing) == 1 else "as colunas"
        parts.append(f"não encontramos {noun} de {_labels(missing)}")
    ambiguous = [r for r in recognition.open_roles if r in recognition.ambiguous]
    if ambiguous:
        parts.append(f"há mais de uma coluna que pode ser {_labels(ambiguous)}")
    sentence = "; ".join(parts)
    return sentence[:1].upper() + sentence[1:] + "."


def recognition_details(recognition: ColumnRecognition | None) -> dict[str, Any]:
    """Os papéis reconhecidos e os em aberto — vocabulário NOSSO, nunca o arquivo."""
    if recognition is None:
        return {
            "recognizedRoles": [],
            "missingRoles": list(REQUIRED_ROLES),
            "ambiguousRoles": [],
        }
    return {
        "recognizedRoles": recognition.recognized_roles,
        "missingRoles": recognition.open_roles,
        "ambiguousRoles": [r for r in recognition.open_roles if r in recognition.ambiguous],
    }


_MAPPING_TAIL = " Diga o que é cada coluna para continuar."
_MAPPING_SHAPE_MESSAGE = (
    "O mapeamento das colunas não cabe nesta planilha. Escolha de novo o que é cada coluna."
)


def mapping_details(recognition: ColumnRecognition) -> dict[str, Any]:
    """O perfil das colunas para a etapa de mapeamento — a exceção DECLARADA (86e3ydjz4).

    `cabecalho` é o texto do cabeçalho acima do bloco (a pessoa precisa dele para
    escolher); NENHUM valor de célula do bloco sai: forma e preenchimento são contagens.
    """
    # O nome recuado do Domínio ocupa várias colunas vizinhas (uma zona): a etapa sugere
    # só a primeira, e escolher QUALQUER coluna da zona como nome lê a zona inteira
    # (`_mapped`), já que a tela dá uma coluna por papel.
    zone = recognition.roles.get("nome", ())
    return {
        **recognition_details(recognition),
        "colunas": [
            {
                "indice": column.index,
                "cabecalho": column.header,
                "formaDetectada": column.form,
                "preenchimento": column.fill_percent,
                "papelSugerido": (
                    None
                    if column.suggested_role == "nome" and column.index != zone[0]
                    else column.suggested_role
                ),
            }
            for column in recognition.columns
        ],
    }


def _recognized(
    content: bytes,
    file_format: InputFileFormat,
    refusal: FileHeaderMismatchError,
    mapping: ColumnMapping | None,
    *,
    allow_problems: bool,
) -> ParsedChartSheet:
    """O terceiro caminho: a planilha lida pelo CONTEÚDO (86e3ydjyq) ou pelo MAPEAMENTO
    que a pessoa escolheu (86e3ydjz4).

    Sem bloco de contas, a recusa de cabeçalho do modelo, como sempre, com os papéis em
    `details`. Com bloco e sem código, nome ou tipo resolvidos, 422
    `MAPEAMENTO_NECESSARIO` com o perfil das colunas: a tela pergunta, nunca beco sem
    saída. Com o mapeamento, o reconhecimento só acha o bloco e a pessoa decide os papéis.
    """
    if file_format is not InputFileFormat.XLSX:
        if mapping is not None:
            raise ValidationAppError("mapeamento do plano em arquivo CSV") from None
        raise _with_roles(refusal, None) from None
    raw = _raw_rows(content)
    recognition = recognize_columns(raw)
    # Sem bloco, ou um bloco sem nenhuma coluna de texto (onde estaria o nome?), não há
    # o que mapear: é a recusa de cabeçalho, nunca uma pergunta sem resposta.
    if recognition is None or not any(c.form == "texto" for c in recognition.columns):
        raise _with_roles(refusal, None) from None
    if mapping is not None:
        return _mapped(raw, recognition, mapping, allow_problems=allow_problems)
    if not recognition.resolved:
        raise ChartMappingRequiredError(
            f"plano contábil: mapeamento necessário ({len(recognition.open_roles)} papéis)",
            user_message=recognition_message(recognition) + _MAPPING_TAIL,
            details=mapping_details(recognition),
        ) from None
    conversion = convert_by_columns(raw, recognition, recognition.roles)
    rows, problems = _validated(
        conversion, user_message=_LINES_MESSAGE, allow_problems=allow_problems
    )
    return ParsedChartSheet(
        rows=rows,
        layout="reconhecido",
        recognition=recognition,
        problems=problems,
        documents=_documents(raw, recognition, rows),
    )


def _mapped(
    raw: list[tuple[int, list[Any]]],
    recognition: ColumnRecognition,
    mapping: ColumnMapping,
    *,
    allow_problems: bool,
) -> ParsedChartSheet:
    width = len(recognition.columns)
    if any(col >= width for col in mapping.roles.values()):
        raise ValidationAppError(
            "mapeamento do plano fora das colunas", user_message=_MAPPING_SHAPE_MESSAGE
        )
    roles: dict[ColumnRole, tuple[int, ...]] = {role: (col,) for role, col in mapping.roles.items()}
    zone = recognition.roles.get("nome", ())
    if "nome" in roles and roles["nome"][0] in zone:
        roles["nome"] = zone
    conversion = convert_by_columns(
        raw, recognition, roles, type_by_hierarchy=mapping.type_by_hierarchy
    )
    rows, problems = _validated(
        conversion, user_message=_LINES_MESSAGE, allow_problems=allow_problems
    )
    derived: tuple[int, int] | None = None
    if mapping.type_by_hierarchy:
        synthetic = sum(r.account_type is AccountingAccountType.SINTETICA for r in rows)
        derived = (synthetic, len(rows) - synthetic)
    return ParsedChartSheet(
        rows=rows,
        layout="mapeado",
        recognition=recognition,
        derived_types=derived,
        problems=problems,
        documents=_documents(raw, recognition, rows),
    )


def _documents(
    raw: list[tuple[int, list[Any]]], recognition: ColumnRecognition, rows: list[ChartSheetRow]
) -> tuple[tuple[int, str, Any], ...]:
    """O valor da coluna de CNPJ/CPF de cada conta válida que tem algo nela (86e3ydjzy)."""
    columns = recognition.roles.get("documento")
    if not columns:
        return ()
    col = columns[0]
    cells = dict(raw)
    found: list[tuple[int, str, Any]] = []
    for row in rows:
        line_cells = cells.get(row.line, [])
        value = line_cells[col] if col < len(line_cells) else None
        if value is not None:
            found.append((row.line, row.code, value))
    return tuple(found)


def _with_roles(
    refusal: FileHeaderMismatchError,
    recognition: ColumnRecognition | None,
    *,
    user_message: str | None = None,
) -> FileHeaderMismatchError:
    return FileHeaderMismatchError(
        refusal.message,
        user_message=user_message or refusal.user_message,
        details={**(refusal.details or {}), **recognition_details(recognition)},
    )


def parse_chart_sheet(
    content: bytes, *, mapping: ColumnMapping | None = None, allow_problems: bool = False
) -> ParsedChartSheet:
    """Lê e valida a planilha INTEIRA; devolve as contas ou levanta a recusa tipada.

    Ordem das recusas (todas 422, nenhuma grava nada): formato pelo conteúdo
    (`FORMATO_NAO_SUPORTADO`) → arquivo que não abre/não itera (`ARQUIVO_INVALIDO`)
    → cabeçalho, ANTES da primeira linha (`CABECALHO_DIVERGENTE`) → linhas
    (`LINHAS_INVALIDAS`, `details.lines=[{line, reason}]` limitado + `total`) →
    planilha sem nenhuma conta (`ARQUIVO_INVALIDO`, `details.reason=sem_contas`).

    Layout, em três caminhos: o cabeçalho exato do Domínio nas primeiras linhas vai
    pelo conversor (`dominio.py`); o cabeçalho exato do modelo, pelo modelo; o resto,
    pelo RECONHECEDOR por conteúdo (`recognizer.py`, 86e3ydjyq). Bloco de contas que o
    reconhecedor não decide é 422 `MAPEAMENTO_NECESSARIO` (86e3ydjz4), e com `mapping` (a
    escolha da pessoa) o bloco é lido pelos papéis que ela deu. Sem bloco de contas, a
    recusa de cabeçalho com os papéis reconhecidos e os que faltam.

    `allow_problems` (86e3ydjze): as linhas com problema voltam em `problems`, fora de
    `rows`, em vez de recusar, salvo código repetido e problemas acima de
    `MAX_PROBLEM_LINE_RATIO`. Sem ele, qualquer linha com problema recusa como sempre.
    """
    # CSV ou planilha (XLSX ou XLS) pelo CONTEÚDO; PDF, HTML e o resto viram
    # `FORMATO_NAO_SUPORTADO`.
    file_format = detect_format(content)
    if mapping is not None:
        # A escolha da pessoa vale sobre qualquer reconhecimento: o arquivo que pediu
        # mapeamento é lido pelo mapeamento, nunca por um caminho que ela não viu.
        refusal = FileHeaderMismatchError(
            "plano contábil lido pelo mapeamento", user_message=_HEADER_MESSAGE
        )
        return _recognized(content, file_format, refusal, mapping, allow_problems=allow_problems)
    header_line = _dominio_header_line(content, file_format)
    if header_line is not None:
        return _parse_dominio(content, header_line, allow_problems=allow_problems)

    options = ReadOptions(
        file_format=file_format,
        csv_delimiter=CHART_CSV_DELIMITER,
        encoding=CHART_CSV_ENCODING,
    )
    try:
        table = read_table(content, options, on_header=check_header)
    except FileHeaderMismatchError as refusal:
        return _recognized(content, file_format, refusal, mapping, allow_problems=allow_problems)
    except FileInvalidError:
        _raise_invalid_file()

    valid, problems = validate_rows(table.columns, table.rows)
    rows, kept = _settle(
        valid,
        [(p.line, p.reason) for p in problems],
        user_message=_LINES_MESSAGE,
        allow_problems=allow_problems,
    )
    return ParsedChartSheet(rows=rows, layout="modelo", problems=kept)
