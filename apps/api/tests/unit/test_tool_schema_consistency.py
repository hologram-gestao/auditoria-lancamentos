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
from app.integrations.anthropic.schemas import ExtractedStatement
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
