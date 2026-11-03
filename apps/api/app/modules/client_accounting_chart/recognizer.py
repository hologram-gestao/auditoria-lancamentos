"""Reconhecer as colunas do plano contábil pelo CONTEÚDO (86e3ydjyq, épico 86e3ydjy9).

Até aqui a importação perguntava "qual layout é este?" e reconhecia DOIS cabeçalhos
exatos (o modelo da plataforma e o export do Domínio); cada cliente com uma planilha
diferente virava um conserto pontual (Gabriel em 02/10, `.xls` e grafia em 09/10, 4S em
26/10, Tract em 03/11). Este módulo faz a pergunta certa, "qual coluna é o quê?", olhando
os VALORES, nunca o nome da coluna:

- inteiro preenchido em quase todas as linhas e sem repetição é o **código reduzido**;
- `1.1.2.01.004` (números separados por ponto) é a **classificação**;
- só `S`/`A` (ou `sintetica`/`analitica`) é o **tipo**; vazio é permitido quando a coluna
  nunca traz `A`, porque o Domínio só marca a sintética;
- inteiro igual à profundidade da classificação em todas as linhas é o **grau**;
- texto livre é o **nome**, com colunas de texto VIZINHAS que nunca estão preenchidas na
  mesma linha fundidas numa zona só (o nome recuado pela hierarquia do Domínio);
- `Devedora`/`Credora` (**natureza**) e CNPJ/CPF (**documento**) são reconhecidos e
  IGNORADOS, como qualquer coluna que a plataforma não usa: coluna a mais nunca recusa.

O nome da coluna só desempata dois candidatos ao mesmo papel; persistindo o empate, o
papel fica EM ABERTO, e quem decide é a pessoa (a etapa de mapeamento, 86e3ydjz4). Nada
aqui é IA nem heurística escondida: cada limiar é uma constante nomeada e testada.

Função PURA sobre as linhas cruas do leitor compartilhado; nada daqui grava nem loga.
Nenhum texto do arquivo sai deste módulo a não ser o CABEÇALHO de cada coluna, que só a
etapa de mapeamento expõe (exceção declarada, §7 Backend). O nome de conta é dado do
cliente final (§4.5): nunca num `repr`.

Só planilha (XLSX e XLS): o CSV continua só no modelo da plataforma, porque ler um CSV cru
pediria mexer no leitor compartilhado, e nenhum arquivo real chegou assim.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from app.db.models.client_accounting_account import AccountingAccountType
from app.modules.client_accounting_chart.dominio import DominioConversion, DominioLineProblem

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Papéis de coluna — vocabulário FECHADO. Os quatro primeiros mais o nome viram a linha
#: do modelo; natureza e documento são reconhecidos e ignorados (o documento é o insumo
#: do vínculo, 86e3ydjzy); `desconhecida` é tudo o mais, também ignorado.
type ColumnRole = Literal[
    "codigo", "classificacao", "tipo", "grau", "nome", "natureza", "documento", "desconhecida"
]
#: Os papéis que viram campo da conta (e que a pessoa pode escolher no mapeamento).
type AccountRole = Literal["codigo", "classificacao", "tipo", "grau", "nome"]
#: A forma que domina uma coluna, como a etapa de mapeamento a mostra.
type ColumnForm = Literal[
    "inteiro", "classificacao", "tipo_sa", "natureza", "documento", "texto", "vazia", "mista"
]
#: Sem estes três não há conta: papel ausente ou empatado recusa (ou pede mapeamento).
REQUIRED_ROLES: tuple[ColumnRole, ...] = ("codigo", "nome", "tipo")
#: Os papéis que viram campo da conta, na ordem em que a recusa e a tela os citam.
ACCOUNT_ROLES: tuple[ColumnRole, ...] = ("codigo", "classificacao", "tipo", "grau", "nome")

#: Fração mínima das linhas do bloco em que o código e o nome têm de estar preenchidos.
MIN_FILL_RATIO = 0.9
#: Fração mínima das células PREENCHIDAS de uma coluna que tem de casar uma forma para a
#: coluna ter essa forma (deixa espaço para as linhas com problema, 86e3ydjze).
MIN_FORM_RATIO = 0.95
#: Fração mínima de valores distintos para uma coluna de inteiros ser o código.
MIN_UNIQUE_RATIO = 0.95
#: Código reduzido em texto (`"649"`) conta como inteiro até este tamanho; mais que isso
#: tem cara de documento sem máscara.
MAX_TEXT_INTEGER_DIGITS = 9
#: Até onde, acima do bloco, a linha preenchida conta como cabeçalho (desempate e a
#: etapa de mapeamento): a linha logo acima, ou a de cima de uma linha em branco.
HEADER_SEARCH_GAP = 2
#: Um cabeçalho tem pelo menos duas colunas nomeadas; o banner tem uma célula por linha.
MIN_HEADER_CELLS = 2

_CLASSIFICATION = re.compile(r"\d+(?:\.\d+)*")
_DIGITS = re.compile(r"\d+")
_DOCUMENT_CHARS = re.compile(r"[\d.\-/\s]+")
_LETTER = re.compile(r"[^\W\d_]")
_HEADER_SEPARATORS = re.compile(r"[\s\-]+")
_HEADER_TRAILING_MARKS = re.compile(r"[*:]+$")

_SYNTHETIC_SPELLINGS = frozenset({"s", "sintetica", "sintetico"})
_ANALYTIC_SPELLINGS = frozenset({"a", "analitica", "analitico"})
_NATURE_SPELLINGS = frozenset({"devedora", "credora", "-", "\N{EM DASH}", "\N{EN DASH}"})

#: Os nomes de coluna que desempatam cada papel (grafia normalizada, nunca decisão sozinha).
_HEADER_HINTS: dict[ColumnRole, frozenset[str]] = {
    "codigo": frozenset({"codigo", "codigo_reduzido", "reduzido", "cod"}),
    "classificacao": frozenset({"classificacao", "class"}),
    "tipo": frozenset({"tipo", "t"}),
    "grau": frozenset({"grau", "nivel"}),
    "nome": frozenset({"nome", "nome_da_conta", "descricao", "conta"}),
}


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.strip().lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalized_header(value: Any) -> str | None:
    """A grafia normalizada de uma célula de cabeçalho (a mesma regra do modelo)."""
    if not isinstance(value, str):
        return None
    stripped = _HEADER_TRAILING_MARKS.sub("", value.strip()).strip()
    return _HEADER_SEPARATORS.sub("_", _fold(stripped)) or None


# ------------------------------------------------------------------ formas de célula


def as_integer(value: Any) -> int | None:
    """Inteiro de célula numérica (`649`, `649.0`) ou de texto só com dígitos (`"649"`)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if _DIGITS.fullmatch(text) and len(text) <= MAX_TEXT_INTEGER_DIGITS:
            return int(text)
    return None


def as_classification(value: Any) -> str | None:
    """A classificação como texto (`1.1.2.01.004`; o inteiro `1` também vale)."""
    if isinstance(value, str):
        text = value.strip()
        return text if _CLASSIFICATION.fullmatch(text) else None
    number = as_integer(value)
    return str(number) if number is not None else None


def type_mark(value: Any) -> AccountingAccountType | None:
    """`S`/`sintetica` → sintética, `A`/`analitica` → analítica; o resto, `None`."""
    if not isinstance(value, str):
        return None
    folded = _fold(value)
    if folded in _SYNTHETIC_SPELLINGS:
        return AccountingAccountType.SINTETICA
    if folded in _ANALYTIC_SPELLINGS:
        return AccountingAccountType.ANALITICA
    return None


def is_nature(value: Any) -> bool:
    return isinstance(value, str) and _fold(value) in _NATURE_SPELLINGS


def _document_digits(value: Any) -> int | None:
    """Quantos dígitos tem um valor com cara de documento, ou `None`."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return len(str(value))
    if isinstance(value, float) and value.is_integer():
        return len(str(int(value)))
    if isinstance(value, str) and _DOCUMENT_CHARS.fullmatch(value.strip()):
        return sum(ch.isdigit() for ch in value)
    return None


def is_document_like(value: Any) -> bool:
    """CNPJ/CPF (com ou sem máscara, numérico que perdeu os zeros) ou `0`."""
    digits = _document_digits(value)
    if digits is None:
        return False
    if as_integer(value) == 0:
        return True
    return 9 <= digits <= 14 and (not isinstance(value, str) or digits in (11, 14))


def is_text(value: Any) -> bool:
    """Texto livre: tem letra e não é tipo nem natureza."""
    return (
        isinstance(value, str)
        and _LETTER.search(value) is not None
        and type_mark(value) is None
        and not is_nature(value)
    )


def _filled(cells: Sequence[Any]) -> int:
    return sum(1 for cell in cells if cell is not None)


def _cell(cells: Sequence[Any], col: int) -> Any:
    return cells[col] if col < len(cells) else None


# ------------------------------------------------------------------ o bloco de contas


def _looks_like_account(cells: Sequence[Any]) -> bool:
    """Duas células ou mais, uma delas inteira ou com forma de classificação com ponto."""
    if _filled(cells) < 2:
        return False
    return any(
        cell is not None
        and not is_document_like(cell)
        and (as_integer(cell) is not None or "." in (as_classification(cell) or ""))
        for cell in cells
    )


def find_block(rows: Sequence[tuple[int, Sequence[Any]]]) -> list[int]:
    """As posições (em `rows`) das linhas do MAIOR bloco contíguo com cara de conta.

    Linha vazia dentro do bloco não o encerra; linha preenchida sem cara de conta, sim.
    Banner acima e rodapé abaixo ficam fora. Sem nenhuma linha com cara de conta, `[]`.
    """
    best: list[int] = []
    current: list[int] = []
    for position, (_, cells) in enumerate(rows):
        if _filled(cells) == 0:
            continue
        if _looks_like_account(cells):
            current.append(position)
            continue
        if len(current) > len(best):
            best = current
        current = []
    return current if len(current) > len(best) else best


# ------------------------------------------------------------------ perfil das colunas


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """O que uma coluna do bloco tem — só forma e contagem, nunca um valor de célula.

    `header` é o texto do cabeçalho acima do bloco (a exceção declarada da etapa de
    mapeamento); `suggested_role` é o papel que o reconhecimento deu a ela, ou `None`.
    """

    index: int
    form: ColumnForm
    fill_percent: int
    header: str | None
    suggested_role: ColumnRole | None


@dataclass(slots=True)
class _Stats:
    filled: int = 0
    integer: int = 0
    classification: int = 0
    dotted: int = 0
    type_marks: int = 0
    analytic_marks: int = 0
    nature: int = 0
    document: int = 0
    real_document: int = 0
    text: int = 0
    integers: list[int] = field(default_factory=list)


def _column_stats(block: Sequence[Sequence[Any]], col: int) -> _Stats:
    stats = _Stats()
    for cells in block:
        value = _cell(cells, col)
        if value is None:
            continue
        stats.filled += 1
        number = as_integer(value)
        document = is_document_like(value)
        if number is not None and not document:
            stats.integer += 1
            stats.integers.append(number)
        classification = as_classification(value)
        if classification is not None and not document:
            stats.classification += 1
            stats.dotted += "." in classification
        mark = type_mark(value)
        if mark is not None:
            stats.type_marks += 1
            stats.analytic_marks += mark is AccountingAccountType.ANALITICA
        stats.nature += is_nature(value)
        if document:
            stats.document += 1
            stats.real_document += as_integer(value) != 0
        stats.text += is_text(value)
    return stats


def _form(stats: _Stats) -> ColumnForm:
    if stats.filled == 0:
        return "vazia"

    def share(count: int) -> float:
        return count / stats.filled

    # A ordem é a de precedência: a classificação também casa "inteiro" no grupo de 1º
    # nível, e o documento sem máscara também casa "inteiro".
    if stats.dotted and share(stats.classification) >= MIN_FORM_RATIO:
        return "classificacao"
    if stats.real_document and share(stats.document) >= MIN_FORM_RATIO:
        return "documento"
    if share(stats.type_marks) >= MIN_FORM_RATIO:
        return "tipo_sa"
    if share(stats.nature) >= MIN_FORM_RATIO:
        return "natureza"
    if share(stats.integer) >= MIN_FORM_RATIO:
        return "inteiro"
    if share(stats.text) >= MIN_FORM_RATIO:
        return "texto"
    return "mista"


# ------------------------------------------------------------------ o reconhecimento


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """O que a PESSOA disse que é cada coluna (a etapa de mapeamento, 86e3ydjz4).

    `roles` usa os papéis de conta (`ACCOUNT_ROLES`), uma coluna por papel. Com
    `type_by_hierarchy`, não há coluna de tipo: a conta que tem outra abaixo é sintética
    (escolha EXPLÍCITA, nunca inferida em silêncio).
    """

    roles: Mapping[AccountRole, int]
    type_by_hierarchy: bool = False


@dataclass(frozen=True, slots=True)
class ColumnRecognition:
    """O que o reconhecimento decidiu sobre a planilha.

    `roles` liga cada papel resolvido às colunas dele (o nome pode ocupar várias colunas
    vizinhas); `missing` são os papéis obrigatórios sem candidato e `ambiguous` os papéis
    com dois candidatos que o cabeçalho não desempatou. `block` são os números de linha
    das contas; `header_line`, a linha do cabeçalho logo acima delas.
    """

    block: tuple[int, ...]
    header_line: int | None
    columns: tuple[ColumnProfile, ...]
    roles: Mapping[ColumnRole, tuple[int, ...]]
    missing: tuple[ColumnRole, ...]
    ambiguous: tuple[ColumnRole, ...]
    #: A coluna de tipo nunca traz `A`: a célula vazia é a conta analítica (o Domínio).
    empty_type_is_analytic: bool

    @property
    def resolved(self) -> bool:
        return not self.missing and not self.ambiguous

    @property
    def recognized_roles(self) -> list[ColumnRole]:
        """Os papéis de conta resolvidos, na ordem de `ACCOUNT_ROLES`."""
        return [role for role in ACCOUNT_ROLES if role in self.roles]

    @property
    def open_roles(self) -> list[ColumnRole]:
        """Os papéis que impedem a leitura: obrigatórios ausentes e empatados."""
        return [role for role in ACCOUNT_ROLES if role in self.missing or role in self.ambiguous]

    @property
    def ignored_columns(self) -> int:
        """Colunas com dado que não viram campo da conta (natureza, documento, o resto)."""
        used = {col for role in ACCOUNT_ROLES for col in self.roles.get(role, ())}
        return sum(1 for c in self.columns if c.form != "vazia" and c.index not in used)

    def __repr__(self) -> str:
        return (
            f"<ColumnRecognition block={len(self.block)} roles={sorted(self.roles)} "
            f"missing={list(self.missing)} ambiguous={list(self.ambiguous)}>"
        )


def _header_line(rows: Sequence[tuple[int, Sequence[Any]]], first_position: int) -> int | None:
    """A posição do cabeçalho logo acima do bloco, ou `None`.

    É a linha preenchida mais próxima acima do bloco (até `HEADER_SEARCH_GAP` linhas),
    e só conta se TEM CARA DE CABEÇALHO: duas células ou mais, todas texto. Uma linha de
    banner (razão social, CNPJ, título) tem uma célula só e nunca vira cabeçalho, porque
    o cabeçalho é o único texto do arquivo que a etapa de mapeamento mostra.
    """
    for position in range(first_position - 1, max(first_position - 1 - HEADER_SEARCH_GAP, -1), -1):
        cells = [cell for cell in rows[position][1] if cell is not None]
        if not cells:
            continue
        if len(cells) >= MIN_HEADER_CELLS and all(isinstance(cell, str) for cell in cells):
            return position
        return None
    return None


def _pick(
    role: ColumnRole,
    candidates: Sequence[tuple[int, ...]],
    headers: Mapping[int, str],
) -> tuple[tuple[int, ...] | None, bool]:
    """O candidato do papel e se ficou empatado. O cabeçalho só desempata."""
    if len(candidates) <= 1:
        return (candidates[0] if candidates else None), False
    hinted = [
        cols for cols in candidates if any(headers.get(c) in _HEADER_HINTS[role] for c in cols)
    ]
    if len(hinted) == 1:
        return hinted[0], False
    return None, True


def _name_zones(
    block: Sequence[Sequence[Any]], text_columns: list[int], empty_columns: set[int]
) -> list[tuple[int, ...]]:
    """Colunas de texto vizinhas que nunca dividem uma linha viram UMA zona de nome."""
    zones: list[list[int]] = []
    for col in text_columns:
        zone = zones[-1] if zones else None
        adjacent = zone is not None and all(c in empty_columns for c in range(zone[-1] + 1, col))
        if zone is not None and adjacent:
            shares_row = any(
                _cell(cells, col) is not None and any(_cell(cells, z) is not None for z in zone)
                for cells in block
            )
            if not shares_row:
                zone.append(col)
                continue
        zones.append([col])
    return [tuple(zone) for zone in zones]


def _zone_fill(block: Sequence[Sequence[Any]], zone: tuple[int, ...]) -> float:
    if not block:
        return 0.0
    filled = sum(1 for cells in block if any(is_text(_cell(cells, c)) for c in zone))
    return filled / len(block)


def _depth(classification: str) -> int:
    return classification.count(".") + 1


def recognize_columns(rows: Sequence[tuple[int, Sequence[Any]]]) -> ColumnRecognition | None:
    """Reconhece as colunas da planilha pelo conteúdo, ou `None` se não há bloco de contas.

    O bloco é percorrido duas vezes (perfil e grau), nunca mais: o custo é linear no
    tamanho que o leitor já limitou.
    """
    positions = find_block(rows)
    if not positions:
        return None
    block = [rows[p][1] for p in positions]
    width = max(len(cells) for cells in block)
    header_position = _header_line(rows, positions[0])
    header_cells = rows[header_position][1] if header_position is not None else []
    headers = {
        col: name
        for col in range(len(header_cells))
        if (name := normalized_header(header_cells[col])) is not None
    }

    stats = {col: _column_stats(block, col) for col in range(width)}
    forms = {col: _form(stats[col]) for col in range(width)}
    total = len(block)

    def fill(col: int) -> float:
        return stats[col].filled / total

    roles: dict[ColumnRole, tuple[int, ...]] = {}
    ambiguous: list[ColumnRole] = []

    def assign(role: ColumnRole, candidates: Sequence[tuple[int, ...]]) -> None:
        chosen, tie = _pick(role, candidates, headers)
        if chosen is not None:
            roles[role] = chosen
        elif tie:
            ambiguous.append(role)

    assign("classificacao", [(c,) for c in range(width) if forms[c] == "classificacao"])
    classification_col = roles.get("classificacao", (None,))[0]

    integer_cols = [c for c in range(width) if forms[c] == "inteiro"]
    grade_cols: list[tuple[int, ...]] = []
    if classification_col is not None:
        for col in integer_cols:
            pairs = [
                (as_integer(_cell(cells, col)), as_classification(_cell(cells, classification_col)))
                for cells in block
            ]
            both = [(g, k) for g, k in pairs if g is not None and k is not None]
            hits = sum(1 for g, k in both if g == _depth(k))
            if both and hits / len(both) >= MIN_FORM_RATIO:
                grade_cols.append((col,))
    assign("grau", grade_cols)
    grade = set(roles.get("grau", ()))

    code_candidates = [
        (c,)
        for c in integer_cols
        if c not in grade
        and fill(c) >= MIN_FILL_RATIO
        and len(set(stats[c].integers)) / max(stats[c].integer, 1) >= MIN_UNIQUE_RATIO
    ]
    assign("codigo", code_candidates)

    assign("tipo", [(c,) for c in range(width) if forms[c] == "tipo_sa"])
    assign("natureza", [(c,) for c in range(width) if forms[c] == "natureza"])
    assign("documento", [(c,) for c in range(width) if forms[c] == "documento"])

    empty = {c for c in range(width) if forms[c] == "vazia"}
    zones = [
        zone
        for zone in _name_zones(block, [c for c in range(width) if forms[c] == "texto"], empty)
        if _zone_fill(block, zone) >= MIN_FILL_RATIO
    ]
    assign("nome", zones)

    missing = tuple(r for r in REQUIRED_ROLES if r not in roles and r not in ambiguous)
    type_col = roles.get("tipo", (None,))[0]
    role_of = {col: role for role, cols in roles.items() for col in cols}
    columns = tuple(
        ColumnProfile(
            index=col,
            form=forms[col],
            fill_percent=round(100 * fill(col)),
            header=_header_text(header_cells, col),
            suggested_role=role_of.get(col),
        )
        for col in range(width)
    )
    return ColumnRecognition(
        block=tuple(rows[p][0] for p in positions),
        header_line=rows[header_position][0] if header_position is not None else None,
        columns=columns,
        roles=roles,
        missing=missing,
        ambiguous=tuple(ambiguous),
        empty_type_is_analytic=type_col is not None and stats[type_col].analytic_marks == 0,
    )


def _header_text(cells: Sequence[Any], col: int) -> str | None:
    value = _cell(cells, col)
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    return text or None


# ------------------------------------------------------------------ a conversão


def derive_type_by_hierarchy(
    classifications: Sequence[str | None],
) -> list[AccountingAccountType | None]:
    """Sintética é a conta que tem outra abaixo dela (`1.1` sobre `1.1.2`); senão, analítica.

    Só por ESCOLHA explícita da pessoa na etapa de mapeamento (86e3ydjz4): nada é
    inferido em silêncio (§4.8). Conta sem classificação fica sem tipo (`tipo_invalido`).
    """
    prefixes = {
        k[:pos] for k in classifications if k is not None for pos, ch in enumerate(k) if ch == "."
    }
    return [
        None
        if k is None
        else (AccountingAccountType.SINTETICA if k in prefixes else AccountingAccountType.ANALITICA)
        for k in classifications
    ]


def _code_text(value: Any) -> Any:
    number = as_integer(value)
    if number is not None and not isinstance(value, str):
        return str(number)
    return value


def convert_by_columns(
    rows: Sequence[tuple[int, Sequence[Any]]],
    recognition: ColumnRecognition,
    roles: Mapping[ColumnRole, tuple[int, ...]],
    *,
    type_by_hierarchy: bool = False,
) -> DominioConversion:
    """As linhas do bloco → `[código, nome, tipo, classificação]` do modelo, ou o motivo.

    `roles` é o reconhecimento (ou o mapeamento que a pessoa escolheu). As linhas seguem
    para `sheet.validate_rows`, que dá os motivos de campo (`codigo_vazio`,
    `tipo_invalido`…); aqui ficam só os motivos de LINHA, no vocabulário do export do
    Domínio: grau fora da profundidade (`grau_divergente`) ou ausente (`grau_ausente`),
    nome partido em duas células (`linha_irreconhecivel`) e conta fora do bloco
    (`conta_fora_do_bloco`: uma conta que a leitura descartaria calada).
    """
    code_col = roles["codigo"][0]
    name_cols = roles["nome"]
    type_col = roles["tipo"][0] if "tipo" in roles else None
    class_col = roles["classificacao"][0] if "classificacao" in roles else None
    grade_col = roles["grau"][0] if "grau" in roles else None
    in_block = set(recognition.block)

    converted: list[tuple[int, list[Any]]] = []
    problems: list[DominioLineProblem] = []
    pending: list[tuple[int, Any, Any, Any, Sequence[Any]]] = []
    for line, cells in rows:
        if line not in in_block:
            continue
        names = [_cell(cells, c) for c in name_cols if _cell(cells, c) is not None]
        if len(names) > 1:
            problems.append(DominioLineProblem(line, "linha_irreconhecivel"))
            continue
        raw_class = _cell(cells, class_col) if class_col is not None else None
        classification = as_classification(raw_class) if raw_class is not None else None
        if grade_col is not None and classification is not None:
            grade = as_integer(_cell(cells, grade_col))
            if grade is None:
                problems.append(DominioLineProblem(line, "grau_ausente"))
                continue
            if grade != _depth(classification):
                problems.append(DominioLineProblem(line, "grau_divergente"))
                continue
        # Classificação com forma estranha segue como texto: o validador do modelo só
        # confere o tamanho, como na planilha do modelo.
        class_value = classification if classification is not None else raw_class
        name = names[0] if names else None
        pending.append((line, _cell(cells, code_col), name, class_value, cells))

    if type_by_hierarchy:
        derived = derive_type_by_hierarchy([as_classification(k) for _, _, _, k, _ in pending])
        types = [t.value if t is not None else None for t in derived]
    else:
        empty_is_analytic = type_col is not None and not any(
            type_mark(_cell(cells, type_col)) is AccountingAccountType.ANALITICA
            for *_, cells in pending
        )
        types = [
            _type_value(_cell(cells, type_col), empty_is_analytic) if type_col is not None else None
            for *_, cells in pending
        ]
    for (line, code, name, classification, _), account_type in zip(pending, types, strict=True):
        text_name = name if name is None or isinstance(name, str) else _plain(name)
        converted.append((line, [_code_text(code), text_name, account_type, classification]))

    code_and_name = (code_col, name_cols)
    problems.extend(
        DominioLineProblem(line, "conta_fora_do_bloco")
        for line, cells in rows
        if line not in in_block
        and line != recognition.header_line
        and _outside_account(cells, code_and_name, class_col)
    )
    return DominioConversion(rows=converted, problems=problems)


def _plain(value: Any) -> str:
    """Nome que veio numérico (`2024`) vira texto, sem o `.0` do Excel."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _type_value(value: Any, empty_is_analytic: bool) -> str | None:
    if value is None:
        return AccountingAccountType.ANALITICA.value if empty_is_analytic else None
    mark = type_mark(value)
    # Marca estranha (`X`) segue como veio: o validador a recusa com `tipo_invalido`.
    return mark.value if mark is not None else str(value)


def _outside_account(
    cells: Sequence[Any], code_and_name: tuple[int, tuple[int, ...]], class_col: int | None
) -> bool:
    """Linha FORA do bloco com código inteiro e (classificação ou nome): é uma conta."""
    code_col, name_cols = code_and_name
    code = _cell(cells, code_col)
    if code is None or as_integer(code) is None or is_document_like(code):
        return False
    if class_col is not None:
        return as_classification(_cell(cells, class_col)) is not None
    return any(is_text(_cell(cells, c)) for c in name_cols)
