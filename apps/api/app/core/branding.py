"""Marca do produto num lugar só (subtask 86e3fr9x3).

O produto se chama Hologram OS (decisão de 30/09/2026); a troca de nome é aqui e em
`apps/web/src/lib/brand.ts`. `tests/unit/test_branding.py` recusa o nome antigo em
qualquer arquivo de `app/` e o nome novo escrito à mão fora deste.
"""

from __future__ import annotations

PRODUCT_NAME = "Hologram OS"
PRODUCT_TITLE = PRODUCT_NAME
API_TITLE = f"{PRODUCT_TITLE} API"
