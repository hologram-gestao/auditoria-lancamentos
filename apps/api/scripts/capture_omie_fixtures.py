"""Captura fixtures REAIS da API Omie (BACK 02.7).

⚠️ **S-3 (ASSUMIDA — NÃO TESTADA / RISCO):** os nomes de campo da Omie hoje em
uso podem estar errados (já aconteceu em prod). Este script grava UMA chamada
real de cada endpoint para que `tests/unit/test_omie_fixtures.py` rode contra a
resposta REAL — não contra a documentação. **A Omie não tem sandbox**; exige
credencial de um cliente autorizado (ex.: Quial) + rede da Omie.

Uso (ver `tests/fixtures/omie/README.md`):

    export OMIE_CAPTURE_APP_KEY=...
    export OMIE_CAPTURE_APP_SECRET=...
    export OMIE_CAPTURE_CONTA_ID=...        # nCodCC com MUITOS movimentos
    export OMIE_CAPTURE_PERIODO_INICIAL=01/04/2026
    export OMIE_CAPTURE_PERIODO_FINAL=30/04/2026
    uv run python -m scripts.capture_omie_fixtures

Grava `<endpoint>.request.json` (SEM credenciais) e `<endpoint>.response.json`
em `tests/fixtures/omie/`. **Sanitize a PII dos VALORES antes de commitar**,
mantendo os NOMES DE CAMPO verbatim (o README explica).
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from app.core.config import get_settings
from app.integrations.omie.client import OmieClient, OmieCredentials

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "omie"

# (nome do arquivo, module, endpoint, call_name, param). O param NÃO contém
# credenciais — o OmieClient injeta app_key/app_secret internamente.
_CAPTURES: list[tuple[str, str, str, str, dict[str, Any]]] = []


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"Variável de ambiente {name} obrigatória. Ver tests/fixtures/omie/README.md."
        )
    return value


def _build_captures() -> None:
    conta_id = int(_require_env("OMIE_CAPTURE_CONTA_ID"))
    periodo_ini = _require_env("OMIE_CAPTURE_PERIODO_INICIAL")
    periodo_fim = _require_env("OMIE_CAPTURE_PERIODO_FINAL")
    _CAPTURES.extend(
        [
            (
                "listar_extrato",
                "financas",
                "extrato",
                "ListarExtrato",
                {
                    "nCodCC": conta_id,
                    "cVisualizar": "T",
                    "dPeriodoInicial": periodo_ini,
                    "dPeriodoFinal": periodo_fim,
                },
            ),
            (
                "listar_contas_correntes",
                "geral",
                "contacorrente",
                "ListarContasCorrentes",
                {"pagina": 1, "registros_por_pagina": 100, "apenas_importado_api": "N"},
            ),
            (
                "listar_contas_pagar",
                "financas",
                "contapagar",
                "ListarContasPagar",
                {"pagina": 1, "registros_por_pagina": 50},
            ),
            (
                "listar_contas_receber",
                "financas",
                "contareceber",
                "ListarContasReceber",
                {"pagina": 1, "registros_por_pagina": 50},
            ),
        ]
    )


async def _capture_one(
    client: OmieClient,
    *,
    name: str,
    module: str,
    endpoint: str,
    call_name: str,
    param: dict[str, Any],
) -> None:
    print(f"[capture] {call_name} ({module}/{endpoint}) ...")
    response = await client.call(module=module, endpoint=endpoint, call_name=call_name, param=param)
    # Request gravado SEM credenciais (o param já é livre de segredos).
    (_FIXTURES_DIR / f"{name}.request.json").write_text(
        json.dumps({"call_name": call_name, "param": param}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (_FIXTURES_DIR / f"{name}.response.json").write_text(
        json.dumps(response, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"[capture] {name}.response.json gravado ({len(json.dumps(response))} bytes).")


async def _main() -> None:
    _build_captures()
    settings = get_settings()
    credentials = OmieCredentials(
        app_key=SecretStr(_require_env("OMIE_CAPTURE_APP_KEY")),
        app_secret=SecretStr(_require_env("OMIE_CAPTURE_APP_SECRET")),
    )
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    async with OmieClient(credentials, settings) as client:
        for name, module, endpoint, call_name, param in _CAPTURES:
            await _capture_one(
                client,
                name=name,
                module=module,
                endpoint=endpoint,
                call_name=call_name,
                param=param,
            )
    print(
        "\n[capture] Concluído. ANONIMIZE os VALORES de PII (nomes/CNPJ) antes de "
        "commitar, mantendo os NOMES DE CAMPO verbatim. Ver README."
    )


if __name__ == "__main__":
    asyncio.run(_main())
