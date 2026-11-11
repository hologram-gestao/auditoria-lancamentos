"""As três tools da Anthropic cabem no modo estrito (`strict: true`, 86e3wg8et).

O claude-sonnet-5-5 recusa `tool_choice` forçado; quem garante argumentos no
schema passou a ser o `strict`. A API recusa com 400 schema estrito fora das
regras ("Strict tool use" / "JSON Schema limitations" da doc de structured
outputs, conferida em 11/11/2026): todo objeto com `additionalProperties: false`,
sem restrição numérica nem de tamanho de texto, `minItems` só 0 ou 1, formato de
texto da lista suportada, no máximo 24 parâmetros opcionais e 16 com tipo união
somando todas as tools estritas do pedido. Este teste quebra antes da API.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from app.integrations.anthropic.tools import EXTRACT_MOVEMENTS_TOOL, IDENTIFY_DOCUMENT_TOOL
from app.modules.reconciliations.qualification.semantic import _QUALIFY_TOOL

TOOLS = [EXTRACT_MOVEMENTS_TOOL, IDENTIFY_DOCUMENT_TOOL, _QUALIFY_TOOL]

_UNSUPPORTED_KEYWORDS = {
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "minLength",
    "maxLength",
    "maxItems",
}
_SUPPORTED_FORMATS = {
    "date-time",
    "time",
    "date",
    "duration",
    "email",
    "hostname",
    "uri",
    "ipv4",
    "ipv6",
    "uuid",
}


def _objects(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if schema.get("type") == "object":
        yield schema
        for child in schema.get("properties", {}).values():
            yield from _objects(child)
    if schema.get("type") == "array":
        yield from _objects(schema["items"])


def _nodes(schema: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield schema
    for child in schema.get("properties", {}).values():
        yield from _nodes(child)
    if "items" in schema:
        yield from _nodes(schema["items"])


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t["name"])
def test_tool_e_estrita_e_todo_objeto_fecha_propriedades(tool: dict[str, Any]) -> None:
    assert tool["strict"] is True
    for obj in _objects(tool["input_schema"]):
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) <= set(obj["properties"])


@pytest.mark.parametrize("tool", TOOLS, ids=lambda t: t["name"])
def test_tool_so_usa_o_que_o_modo_estrito_aceita(tool: dict[str, Any]) -> None:
    for node in _nodes(tool["input_schema"]):
        assert not (_UNSUPPORTED_KEYWORDS & set(node)), node
        assert node.get("minItems", 0) in (0, 1)
        if "format" in node:
            assert node["format"] in _SUPPORTED_FORMATS
        assert not isinstance(node.get("type"), list), "tipo união conta no teto de 16"


def test_opcionais_cabem_no_teto_por_pedido() -> None:
    # A extração e a identificação vão em pedidos separados; somar as três é o
    # pior caso, e ainda assim cabe nos 24 opcionais.
    optional = sum(
        len(set(obj["properties"]) - set(obj["required"]))
        for tool in TOOLS
        for obj in _objects(tool["input_schema"])
    )
    assert optional <= 24
