"""Marca num lugar só (86e3fr9x3): o produto é o Hologram OS desde 30/09/2026.

O nome antigo não pode voltar em arquivo nenhum de `app/`, e o nome novo só é escrito
em `core/branding.py`. Os literais antigos estão escritos AQUI, e não derivados da
constante: derivados, o teste passaria a procurar o nome novo e deixaria o antigo
voltar calado.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core import branding
from app.main import app

APP_DIR = Path(__file__).resolve().parents[2] / "app"
BRANDING_FILE = APP_DIR / "core" / "branding.py"

OLD_NAME = "Auditoria de Lançamentos"
# A sigla antiga dentro de uma string Python. Comentário e docstring podem citá-la (é
# como o time chamava o sistema), e identificador com hífen (`ADL-PARSE-LIMIT`) fica.
# O prefixo do `cCodIntLanc` ("ADL", em `omie_posting/keys.py`) é a exceção nomeada:
# trocar mudaria a chave de dedup dos lançamentos já feitos no Omie (CLAUDE.md §3.16).
OLD_ACRONYM_IN_STRING = re.compile(r"""["'][^"'\n]*\bADL\b(?!-)[^"'\n]*["']""")
ACRONYM_ALLOWED_LINES = {'COD_INT_LANC_PREFIX = "ADL"'}


def _code_lines(path: Path) -> list[str]:
    """Linhas de código: sem comentário e sem o miolo de docstring."""
    lines: list[str] = []
    in_doc = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        quotes = line.count('"""') + line.count("'''")
        if in_doc:
            if quotes % 2 == 1:
                in_doc = False
            continue
        if line.startswith(('"""', "'''")):
            in_doc = quotes % 2 == 1
            continue
        if line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _app_files() -> list[Path]:
    return [path for path in APP_DIR.rglob("*.py") if path != BRANDING_FILE]


def test_product_is_hologram_os() -> None:
    assert branding.PRODUCT_NAME == "Hologram OS"
    assert branding.PRODUCT_TITLE == "Hologram OS"
    assert branding.API_TITLE == "Hologram OS API"
    assert app.title == branding.API_TITLE


def test_old_name_is_gone_from_app() -> None:
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in _app_files()
        if OLD_NAME in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_old_acronym_is_not_in_strings() -> None:
    offenders = [
        f"{path.relative_to(APP_DIR)}: {line}"
        for path in _app_files()
        for line in _code_lines(path)
        if OLD_ACRONYM_IN_STRING.search(line) and line not in ACRONYM_ALLOWED_LINES
    ]
    assert offenders == []


def test_new_name_is_written_only_in_branding() -> None:
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in _app_files()
        if any(branding.PRODUCT_NAME in line for line in _code_lines(path))
    ]
    assert offenders == []
