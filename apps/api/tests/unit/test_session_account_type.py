"""Unit — mapeamento `tipo` Omie → `account_type` da sessão (BACK 1.3).

Regra cravada (Risco #1 da FASE 1): só `CR` (Cartão de Crédito) → 'credit_card';
qualquer outro tipo — incluindo `CA` (Conta Aplicação) e None — → 'checking'.
Mapear `CA` para cartão era o bug M-1 (auditoria 20/05/2026).
"""

from __future__ import annotations

import pytest

from app.modules.reconciliations.service import session_account_type_from_omie_tipo


@pytest.mark.parametrize(
    ("omie_tipo", "expected"),
    [
        ("CR", "credit_card"),  # Cartão de Crédito — único que vira cartão
        ("CC", "checking"),  # Conta Corrente
        ("CA", "checking"),  # Conta Aplicação (investimento) — NÃO é cartão (M-1)
        ("CX", "checking"),  # Caixinha
        ("PG", "checking"),  # qualquer outro código Omie
        (None, "checking"),  # conta não cacheada
        ("", "checking"),  # string vazia (defensivo)
    ],
)
def test_session_account_type_from_omie_tipo(omie_tipo: str | None, expected: str) -> None:
    assert session_account_type_from_omie_tipo(omie_tipo) == expected
