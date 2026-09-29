"""O contrato OpenAPI da Sprint 13 monta e expõe o que o front consome (BACK 13.2 e 13.4).

O `schema.ts` do front é regenerado a partir de `app.openapi()` (receita do ADR-045-FE):
se o documento não monta, ou se um campo do contrato some, a FRONT descobre tarde. Este
teste gera o documento de verdade, sem servidor nem banco.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.main import app as fastapi_app


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    document: dict[str, Any] = fastapi_app.openapi()
    return document


def _props(spec: dict[str, Any], schema: str) -> set[str]:
    return set(spec["components"]["schemas"][schema]["properties"])


class TestRotas:
    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/api/v1/export-layout-templates", "get"),
            ("/api/v1/export-layouts", "get"),
            ("/api/v1/export-layouts", "post"),
            ("/api/v1/export-layouts/from-template", "post"),
            ("/api/v1/export-layouts/{layout_id}", "get"),
            ("/api/v1/export-layouts/{layout_id}/versions", "post"),
            ("/api/v1/clients/{client_id}/accounting-files", "post"),
            ("/api/v1/clients/{client_id}/accounting-files", "get"),
            ("/api/v1/clients/{client_id}/accounting-files/{generation_id}/download", "get"),
        ],
    )
    def test_as_rotas_existem_com_resumo(
        self, spec: dict[str, Any], path: str, method: str
    ) -> None:
        operation = spec["paths"][path][method]
        assert operation["summary"]

    def test_listagem_de_geracoes_pagina_com_page_size(self, spec: dict[str, Any]) -> None:
        params = spec["paths"]["/api/v1/clients/{client_id}/accounting-files"]["get"]["parameters"]
        names = {p["name"] for p in params}
        assert {"pageSize", "page", "competence"} <= names


class TestCampos:
    def test_geracao_so_metadados(self, spec: dict[str, Any]) -> None:
        assert _props(spec, "AccountingFileGenerationItem") == {
            "id",
            "competence",
            "materializationId",
            "materializationVersion",
            "layoutId",
            "layoutName",
            "layoutVersion",
            "lines",
            "totalAmount",
            "sha256",
            "fileName",
            "author",
            "createdAt",
        }

    def test_pedido_de_geracao(self, spec: dict[str, Any]) -> None:
        assert _props(spec, "GenerateAccountingFileRequest") == {
            "layoutId",
            "competence",
            "materializationId",
        }

    def test_layout_e_versoes(self, spec: dict[str, Any]) -> None:
        assert {"id", "name", "targetSystem", "organizationId", "latestVersion", "versions"} <= (
            _props(spec, "ExportLayoutDetail")
        )
        assert _props(spec, "LayoutDefinitionPayload") == {
            "columns",
            "separator",
            "hasHeader",
            "encoding",
            "lineEnding",
            "dateFormat",
            "amountFormat",
        }
