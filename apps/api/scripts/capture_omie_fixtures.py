"""Captura fixtures REAIS da API Omie (BACK 02.7 · leitura · BACK 07.1 · escrita).

⚠️ **S-3 (ASSUMIDA — NÃO TESTADA / RISCO):** os nomes de campo da Omie hoje em
uso podem estar errados (já aconteceu em prod). Este script grava UMA chamada
real de cada endpoint para que `tests/unit/test_omie_fixtures.py` rode contra a
resposta REAL — não contra a documentação. **A Omie não tem sandbox**; exige
credencial de um cliente autorizado (ex.: Quial) + rede da Omie.

⚠️ **S-1 (ASSUMIDA — NÃO TESTADA / RISCO) — captura de ESCRITA:** o contrato do
`IncluirLancCC` (nomes de campo, convenção de sinal `nValorLanc` absoluto +
`cNatureza`, e unicidade de `cCodIntLanc`) é **suposição documentada**, não fato
observado. A captura de escrita existe para fechar essa lacuna.

**Escrita CRIA MOVIMENTO FINANCEIRO REAL** na contabilidade de um cliente — a
Omie não tem sandbox (CLAUDE.md §10). Por isso é **opt-in explícito**
(`OMIE_CAPTURE_ALLOW_WRITE=1`) e nunca roda por acidente. O lançamento criado
precisa ser **excluído manualmente no Omie** depois (ver README das fixtures).

Uso — só leitura (ver `tests/fixtures/omie/README.md`):

    export OMIE_CAPTURE_APP_KEY=...
    export OMIE_CAPTURE_APP_SECRET=...
    export OMIE_CAPTURE_CONTA_ID=...        # nCodCC com MUITOS movimentos
    export OMIE_CAPTURE_PERIODO_INICIAL=01/04/2026
    export OMIE_CAPTURE_PERIODO_FINAL=30/04/2026
    uv run python -m scripts.capture_omie_fixtures

Uso — leitura + ESCRITA (opt-in; cria lançamento real):

    export OMIE_CAPTURE_ALLOW_WRITE=1
    export OMIE_CAPTURE_COD_CATEG=...       # cCodCateg válido no cliente
    export OMIE_CAPTURE_COD_INT_LANC=...    # chave de integração (<=20 chars)
    export OMIE_CAPTURE_DATA_LANC=01/04/2026        # opcional; default = PERIODO_INICIAL
    export OMIE_CAPTURE_VALOR_LANC=0.01             # opcional; default 0.01
    uv run python -m scripts.capture_omie_fixtures

Grava `<endpoint>.request.json` (SEM credenciais) e `<endpoint>.response.json`
em `tests/fixtures/omie/`. **Sanitize a PII dos VALORES antes de commitar**,
mantendo os NOMES DE CAMPO verbatim (o README explica).
"""

from __future__ import annotations

import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from app.core.config import get_settings
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.integrations.omie.schemas import IncluirLancCCRequest

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "omie"

# (nome do arquivo, module, endpoint, call_name, param). O param NÃO contém
# credenciais — o OmieClient injeta app_key/app_secret internamente.
_Capture = tuple[str, str, str, str, dict[str, Any]]

# Serviço Omie do lançamento em conta corrente (escrita).
_LANC_CC_MODULE = "financas"
_LANC_CC_ENDPOINT = "contacorrentelancamentos"
_LANC_CC_CALL = "IncluirLancCC"

# Valor default da captura de escrita: o menor possível, porque o lançamento é
# REAL na contabilidade do cliente. Não é "seguro" — só menos danoso.
_DEFAULT_VALOR_LANC = "0.01"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(
            f"Variável de ambiente {name} obrigatória. Ver tests/fixtures/omie/README.md."
        )
    return value


def _build_captures() -> list[_Capture]:
    conta_id = int(_require_env("OMIE_CAPTURE_CONTA_ID"))
    periodo_ini = _require_env("OMIE_CAPTURE_PERIODO_INICIAL")
    periodo_fim = _require_env("OMIE_CAPTURE_PERIODO_FINAL")
    return [
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
        # BACK 07.3 — o combobox de classificação depende deste contrato, e os
        # nomes de campo do `CategoriaOmie` vieram da doc, não de uma resposta.
        (
            "listar_categorias",
            "geral",
            "categorias",
            "ListarCategorias",
            {"pagina": 1, "registros_por_pagina": 50},
        ),
    ]


def _write_json(name: str, payload: object) -> None:
    path = _FIXTURES_DIR / f"{name}.json"
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"[capture] {path.name} gravado.")


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
    _write_json(f"{name}.request", {"call_name": call_name, "param": param})
    _write_json(f"{name}.response", response)


# ----------------------------------------------------------------------
# Captura de ESCRITA — opt-in explícito (BACK 07.1)
# ----------------------------------------------------------------------


def _write_capture_enabled() -> bool:
    """`OMIE_CAPTURE_ALLOW_WRITE=1` é o ÚNICO caminho para a captura de escrita.

    Aceita apenas valores afirmativos explícitos. Qualquer outra coisa (ausente,
    vazio, `0`, `false`) significa NÃO — a captura de escrita cria movimento
    financeiro real e não pode depender de um default permissivo.
    """
    raw = os.environ.get("OMIE_CAPTURE_ALLOW_WRITE", "").strip().lower()
    return raw in {"1", "true", "yes"}


def _build_incluir_lanc_cc_request() -> IncluirLancCCRequest:
    """Monta o request de captura a partir do DTO — não de um dict solto.

    Construir a partir do `IncluirLancCCRequest` é o que fecha o laço do gate:
    a fixture gravada carrega exatamente as chaves que o DTO emite, e o teste
    compara as duas. Um dict escrito à mão aqui permitiria que o DTO e a
    chamada real divergissem sem ninguém notar.
    """
    cod_int_lanc = _require_env("OMIE_CAPTURE_COD_INT_LANC")
    if len(cod_int_lanc) > 20:
        raise SystemExit(
            f"OMIE_CAPTURE_COD_INT_LANC tem {len(cod_int_lanc)} chars; "
            "o campo cCodIntLanc é string20."
        )
    data_lanc = os.environ.get("OMIE_CAPTURE_DATA_LANC") or _require_env(
        "OMIE_CAPTURE_PERIODO_INICIAL"
    )
    valor = Decimal(os.environ.get("OMIE_CAPTURE_VALOR_LANC") or _DEFAULT_VALOR_LANC)
    return IncluirLancCCRequest(
        n_cod_cc=int(_require_env("OMIE_CAPTURE_CONTA_ID")),
        d_dt_lanc=data_lanc,
        n_valor_lanc=valor,
        # 'D' (débito/compra) é o caso principal do fluxo da Sprint 7. O que a
        # fixture prova é o NOME e a ACEITAÇÃO do campo; o efeito do sinal é
        # conferido pelo readback do extrato (abaixo).
        c_natureza="D",
        c_cod_categ=_require_env("OMIE_CAPTURE_COD_CATEG"),
        c_cod_int_lanc=cod_int_lanc,
        c_obs="ADL BACK 07.1 - captura de fixture (excluir manualmente)",
        # cTipo NÃO é enviado: o valor `DIN` do PRD é palpite não-verificado.
        # Enviar um valor inventado poderia fazer a Omie recusar a chamada e
        # transformar a captura numa prova falsa ("o contrato está errado")
        # quando o errado seria só este campo.
        c_tipo=None,
    )


async def _capture_incluir_lanc_cc(client: OmieClient) -> None:
    """Grava request + DUAS respostas do mesmo `cCodIntLanc` (prova de idempotência).

    Uma resposta só (1 POST) mostra o contrato, mas **não** demonstra que a
    Omie impõe unicidade sobre `cCodIntLanc` (S-1). Por isso o segundo POST é
    parte da captura, e a resposta dele é gravada separadamente — inclusive
    quando é um `faultstring` (que é, aliás, o resultado *desejado*).

    **Recusa do 1º POST também é captura válida** (cross-check da doc,
    19/08/2026): a doc oficial descreve o `param` aninhado e o DTO emite plano,
    então a expectativa realista é a Omie recusar. A faultstring é gravada
    verbatim como `incluir_lanc_cc.response.json` e a captura de escrita para
    aí — nada foi criado, nada há para repetir nem reler.
    """
    request = _build_incluir_lanc_cc_request()
    param = request.model_dump(by_alias=True, exclude_none=True, mode="json")

    print(
        f"[capture] ⚠️ ESCRITA REAL: {_LANC_CC_CALL} "
        f"({_LANC_CC_MODULE}/{_LANC_CC_ENDPOINT}) — cria lançamento no Omie."
    )
    _write_json("incluir_lanc_cc.request", {"call_name": _LANC_CC_CALL, "param": param})

    try:
        first = await client.call(
            module=_LANC_CC_MODULE,
            endpoint=_LANC_CC_ENDPOINT,
            call_name=_LANC_CC_CALL,
            param=param,
        )
    except Exception as exc:  # a recusa é evidência S-1 — gravar, não crashar
        # Desde o cross-check da doc (19/08/2026) a recusa do 1º POST é o
        # desfecho MAIS PROVÁVEL: a doc descreve o `param` ANINHADO
        # (`cabecalho`/`detalhes`) e sem `cNatureza`, e o DTO emite plano —
        # ver `IncluirLancCCRequest`. A faultstring é exatamente a evidência
        # que o S-1 pede; crashar aqui a perderia (viraria só traceback).
        # Sem lançamento criado, o 2º POST não provaria nada e o readback não
        # teria o que ler — por isso a captura de escrita PARA aqui.
        _write_json(
            "incluir_lanc_cc.response",
            {
                "_adl_capture_note": (
                    "1º POST recusado — provável divergência de contrato "
                    "(doc descreve param aninhado). Texto preservado VERBATIM."
                ),
                "_adl_capture_exception_type": type(exc).__name__,
                "_adl_capture_exception_message": str(exc),
            },
        )
        print(
            "[capture] ⚠️ 1º POST RECUSADO — faultstring gravada em "
            "incluir_lanc_cc.response.json (evidência S-1, não é captura perdida).\n"
            "[capture]   Se a exceção foi TIMEOUT (não faultstring), confira no "
            "Omie se o lançamento chegou a ser criado antes de re-rodar."
        )
        return
    _write_json("incluir_lanc_cc.response", first)

    print("[capture] 2º POST com o MESMO cCodIntLanc (prova de idempotência) ...")
    try:
        repeat = await client.call(
            module=_LANC_CC_MODULE,
            endpoint=_LANC_CC_ENDPOINT,
            call_name=_LANC_CC_CALL,
            param=param,
        )
    except Exception as exc:  # o fault é o resultado ESPERADO aqui, não uma falha da captura
        # A Omie responde HTTP 200 com `faultstring` em erro; o OmieClient
        # converte isso em exceção. Se ela IMPÕE unicidade, o 2º POST cai aqui
        # — e essa é a evidência que a fixture precisa registrar.
        repeat = {
            "_adl_capture_note": (
                "2º POST levantou exceção no OmieClient — provável faultstring "
                "de duplicidade. Texto preservado abaixo VERBATIM."
            ),
            "_adl_capture_exception_type": type(exc).__name__,
            "_adl_capture_exception_message": str(exc),
        }
    _write_json("incluir_lanc_cc_repeat.response", repeat)

    await _capture_lanc_cc_readback(client, data_lanc=request.d_dt_lanc)


async def _capture_lanc_cc_readback(client: OmieClient, *, data_lanc: str) -> None:
    """Relê o extrato do dia do lançamento criado — evidência da CONVENÇÃO DE SINAL.

    Um request gravado não prova sinal nenhum: ele só mostra o que MANDAMOS. O
    que responde "o Omie interpretou `cNatureza='D'` + valor absoluto como
    débito?" é o extrato lido de volta. Sem este arquivo, a asserção de sinal
    da BACK 07.4 continuaria sendo uma suposição com cara de teste.
    """
    print(f"[capture] readback do extrato em {data_lanc} (evidência do sinal) ...")
    response = await client.call(
        module="financas",
        endpoint="extrato",
        call_name="ListarExtrato",
        param={
            "nCodCC": int(_require_env("OMIE_CAPTURE_CONTA_ID")),
            "cCodIntCC": "",
            "dPeriodoInicial": data_lanc,
            "dPeriodoFinal": data_lanc,
        },
        timeout_seconds=get_settings().OMIE_TIMEOUT_EXTRATO_SECONDS,
    )
    _write_json("incluir_lanc_cc.readback", response)


async def _main() -> None:
    captures = _build_captures()
    settings = get_settings()
    credentials = OmieCredentials(
        app_key=SecretStr(_require_env("OMIE_CAPTURE_APP_KEY")),
        app_secret=SecretStr(_require_env("OMIE_CAPTURE_APP_SECRET")),
    )
    write_enabled = _write_capture_enabled()
    if not write_enabled:
        print(
            "[capture] Captura de ESCRITA DESLIGADA (OMIE_CAPTURE_ALLOW_WRITE "
            "não está em 1/true/yes). Nenhum POST de IncluirLancCC será feito. "
            "Ver tests/fixtures/omie/README.md § captura de escrita."
        )

    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    async with OmieClient(credentials, settings) as client:
        for name, module, endpoint, call_name, param in captures:
            await _capture_one(
                client,
                name=name,
                module=module,
                endpoint=endpoint,
                call_name=call_name,
                param=param,
            )
        if write_enabled:
            await _capture_incluir_lanc_cc(client)

    print(
        "\n[capture] Concluído. ANONIMIZE os VALORES de PII (nomes/CNPJ) antes de "
        "commitar, mantendo os NOMES DE CAMPO verbatim. Ver README."
    )
    if write_enabled:
        print(
            "[capture] ⚠️ AÇÃO MANUAL PENDENTE: exclua no Omie o(s) lançamento(s) "
            "criados por esta captura (cCodIntLanc="
            f"{os.environ.get('OMIE_CAPTURE_COD_INT_LANC')}). Ver README."
        )


if __name__ == "__main__":
    asyncio.run(_main())
