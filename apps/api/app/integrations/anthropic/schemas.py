"""DTOs Pydantic do parsing IA.

Estrutura espelha o `EXTRACT_MOVEMENTS_TOOL` (ver `tools.py`) — o modelo é
forçado a emitir exatamente este schema via `tool_choice`.

Decisões:
    - **`Decimal` em todos os valores monetários** (CLAUDE.md §3.4). NUNCA float.
    - **`date` como `datetime.date`** (parse estrito YYYY-MM-DD pelo Pydantic;
      datas em PT-BR como "31/03/2026" explodem aqui de propósito —
      o system prompt instrui a IA a usar ISO 8601, e queremos que falhe alto
      caso o modelo desobedeça).
    - **`from_attributes` desligado** — sempre validação a partir de dict
      vindo do tool_use (`message.content[i].input`).
"""

from __future__ import annotations

from datetime import date as _date_type
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _to_decimal(value: Any) -> Decimal:
    """Coerce qualquer numérico (int/float/str) em Decimal exato.

    Usa `str()` no path do float para evitar a representação binária inexata
    (ex: 0.1 → 0.10000000000000000555...). Decimal já é passado direto.
    """
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        # bool é subclasse de int — proteção defensiva contra type confusion
        raise TypeError("Booleano não é valor monetário válido.")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError(f"Valor monetário inválido: {value!r}") from exc
    raise TypeError(f"Tipo não suportado para Decimal: {type(value).__name__}")


class ExtractedTransaction(BaseModel):
    """Uma linha do extrato extraída pela IA.

    `amount` já vem com sinal aritmético (positivo = crédito, negativo = débito)
    — convenção forçada pelo system prompt + tool description. Saldos pós-linha
    (`balance`) podem vir nulos (faturas de cartão geralmente não trazem).
    """

    model_config = ConfigDict(strict=False)

    date: _date_type = Field(description="Data ISO 8601 (YYYY-MM-DD).")
    description: str = Field(min_length=1, description="Descrição preservada do documento.")
    amount: Decimal = Field(description="Valor com sinal: positivo = crédito, negativo = débito.")
    balance: Decimal | None = Field(default=None, description="Saldo após a transação.")

    @field_validator("amount", mode="before")
    @classmethod
    def _coerce_amount(cls, v: Any) -> Decimal:
        return _to_decimal(v)

    @field_validator("balance", mode="before")
    @classmethod
    def _coerce_balance(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        return _to_decimal(v)


class ExtractedStatement(BaseModel):
    """Resultado final da extração — payload do tool_use validado."""

    model_config = ConfigDict(strict=False)

    bank_name: str = Field(min_length=1)
    account_type: Literal["checking", "credit_card", "investment"]
    period_start: _date_type
    period_end: _date_type
    opening_balance: Decimal
    closing_balance: Decimal
    transactions: list[ExtractedTransaction] = Field(min_length=1)

    @field_validator("opening_balance", "closing_balance", mode="before")
    @classmethod
    def _coerce_balances(cls, v: Any) -> Decimal:
        return _to_decimal(v)
