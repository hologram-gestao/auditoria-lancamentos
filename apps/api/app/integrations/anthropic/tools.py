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

EXTRACT_MOVEMENTS_TOOL_NAME = "extract_movements"

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
                "enum": ["checking", "credit_card"],
                "description": "checking = conta corrente; credit_card = cartão.",
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
            "transactions": {
                "type": "array",
                "description": (
                    "Todas as movimentações na ordem em que aparecem. Em faturas de "
                    "cartão: cada parcela é uma linha (valor unitário + data da "
                    "parcela, sem agrupar); estornos com amount positivo; encargos "
                    "(juros/IOF/multa) como linhas separadas; NÃO incluir o "
                    "pagamento da fatura."
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
