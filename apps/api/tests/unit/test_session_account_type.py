"""Unit — mapeamento `tipo` Omie (+ tipo do arquivo) → `account_type` da sessão.

Regra (BACK 1.3 + aplicação + 86e3qyu04): código que a plataforma ENTENDE vem
do Omie e o arquivo não opina — `CR` (Cartão de Crédito) → 'credit_card';
`CA` (Conta Aplicação) → 'investment' (mini-fase conta aplicação, 27/06);
`CC` (Conta Corrente) → 'checking'. Código DESCONHECIDO (os outros 10 da doc
da Omie, `AC` Administradora de Cartões inclusive, e `None` = conta não
cacheada) deixa o tipo identificado no ARQUIVO decidir; sem arquivo que diga,
'checking'. ⚠️ Mapear `CA` para CARTÃO era o bug M-1 (auditoria 20/05/2026) —
segue proibido; `CA` é investimento.
"""

from __future__ import annotations

import pytest

from app.modules.reconciliations.schemas import ReconciliationStatementInput
from app.modules.reconciliations.service import (
    session_account_type_from_omie_tipo,
    statement_account_type_of,
)


@pytest.mark.parametrize(
    ("omie_tipo", "expected"),
    [
        ("CR", "credit_card"),  # Cartão de Crédito — único que vira cartão
        ("CC", "checking"),  # Conta Corrente
        ("CA", "investment"),  # Conta Aplicação (investimento) — NUNCA cartão (M-1)
        ("CX", "checking"),  # Caixinha
        ("PG", "checking"),  # qualquer outro código Omie
        ("AC", "checking"),  # Administradora de Cartões, sem arquivo que diga
        (None, "checking"),  # conta não cacheada
        ("", "checking"),  # string vazia (defensivo)
    ],
)
def test_session_account_type_from_omie_tipo(omie_tipo: str | None, expected: str) -> None:
    assert session_account_type_from_omie_tipo(omie_tipo) == expected


@pytest.mark.parametrize(
    ("omie_tipo", "statement_account_type", "expected"),
    [
        # Código conhecido: o Omie vence, o arquivo não opina.
        ("CR", "checking", "credit_card"),
        ("CA", "credit_card", "investment"),  # anti-M-1 continua valendo
        ("CC", "credit_card", "checking"),
        ("CC", "investment", "checking"),
        # Código desconhecido: o arquivo decide (86e3qyu04 — cartão Nubank em `AC`).
        ("AC", "credit_card", "credit_card"),
        ("AC", "checking", "checking"),
        ("AC", "investment", "investment"),
        ("PG", "credit_card", "credit_card"),
        ("CX", "credit_card", "credit_card"),
        (None, "credit_card", "credit_card"),
        ("", "credit_card", "credit_card"),
        # Desconhecido sem tipo válido do arquivo: default de sempre.
        ("AC", None, "checking"),
        ("AC", "poupanca", "checking"),
    ],
)
def test_session_account_type_with_statement(
    omie_tipo: str | None, statement_account_type: str | None, expected: str
) -> None:
    assert session_account_type_from_omie_tipo(omie_tipo, statement_account_type) == expected


def _statement(account_type: str) -> ReconciliationStatementInput:
    return ReconciliationStatementInput.model_validate(
        {
            "bank_name": "Nubank",
            "account_type": account_type,
            "period_start": "2026-08-04",
            "period_end": "2026-09-04",
            "opening_balance": "0",
            "closing_balance": "351.50",
            "transactions": [
                {"date": "2026-08-08", "description": "Apple.Com/Bill", "amount": "-45.90"}
            ],
        }
    )


def test_statement_account_type_of_single_type() -> None:
    parts = [_statement("credit_card"), _statement("credit_card")]
    assert statement_account_type_of(parts) == "credit_card"


def test_statement_account_type_of_divergent_parts_is_none() -> None:
    parts = [_statement("credit_card"), _statement("checking")]
    assert statement_account_type_of(parts) is None


def test_statement_account_type_of_empty_is_none() -> None:
    assert statement_account_type_of([]) is None
