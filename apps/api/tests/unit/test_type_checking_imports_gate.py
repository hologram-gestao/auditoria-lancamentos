"""Gate de CI: símbolo importado sob `TYPE_CHECKING` não pode ser CHAMADO (S11, QA 11.8).

Este gate nasce de um defeito real da Sprint 11, e existe para que ele não volte.

**O que aconteceu.** `app/modules/client_titles/service.py` construía
`TitlesSummary(...)` em runtime, mas o símbolo só era importado dentro de
`if TYPE_CHECKING:`. Em produção isso é `NameError` — `GET /titles/summary` e
`POST /titles/sync` respondiam **500**, duas das três rotas da sprint inteiras.

**Por que nenhuma camada pegou.** `mypy --strict` aprova **por definição**: o
bloco `TYPE_CHECKING` existe justamente para que o type checker enxergue o que o
runtime não importa. `ruff` não distingue uso em anotação de uso como chamável.
E os 1.141 testes unitários da época montavam o dataclass DIRETO, com import
correto no topo do arquivo de teste — provavam o valor de retorno, nunca a
função que o produz. O defeito só apareceu na suíte de integração, que exige
Postgres e por isso **não roda na máquina de todo mundo**.

Daí a forma deste gate: ele é AST puro, roda em qualquer máquina, em
milissegundos, e reprova a classe inteira do erro em vez de um caso.

**A regra.** No bloco `if TYPE_CHECKING:` entra só o que aparece
EXCLUSIVAMENTE depois de `:` ou `->` (anotação, `Annotated[...]`, genérico).
Símbolo usado como **chamável** — construtor, função, decorator ou classe-base —
é import de RUNTIME, no topo do módulo.

⚠️ O gate é deliberadamente estreito: ele só acusa as quatro posições em que o
nome é AVALIADO de verdade (chamada, decorator, classe-base, `raise`). Não tenta
adivinhar anotação em string nem `cast("X", ...)` — os dois são resolvidos pelo
type checker e nunca tocam o runtime, e é por isso que continuam legítimos.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[2]

#: As árvores que viram código RODANDO em produção. `scripts/` entra junto de
#: propósito: `scripts/sync_client_titles.py` é o comando do Cloud Run Job
#: diário, e um `NameError` ali é uma sincronização que falha de madrugada, sem
#: ninguém olhando.
_SOURCE_TREES = ("app", "scripts")


def _python_files() -> list[Path]:
    files: list[Path] = []
    for tree in _SOURCE_TREES:
        files.extend(sorted((_API_ROOT / tree).rglob("*.py")))
    return files


def _is_type_checking_test(node: ast.expr) -> bool:
    """`if TYPE_CHECKING:` ou `if typing.TYPE_CHECKING:` — as duas formas."""
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _bound_names(stmt: ast.Import | ast.ImportFrom) -> set[str]:
    """Os nomes que um import LIGA no escopo do módulo.

    `import a.b.c` liga `a`; `from x import y as z` liga `z`.
    """
    names: set[str] = set()
    for alias in stmt.names:
        if alias.asname:
            names.add(alias.asname)
        elif isinstance(stmt, ast.Import):
            names.add(alias.name.split(".")[0])
        else:
            names.add(alias.name)
    return names


def _type_checking_imports(tree: ast.Module) -> set[str]:
    """Nomes importados dentro de um `if TYPE_CHECKING:` de nível de módulo."""
    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.If) or not _is_type_checking_test(node.test):
            continue
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Import | ast.ImportFrom):
                names |= _bound_names(stmt)
    return names


def _runtime_imports(tree: ast.Module) -> set[str]:
    """Nomes importados em RUNTIME, em qualquer lugar do módulo.

    Inclui import dentro de função (o truque legítimo para ciclo de import): se
    o nome é reimportado antes de ser usado, não há `NameError`. Sem isto o gate
    acusaria um padrão correto.
    """
    names: set[str] = set()
    type_checking_blocks = [
        node for node in tree.body if isinstance(node, ast.If) and _is_type_checking_test(node.test)
    ]
    inside_type_checking = {id(stmt) for block in type_checking_blocks for stmt in ast.walk(block)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import | ast.ImportFrom) and id(node) not in inside_type_checking:
            names |= _bound_names(node)
    return names


def _root_name(node: ast.expr) -> str | None:
    """A raiz de `a`, `a.b` ou `a.b.c` — o nome que precisa existir em runtime."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _evaluated_positions(tree: ast.Module) -> list[tuple[str, int, str]]:
    """Onde um nome é AVALIADO de verdade: (nome, linha, que posição é).

    As quatro posições em que `TYPE_CHECKING` vira `NameError` em produção.
    Anotação NÃO entra aqui — é exatamente o uso legítimo.
    """
    found: list[tuple[str, int, str]] = []

    def record(expr: ast.expr, kind: str) -> None:
        name = _root_name(expr)
        if name is not None:
            found.append((name, expr.lineno, kind))

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            record(node.func, "chamada")
        elif isinstance(node, ast.ClassDef):
            for base in node.bases:
                record(base, "classe-base")
            for deco in node.decorator_list:
                record(deco, "decorator")
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for deco in node.decorator_list:
                record(deco, "decorator")
        elif isinstance(node, ast.Raise) and node.exc is not None:
            record(node.exc, "raise")

    return found


def _offenders(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    type_only = _type_checking_imports(tree)
    if not type_only:
        return []
    # Um nome reimportado em runtime (mesmo dentro de função) está seguro.
    suspects = type_only - _runtime_imports(tree)
    if not suspects:
        return []

    # `relative_to` só vale para arquivo DENTRO da árvore varrida; os casos
    # sintéticos do auto-teste moram em `tmp_path` e cairiam com `ValueError`.
    relative = path.relative_to(_API_ROOT) if path.is_relative_to(_API_ROOT) else path.name
    return [
        f"{relative}:{lineno} — `{name}` é importado só sob `TYPE_CHECKING` e aparece como {kind}"
        for name, lineno, kind in _evaluated_positions(tree)
        if name in suspects
    ]


class TestNenhumSimboloDeTypeCheckingEChamadoEmRuntime:
    """A classe inteira do defeito, varrida em `app/` e `scripts/`."""

    def test_o_bloco_type_checking_so_tem_nome_de_anotacao(self) -> None:
        problemas = [linha for path in _python_files() for linha in _offenders(path)]

        assert not problemas, (
            "Símbolo importado sob `if TYPE_CHECKING:` está sendo AVALIADO em "
            "runtime — isso é `NameError` em produção, e nem mypy nem ruff pegam.\n"
            "Mova o import para o topo do módulo (runtime); no bloco "
            "`TYPE_CHECKING` fica só o que aparece depois de `:` ou `->`.\n\n"
            + "\n".join(problemas)
        )


class TestOGateRealmenteDetecta:
    """O gate precisa FALHAR no código defeituoso — senão é decoração.

    Sem estes casos, um `_offenders` que sempre devolvesse `[]` passaria no teste
    acima e daria a mesma sensação de segurança que os 1.141 unitários deram
    enquanto duas rotas respondiam 500.
    """

    @staticmethod
    def _escrever(tmp_path: Path, codigo: str) -> Path:
        alvo = tmp_path / "modulo.py"
        alvo.write_text(codigo, encoding="utf-8")
        return alvo

    def test_pega_o_defeito_exato_da_sprint_11(self, tmp_path: Path) -> None:
        """O construtor chamado com o import sob `TYPE_CHECKING`."""
        alvo = self._escrever(
            tmp_path,
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from x import TitlesSummary\n"
            "def f():\n"
            "    return TitlesSummary(1)\n",
        )
        problemas = _offenders(alvo)
        assert len(problemas) == 1
        assert "TitlesSummary" in problemas[0]
        assert "chamada" in problemas[0]

    def test_pega_decorator_e_classe_base(self, tmp_path: Path) -> None:
        alvo = self._escrever(
            tmp_path,
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from x import Base, deco\n"
            "@deco\n"
            "class C(Base):\n"
            "    pass\n",
        )
        tipos = " ".join(_offenders(alvo))
        assert "decorator" in tipos
        assert "classe-base" in tipos

    def test_anotacao_pura_e_legitima_e_nao_acusa(self, tmp_path: Path) -> None:
        """O uso para o qual o `TYPE_CHECKING` existe."""
        alvo = self._escrever(
            tmp_path,
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from x import Repo\n"
            "def f(repo: 'Repo') -> 'Repo':\n"
            "    return repo\n",
        )
        assert _offenders(alvo) == []

    def test_reimport_em_runtime_dentro_da_funcao_nao_acusa(self, tmp_path: Path) -> None:
        """O padrão legítimo para quebrar ciclo de import."""
        alvo = self._escrever(
            tmp_path,
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    from x import Thing\n"
            "def f():\n"
            "    from x import Thing\n"
            "    return Thing()\n",
        )
        assert _offenders(alvo) == []


@pytest.mark.parametrize("tree", _SOURCE_TREES)
def test_a_arvore_varrida_existe_e_nao_esta_vazia(tree: str) -> None:
    """Se `app/` ou `scripts/` sumir do lugar, o gate vira no-op silencioso."""
    arquivos = list((_API_ROOT / tree).rglob("*.py"))
    assert arquivos, f"nenhum .py em {tree}/ — o gate estaria varrendo o vazio"
