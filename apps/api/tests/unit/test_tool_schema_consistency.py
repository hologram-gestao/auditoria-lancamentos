"""A tool, o prompt e o schema Pydantic precisam concordar sobre `account_type`.

Regressão real: a tool declarava `enum: ["checking", "credit_card"]` enquanto o
prompt (regra 6) mandava a IA emitir `investment` para conta de aplicação e o
`ExtractedStatement` aceitava os três. O prompt pedia um valor que a tool
proibia — a IA ficava entre uma instrução e um schema que a contradizia.
"""

from __future__ import annotations

import typing
from typing import Any, cast

from app.integrations.anthropic.prompts import SYSTEM_PROMPT
from app.integrations.anthropic.schemas import ExtractedStatement, ExtractedTransaction
from app.integrations.anthropic.tools import EXTRACT_MOVEMENTS_TOOL


def _tool_account_type_enum() -> set[str]:
    schema = cast("dict[str, Any]", EXTRACT_MOVEMENTS_TOOL["input_schema"])
    return set(schema["properties"]["account_type"]["enum"])


def _pydantic_account_types() -> set[str]:
    annotation = ExtractedStatement.model_fields["account_type"].annotation
    return set(typing.get_args(annotation))


def test_tool_enum_matches_pydantic_literal() -> None:
    assert _tool_account_type_enum() == _pydantic_account_types()


def test_prompt_mentions_every_account_type_the_tool_allows() -> None:
    for value in _tool_account_type_enum():
        assert value in SYSTEM_PROMPT, f"`{value}` está na tool mas não é explicado no prompt"


def test_investment_is_supported_end_to_end() -> None:
    # Guarda específica do bug: conta aplicação existe desde a FASE 1.
    assert "investment" in _tool_account_type_enum()
    assert "investment" in _pydantic_account_types()


def test_invoice_due_date_esta_na_tool_no_prompt_e_no_schema() -> None:
    """86e3n70p0 — o vencimento da fatura: tool, regra do prompt e campo opcional."""
    schema = cast("dict[str, Any]", EXTRACT_MOVEMENTS_TOOL["input_schema"])
    prop = schema["properties"]["invoice_due_date"]
    assert prop["format"] == "date"
    assert "invoice_due_date" not in schema["required"]
    assert "Nunca inventar" in prop["description"]
    assert "`invoice_due_date`" in SYSTEM_PROMPT
    assert "Nunca invente" in SYSTEM_PROMPT
    assert ExtractedStatement.model_fields["invoice_due_date"].is_required() is False


def test_pagamento_do_resumo_da_fatura_nao_e_transacao_no_prompt_na_nota_e_na_tool() -> None:
    """86e3qyu04 — a fatura Nubank da DM Construções (15/10/2026) saiu com o
    pagamento DUAS vezes: a linha da lista de transações e o "Pagamento recebido"
    do bloco RESUMO (em outro bloco de páginas, com a data de emissão inventada).
    O checksum do cartão exclui `is_payment`, então nada acusou. A regra 12 e a
    descrição da tool passam a dizer que o pagamento só sai da lista de
    transações, com a data impressa nele. A NOTA DE BLOCO fica de fora de
    propósito: a versão que a mencionava fez o modelo devolver zero linhas em
    todos os blocos (5 de 6 rodadas reais), e este teste trava a ausência."""
    from app.integrations.anthropic.prompts import PART_NOTE_TEMPLATE

    assert "SOMENTE da lista de transações" in SYSTEM_PROMPT
    assert "RESUMO da fatura" in SYSTEM_PROMPT
    assert "nunca é data de pagamento" in SYSTEM_PROMPT
    assert "resumo" not in PART_NOTE_TEMPLATE.lower()
    assert "outro bloco" not in PART_NOTE_TEMPLATE
    schema = EXTRACT_MOVEMENTS_TOOL["input_schema"]
    prop = schema["properties"]["transactions"]["items"]["properties"]["is_payment"]
    assert "RESUMO da fatura" in prop["description"]


def test_encargos_do_resumo_da_fatura_nao_sao_transacao_no_prompt_e_na_tool() -> None:
    """86e3tpu7r — a fatura da Cora (13/10/2026) saía, em 8 de 10 rodadas, com uma
    linha a MAIS: o "IOF de compras internacionais" do RESUMO da página 2, que é
    totalizador, com o ano inventado. A regra 11 mandava emitir todo IOF como linha
    própria. Agora o encargo só vira linha quando está na LISTA de transações, e as
    linhas do resumo são totalizadores (regra 4). A regra não exige "data impressa
    ao lado da linha" (o Nubank agrupa por dia, e essa exigência esvaziou blocos em
    86e3qyu04), e a NOTA DE BLOCO segue sem falar de resumo."""
    from app.integrations.anthropic.prompts import PART_NOTE_TEMPLATE

    regra_11 = SYSTEM_PROMPT.split("11. **")[1].split("12. **")[0]
    assert "Encargos da LISTA de transações" in regra_11
    assert "bloco de RESUMO" in regra_11
    assert '"IOF de compras' in regra_11
    assert "totalizadores (regra 4)" in regra_11
    assert "não as extraia" in regra_11
    assert "agrupamento por dia" in regra_11
    assert "impressa ao lado" not in regra_11
    assert "resumo" not in PART_NOTE_TEMPLATE.lower()
    assert "outro bloco" not in PART_NOTE_TEMPLATE
    schema = EXTRACT_MOVEMENTS_TOOL["input_schema"]
    description = schema["properties"]["transactions"]["description"]
    assert "LISTA de transações" in description
    assert "RESUMO da fatura" in description
    assert "não são extraídas" in description


def test_parcela_esta_na_tool_no_prompt_e_no_schema() -> None:
    """86e3qhhtb — `installment_number`/`installment_total`: tool, regra 9 e DTO.

    Opcionais nos três (compra à vista omite), inteiros na tool e mencionados na
    regra 9, que é a das parcelas. A regra continua mandando preservar o texto
    `N/M` na descrição: o campo estruturado é o mesmo dado, não um substituto.
    """
    schema = cast("dict[str, Any]", EXTRACT_MOVEMENTS_TOOL["input_schema"])
    item = schema["properties"]["transactions"]["items"]
    for field in ("installment_number", "installment_total"):
        assert item["properties"][field]["type"] == "integer"
        assert field not in item["required"]
        assert f"`{field}`" in SYSTEM_PROMPT
        assert ExtractedTransaction.model_fields[field].is_required() is False
    regra_9 = SYSTEM_PROMPT.split("9. **Parcelas são linhas individuais.**")[1].split("10. **")[0]
    assert "installment_number" in regra_9
    assert "preserve esse texto na descrição" in regra_9
    assert "omita" in regra_9
