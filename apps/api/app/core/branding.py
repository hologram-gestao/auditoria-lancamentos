"""Marca do produto num lugar só (subtask 86e3fr9x3).

O nome novo ainda não foi decidido (86e3fr9wm). Até lá o valor é o de hoje, sem
mudança visível; quando o nome existir, a troca é aqui e em
`apps/web/src/lib/brand.ts`. `tests/unit/test_branding.py` recusa o nome antigo
escrito à mão fora deste arquivo.
"""

from __future__ import annotations

PRODUCT_NAME = "Auditoria de Lançamentos"
PRODUCT_TITLE = f"Sistema de {PRODUCT_NAME}"
API_TITLE = f"{PRODUCT_TITLE} — API"
