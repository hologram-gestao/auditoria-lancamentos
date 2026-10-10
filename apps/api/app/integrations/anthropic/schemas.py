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
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    # BACK 02.3 — checksum de cartão: marca linhas de PAGAMENTO da fatura
    # anterior. O prompt manda extrair TODA movimentação visível, e numa fatura
    # o pagamento da fatura anterior aparece — então a IA o inclui. Não
    # excluímos (perderia o dado); MARCAMOS, para que o checksum do cartão some
    # tudo EXCETO os pagamentos e feche no total da fatura.
    # Default False: só faturas de cartão têm pagamento a marcar; conta corrente
    # e conta aplicação nunca marcam. Chave ausente no tool_use = False.
    is_payment: bool = Field(
        default=False,
        description=(
            "True apenas para linhas de PAGAMENTO da fatura anterior (cartão). "
            "Excluídas do checksum do cartão. Conta corrente/aplicação: sempre False."
        ),
    )

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


# Fonte única do enum de tipo de conta: o `ExtractedStatement`, o
# `DocumentIdentity` e as duas tools (`tools.py`) falam o mesmo vocabulário.
AccountType = Literal["checking", "credit_card", "investment"]
ACCOUNT_TYPES: tuple[str, ...] = get_args(AccountType)


class ExtractedStatement(BaseModel):
    """Resultado final da extração — payload do tool_use validado."""

    model_config = ConfigDict(strict=False)

    bank_name: str = Field(min_length=1)
    account_type: AccountType
    period_start: _date_type
    period_end: _date_type
    opening_balance: Decimal
    closing_balance: Decimal
    transactions: list[ExtractedTransaction] = Field(min_length=1)
    # 86e3n70p0 — vencimento da fatura de CARTÃO, como impresso (regra 15 do
    # prompt). É a proposta que a prévia mostra para o usuário CONFIRMAR: no
    # modo "vencimento da fatura" ele vira o centro da janela do Omie. `None`
    # quando o documento não mostra, e SEMPRE fora do cartão (ver validador).
    invoice_due_date: _date_type | None = Field(
        default=None, description="Vencimento da fatura do cartão (YYYY-MM-DD)."
    )

    @field_validator("opening_balance", "closing_balance", mode="before")
    @classmethod
    def _coerce_balances(cls, v: Any) -> Decimal:
        return _to_decimal(v)

    @model_validator(mode="after")
    def _due_date_only_for_card(self) -> ExtractedStatement:
        # Nada muda para conta corrente e aplicação: um vencimento que o modelo
        # emitisse ali por engano não chega à prévia nem à sessão.
        if self.account_type != "credit_card":
            self.invoice_due_date = None
        return self


class ExtractedStatementBlock(ExtractedStatement):
    """Resultado de UM bloco de um arquivo dividido (86e3ff8xd, D4).

    Um bloco de PDF pode não ter movimentação nenhuma (página só com totais,
    avisos ou rodapé), então aqui `transactions` aceita lista vazia. O arquivo
    INTEIRO continua exigindo pelo menos uma linha: `merge_statements` recusa o
    total zero com erro acionável. Só `AnthropicClient.extract_movements` em
    modo bloco (`part is not None`) valida com este modelo.
    """

    transactions: list[ExtractedTransaction] = Field(min_length=0)


class DocumentIdentity(BaseModel):
    """Identificação de um documento dividido em blocos (86e3ff8xd, D2).

    Vem de uma chamada curta só com a primeira página (`identify_document`) e
    entra como nota no user prompt de TODO bloco: página do meio não tem
    cabeçalho, e as regras de extração dependem do tipo de conta.

    86e3n70qf — numa fatura de CARTÃO, o total e o vencimento moram no
    cabeçalho (páginas 1 e 2), e a última página costuma ter só compras: o
    bloco final não vê o total. Por isso a identidade também traz, só para
    `credit_card`, o total a pagar (`closing_balance`) e o vencimento, que
    `merge_statements` usa no lugar dos blocos. Fora do cartão os dois são
    sempre `None` (ver validador): extrato tem saldo final no fim, e ali o
    último bloco continua valendo.
    """

    model_config = ConfigDict(strict=False)

    bank_name: str = Field(min_length=1)
    account_type: AccountType
    closing_balance: Decimal | None = Field(
        default=None, description="Total a pagar da fatura do cartão, como impresso."
    )
    invoice_due_date: _date_type | None = Field(
        default=None, description="Vencimento da fatura do cartão (YYYY-MM-DD)."
    )

    @field_validator("closing_balance", mode="before")
    @classmethod
    def _coerce_total(cls, v: Any) -> Decimal | None:
        if v is None:
            return None
        return _to_decimal(v)

    @model_validator(mode="after")
    def _invoice_fields_only_for_card(self) -> DocumentIdentity:
        # Nada muda para conta corrente e aplicação: um total ou vencimento que
        # o modelo emitisse ali por engano não chega à junção.
        if self.account_type != "credit_card":
            self.closing_balance = None
            self.invoice_due_date = None
        return self
