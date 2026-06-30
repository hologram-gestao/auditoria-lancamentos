"""Unit — mapeamento `tipo` Omie → `account_type` da sessão (BACK 1.3 + aplicação).

Regra: `CR` (Cartão de Crédito) → 'credit_card'; `CA` (Conta Aplicação) →
'investment' (mini-fase conta aplicação, 27/06); qualquer outro tipo —
incluindo None — → 'checking'. ⚠️ Mapear `CA` para CARTÃO era o bug M-1
(auditoria 20/05/2026) — segue proibido; `CA` é investimento.
"""

from __future__ import annotations

import pytest

from app.modules.reconciliations.service import session_account_type_from_omie_tipo


@pytest.mark.parametrize(
    ("omie_tipo", "expected"),
    [
        ("CR", "credit_card"),  # Cartão de Crédito — único que vira cartão
        ("CC", "checking"),  # Conta Corrente
        ("CA", "investment"),  # Conta Aplicação (investimento) — NUNCA cartão (M-1)
        ("CX", "checking"),  # Caixinha
        ("PG", "checking"),  # qualquer outro código Omie
        (None, "checking"),  # conta não cacheada
        ("", "checking"),  # string vazia (defensivo)
    ],
)
def test_session_account_type_from_omie_tipo(omie_tipo: str | None, expected: str) -> None:
    assert session_account_type_from_omie_tipo(omie_tipo) == expected
