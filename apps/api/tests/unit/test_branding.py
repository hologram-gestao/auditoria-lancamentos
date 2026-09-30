"""Marca num lugar só (86e3fr9x3): o nome do produto só é escrito em `core/branding.py`.

Quando o nome novo existir (86e3fr9wm), a troca no backend é uma linha. Este teste
recusa o nome escrito à mão em qualquer outro arquivo de `app/`.
"""

from __future__ import annotations

from pathlib import Path

from app.core import branding
from app.main import app

APP_DIR = Path(__file__).resolve().parents[2] / "app"
BRANDING_FILE = APP_DIR / "core" / "branding.py"


def test_name_is_unchanged_until_the_new_one_exists() -> None:
    assert branding.PRODUCT_NAME == "Auditoria de Lançamentos"
    assert app.title == branding.API_TITLE


def test_product_name_is_written_only_in_branding() -> None:
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in APP_DIR.rglob("*.py")
        if path != BRANDING_FILE and branding.PRODUCT_NAME in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
