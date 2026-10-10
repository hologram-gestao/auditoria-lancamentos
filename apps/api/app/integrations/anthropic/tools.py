"""Definição do tool use `extract_movements` para a Anthropic API.

Princípio (Doc §12 + PLANO §S9): forçamos o modelo a emitir o schema exato via
`tool_choice = {"type": "tool", "name": "extract_movements"}`. A resposta fica
em `message.content[i].input` quando `type == "tool_use"`.

Mantemos o schema separado dos prompts para:
    - Reuso em testes (validação JSON Schema do shape esperado).
    - Marcar `cache_control: ephemeral` no bloco que carrega o schema (S9
      §1.4 do PLANO — prompt caching reduz custo após a 2ª chamada).
"""

from __future__ import annotations

from typing import Any

from app.integrations.anthropic.schemas import ACCOUNT_TYPES

EXTRACT_MOVEMENTS_TOOL_NAME = "extract_movements"
IDENTIFY_DOCUMENT_TOOL_NAME = "identify_document"

_ACCOUNT_TYPE_DESCRIPTION = (
    "checking = conta corrente / poupança; credit_card = fatura de "
    "cartão de crédito; investment = conta de aplicação / "
    "investimento (CDB, fundo, RDB, tesouro)."
)

# Schema imutável — exposto como dict pra ser passado direto ao SDK.
# `cache_control: ephemeral` (P1-008): tool definition é estável entre
# chamadas; marcando como cacheável a Anthropic reusa o tokenization do
# schema na janela de 5min (prompt caching). Reduz custo significativo no
# padrão de muitas conciliações em sequência. Ver PLANO §6.2 #2.
EXTRACT_MOVEMENTS_TOOL: dict[str, Any] = {
    "name": EXTRACT_MOVEMENTS_TOOL_NAME,
    "description": (
        "Extrai todos os lançamentos de um extrato bancário ou fatura de cartão "
        "em formato estruturado. Aplica o sinal aritmético no campo amount: "
        "créditos (entradas) são positivos, débitos (saídas) são negativos. "
        "Datas no formato ISO 8601 (YYYY-MM-DD). Preserva a descrição original. "
        "Não inventa transações; não filtra nenhuma linha."
    ),
    "cache_control": {"type": "ephemeral"},
    "input_schema": {
        "type": "object",
        "properties": {
            "bank_name": {
                "type": "string",
                "description": "Nome do banco/instituição identificado no documento.",
            },
            "account_type": {
                "type": "string",
                "enum": list(ACCOUNT_TYPES),
                "description": _ACCOUNT_TYPE_DESCRIPTION,
            },
            "period_start": {
                "type": "string",
                "format": "date",
                "description": "Data inicial do período coberto pelo documento (YYYY-MM-DD).",
            },
            "period_end": {
                "type": "string",
                "format": "date",
                "description": "Data final do período coberto pelo documento (YYYY-MM-DD).",
            },
            "opening_balance": {
                "type": "number",
                "description": "Saldo inicial conforme o documento (pode ser zero).",
            },
            "closing_balance": {
                "type": "number",
                "description": "Saldo final conforme o documento (pode ser zero).",
            },
            "invoice_due_date": {
                "type": "string",
                "format": "date",
                "description": (
                    "SÓ fatura de cartão: data de vencimento da fatura, como impressa "
                    "(YYYY-MM-DD). Nunca inventar: omita se o documento não mostrar, e "
                    "omita sempre em conta corrente e conta de aplicação."
                ),
            },
            "transactions": {
                "type": "array",
                "description": (
                    "Todas as movimentações na ordem em que aparecem. Em faturas de "
                    "cartão: cada parcela é uma linha (valor unitário + data da "
                    "parcela, sem agrupar); estornos com amount positivo; encargos "
                    "(juros/IOF/multa) como linhas separadas; o pagamento da fatura "
                    "anterior TAMBÉM é extraído, marcado com is_payment=true."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {
                            "type": "string",
                            "format": "date",
                            "description": "Data da movimentação (YYYY-MM-DD).",
                        },
                        "description": {
                            "type": "string",
                            "description": "Descrição preservada exatamente como no documento.",
                        },
                        "amount": {
                            "type": "number",
                            "description": (
                                "Valor com sinal aritmético: crédito positivo, débito negativo."
                            ),
                        },
                        "balance": {
                            "type": "number",
                            "description": (
                                "Saldo após a transação. Omita o campo "
                                "(NÃO use null) se o documento não fornecer."
                            ),
                        },
                        "is_payment": {
                            "type": "boolean",
                            "description": (
                                "True SOMENTE para linhas de PAGAMENTO da fatura "
                                "anterior em faturas de cartão (ex: 'PAGAMENTO "
                                "FATURA', 'PGTO EFETUADO', crédito que quita o "
                                "saldo anterior). Excluídas do checksum da fatura. "
                                "Para conta corrente, conta aplicação e para "
                                "qualquer compra/encargo/estorno, omita o campo "
                                "ou use false."
                            ),
                        },
                    },
                    "required": ["date", "description", "amount"],
                },
            },
        },
        "required": [
            "bank_name",
            "account_type",
            "period_start",
            "period_end",
            "opening_balance",
            "closing_balance",
            "transactions",
        ],
    },
}


# 86e3ff8xd (D2) — identificação de um PDF dividido em blocos de páginas. Só
# a primeira página vai nesta chamada; o resultado entra como nota no prompt de
# todo bloco. `max_tokens` pequeno no client: a resposta são dois campos (quatro
# numa fatura de cartão, 86e3n70qf).
IDENTIFY_DOCUMENT_TOOL: dict[str, Any] = {
    "name": IDENTIFY_DOCUMENT_TOOL_NAME,
    "description": (
        "Identifica o banco/instituição e o tipo de conta de um extrato bancário "
        "ou fatura de cartão a partir da primeira página do documento. Não "
        "extrai movimentações."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "bank_name": {
                "type": "string",
                "description": (
                    "Nome do banco/instituição identificado no documento. "
                    'Use "Desconhecido" se não conseguir identificar.'
                ),
            },
            "account_type": {
                "type": "string",
                "enum": list(ACCOUNT_TYPES),
                "description": _ACCOUNT_TYPE_DESCRIPTION,
            },
            # 86e3n70qf — o total da fatura mora no cabeçalho, não no fim do
            # documento: a junção dos blocos o toma daqui.
            "closing_balance": {
                "type": "number",
                "description": (
                    "SÓ fatura de cartão: o TOTAL A PAGAR desta fatura, como impresso "
                    '("Total a pagar", "Valor da fatura", "Total desta fatura"), '
                    "positivo. Nunca inventar nem calcular: omita se a página não "
                    "mostrar, e omita sempre em conta corrente e conta de aplicação. "
                    "Não confunda com limite, pagamento mínimo ou saldo anterior."
                ),
            },
            "invoice_due_date": {
                "type": "string",
                "format": "date",
                "description": (
                    "SÓ fatura de cartão: data de vencimento da fatura, como impressa "
                    "(YYYY-MM-DD). Nunca inventar: omita se a página não mostrar, e "
                    "omita sempre em conta corrente e conta de aplicação."
                ),
            },
        },
        "required": ["bank_name", "account_type"],
    },
}
