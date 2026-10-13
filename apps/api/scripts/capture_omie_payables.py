"""Captura REAL das chamadas do Contas a Pagar do Omie (task 86e3n70q0, 11/10/2026).

Mesma receita da captura S-1 do `IncluirLancCC` (21/08/2026): cada chamada vai
à API REAL da Omie, o request (SEM credencial) e a resposta crua são gravados
em `tests/fixtures/omie/`, e a RECUSA (`faultstring`) também é evidência:
fica em `<nome>.response.json` (envelope `_adl_capture_*`) e acumulada em
`<nome>.refusals.json`, para o próximo payload ser escolhido pela resposta
real, nunca pela doc nem por memória (CLAUDE.md §6.8).

⚠️ **A Omie não tem sandbox.** Toda escrita cria registro REAL na
contabilidade do cliente autorizado. Por isso:

- escrita é opt-in (`OMIE_CAPTURE_ALLOW_WRITE=1`), como no script irmão;
- valores de R$ 0,01 e textos começando com `TESTE HOLOGRAM OS CAPTURA`;
- a LIMPEZA é um passo do próprio script (`--step cleanup`): cancela a baixa,
  exclui os três títulos e o fornecedor de teste, cada exclusão capturada; o
  que a API recusar sai na lista "apagar à mão" do relatório.

Uso (a partir de `apps/api`; ver `tests/fixtures/omie/README.md`):

    export OMIE_CAPTURE_APP_KEY=... OMIE_CAPTURE_APP_SECRET=...
    export OMIE_CAPTURE_CONTA_ID=<nCodCC do cartão>      # conta do título e da baixa
    export OMIE_CAPTURE_COD_CATEG=<categoria de despesa>  # 1ª categoria
    export OMIE_CAPTURE_COD_CATEG_2=<outra despesa>       # 2ª categoria (título b)
    export OMIE_CAPTURE_ALLOW_WRITE=1
    uv run python -m scripts.capture_omie_payables --step listar_departamentos
    uv run python -m scripts.capture_omie_payables                 # todos, em ordem
    uv run python -m scripts.capture_omie_payables --step cleanup  # limpeza

Variáveis opcionais:

    OMIE_CAPTURE_CNPJ_EXISTENTE   CNPJ que EXISTE no cadastro (default: o do 1º
                                  fornecedor devolvido por ListarClientes)
    OMIE_CAPTURE_CNPJ_TESTE       CNPJ do fornecedor de TESTE, que NÃO existe
                                  (default: 11.222.333/0001-81, dígitos válidos)
    OMIE_CAPTURE_DEP_1 / _DEP_2   códigos de dois departamentos ANALÍTICOS e ativos
                                  (obrigatórios para o rateio: a resposta real não
                                  distingue sintético de analítico)
    OMIE_CAPTURE_KEY_PREFIX       prefixo das chaves de integração (default
                                  `HOLOCAP-<ddmm>`); são elas que a limpeza usa
    OMIE_CAPTURE_ESTORNO=1        passo OPCIONAL `estorno`: dois `IncluirLancCC`
                                  (categoria de receita e valor negativo) para
                                  ver qual vira crédito no extrato; exige
                                  OMIE_CAPTURE_COD_CATEG_RECEITA
    OMIE_CAPTURE_DATA_HOJE        `dd/mm/aaaa` da emissão/baixa (default: hoje)

A limpeza lê os ids das PRÓPRIAS fixtures gravadas (`incluir_cliente.response`,
`incluir_conta_pagar*.response`, `lancar_pagamento.response`): não há estado
fora do diretório de fixtures. **Sanitize a PII dos VALORES antes de commitar**,
mantendo os NOMES DE CAMPO verbatim (README).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, NamedTuple

from pydantic import BaseModel, SecretStr

from app.core.config import get_settings
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.integrations.omie.schemas import (
    CancelarPagamentoRequest,
    ClienteChaveRequest,
    ClientesFiltro,
    ContaPagarCategoria,
    ContaPagarChaveRequest,
    ContaPagarDistribuicao,
    ExcluirLancCCRequest,
    IncluirClienteRequest,
    IncluirContaPagarRequest,
    IncluirLancCCRequest,
    LancarPagamentoRequest,
    LancCCCabecalho,
    LancCCDetalhes,
    ListarClientesPorCnpjRequest,
)
from scripts.capture_omie_fixtures import (
    _FIXTURES_DIR,
    _require_env,
    _write_capture_enabled,
    _write_json,
)

# O menor valor possível: o registro é REAL. "Menos danoso", não "seguro".
_VALOR_TESTE = Decimal("0.01")
_PREFIXO_TEXTO = "TESTE HOLOGRAM OS CAPTURA"
# CNPJ com dígitos verificadores válidos que não pertence a empresa real
# conhecida; a busca por ele (passo 2) prova que não existe no cadastro.
_CNPJ_TESTE_DEFAULT = "11.222.333/0001-81"
# A Omie processa 1 requisição do mesmo método por vez (CLAUDE.md skill `omie`,
# passo 4): entre o 1º e o 2º POST do mesmo método há uma pausa curta.
_PAUSA_ENTRE_CHAMADAS_SECONDS = 1.5
# O extrato NÃO reflete na hora o lançamento recém-criado: em 13/10/2026 a releitura
# 1,5 s depois do `IncluirLancCC` não trazia a linha, e 20 s depois trazia. Antes de reler
# o extrato como evidência, o script espera.
_ESPERA_EXTRATO_SECONDS = 25.0
# UTC-3 fixo, como `client_movements/competence.py`: "hoje" é o dia no Brasil.
_BRT = timezone(timedelta(hours=-3))

_Step = Callable[["_Capture"], Awaitable[None]]


class _Service(NamedTuple):
    """Par módulo/endpoint da URL da Omie (`/api/v1/<module>/<endpoint>/`)."""

    module: str
    endpoint: str


def _hoje() -> str:
    return os.environ.get("OMIE_CAPTURE_DATA_HOJE") or datetime.now(tz=_BRT).strftime("%d/%m/%Y")


def _mais_dias(data_br: str, dias: int) -> str:
    base = datetime.strptime(data_br, "%d/%m/%Y").replace(tzinfo=_BRT)
    return (base + timedelta(days=dias)).strftime("%d/%m/%Y")


def _prefixo_chave() -> str:
    hoje = datetime.strptime(_hoje(), "%d/%m/%Y").replace(tzinfo=_BRT)
    return os.environ.get("OMIE_CAPTURE_KEY_PREFIX") or f"HOLOCAP-{hoje:%d%m}"


def _chave(sufixo: str, *, max_len: int = 60) -> str:
    chave = f"{_prefixo_chave()}-{sufixo}"
    if len(chave) > max_len:
        raise SystemExit(f"chave de integração {chave!r} tem {len(chave)} chars; limite {max_len}.")
    return chave


def _load(name: str) -> dict[str, Any] | None:
    path = _FIXTURES_DIR / f"{name}.json"
    if not path.exists():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else None


def _is_refusal(payload: dict[str, Any] | None) -> bool:
    return payload is None or "_adl_capture_exception_type" in payload


def _param(request: BaseModel) -> dict[str, Any]:
    """`param` no fio a partir do DTO — é o que fecha o laço com o gate de fixture."""
    dumped = request.model_dump(by_alias=True, exclude_none=True, mode="json")
    assert isinstance(dumped, dict)
    return dumped


class _Capture:
    """Uma sessão de captura: o client, e as chamadas com gravação e recusa."""

    def __init__(self, client: OmieClient) -> None:
        self.client = client

    async def call(
        self,
        name: str,
        *,
        service: _Service,
        call_name: str,
        param: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        """Grava `<name>.request.json` e a resposta (ou a recusa) em `<name>.response.json`.

        A recusa NÃO derruba o script: ela é a evidência que decide o próximo
        payload. Fica no `response` (envelope `_adl_capture_*`, como na S-1) e
        acumula em `<name>.refusals.json` com o param que foi recusado, para o
        relatório listar cada tentativa. Devolve `None` na recusa.
        """
        module, endpoint = service
        print(f"[capture] {call_name} ({module}/{endpoint}) -> {name}")
        _write_json(f"{name}.request", {"call_name": call_name, "param": param})
        try:
            response = await self.client.call(
                module=module,
                endpoint=endpoint,
                call_name=call_name,
                param=param,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:  # a recusa é a evidência — gravar, nunca crashar
            refusal = {
                "_adl_capture_note": (
                    f"{call_name} recusado pela Omie. Texto preservado VERBATIM; o "
                    "param recusado está em .refusals.json."
                ),
                "_adl_capture_exception_type": type(exc).__name__,
                "_adl_capture_exception_message": str(exc),
            }
            _write_json(f"{name}.response", refusal)
            self._append_refusal(name, call_name=call_name, param=param, refusal=refusal)
            print(f"[capture] ⚠️ RECUSADO: {exc}")
            return None
        _write_json(f"{name}.response", response)
        return response

    async def repeat(
        self,
        name: str,
        *,
        service: _Service,
        call_name: str,
        param: dict[str, Any],
    ) -> dict[str, Any] | None:
        """2º envio do MESMO param — prova (ou refuta) a idempotência da chave."""
        await asyncio.sleep(_PAUSA_ENTRE_CHAMADAS_SECONDS)
        module, endpoint = service
        print(f"[capture] {call_name} REPETIDO com a mesma chave -> {name}_repeat")
        try:
            response = await self.client.call(
                module=module, endpoint=endpoint, call_name=call_name, param=param
            )
        except Exception as exc:  # o fault é um resultado possível aqui, não falha da captura
            response = {
                "_adl_capture_note": (
                    "2º envio levantou exceção no OmieClient — a Omie recusou a "
                    "repetição da chave. Texto preservado VERBATIM."
                ),
                "_adl_capture_exception_type": type(exc).__name__,
                "_adl_capture_exception_message": str(exc),
            }
        _write_json(f"{name}_repeat.response", response)
        return response

    @staticmethod
    def _append_refusal(
        name: str, *, call_name: str, param: dict[str, Any], refusal: dict[str, Any]
    ) -> None:
        path = _FIXTURES_DIR / f"{name}.refusals.json"
        history: list[dict[str, Any]] = []
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        history.append(
            {
                "call_name": call_name,
                "captured_at": datetime.now(tz=UTC).isoformat(timespec="seconds"),
                "param": param,
                **{k: v for k, v in refusal.items() if k != "_adl_capture_note"},
            }
        )
        _write_json(f"{name}.refusals", history)


# ----------------------------------------------------------------------
# Passo 1 — departamentos
# ----------------------------------------------------------------------


async def step_listar_departamentos(cap: _Capture) -> None:
    await cap.call(
        "listar_departamentos",
        service=_DEPARTAMENTOS,
        call_name="ListarDepartamentos",
        param={"pagina": 1, "registros_por_pagina": 50},
    )


def _departamentos_do_rateio() -> tuple[str, str]:
    """Os dois departamentos do rateio vêm de `OMIE_CAPTURE_DEP_1/_DEP_2`, sempre.

    A resposta real do `ListarDepartamentos` (13/10/2026) NÃO traz o
    `nivel_totalizador` da doc: nível sintético e analítico só se distinguem pela
    convenção de nome do cliente. Escolher "os dois primeiros ativos" cairia num
    sintético e a Omie recusaria (ou, pior, aceitaria) o rateio sem a captura dizer
    por quê — por isso a escolha é explícita e fica registrada no relatório.
    """
    dep1 = os.environ.get("OMIE_CAPTURE_DEP_1")
    dep2 = os.environ.get("OMIE_CAPTURE_DEP_2")
    if not (dep1 and dep2):
        raise SystemExit(
            "Rateio pede OMIE_CAPTURE_DEP_1 e OMIE_CAPTURE_DEP_2 (códigos de departamentos "
            "ANALÍTICOS e ativos de `listar_departamentos.response.json`)."
        )
    return dep1, dep2


# ----------------------------------------------------------------------
# Passo 2 — fornecedor por CNPJ (existente e inexistente)
# ----------------------------------------------------------------------


def _cnpj_teste() -> str:
    return os.environ.get("OMIE_CAPTURE_CNPJ_TESTE") or _CNPJ_TESTE_DEFAULT


async def _cnpj_existente(cap: _Capture) -> str:
    informado = os.environ.get("OMIE_CAPTURE_CNPJ_EXISTENTE")
    if informado:
        return informado
    # Sem CNPJ informado, o 1º cadastro devolvido pela listagem serve: a
    # resposta é anonimizada antes do commit, como toda fixture.
    resp = await cap.client.call(
        module="geral",
        endpoint="clientes",
        call_name="ListarClientes",
        param={"pagina": 1, "registros_por_pagina": 1},
    )
    cadastros = resp.get("clientes_cadastro") or []
    cnpj = cadastros[0].get("cnpj_cpf") if cadastros else None
    if not cnpj:
        raise SystemExit(
            "ListarClientes não devolveu cnpj_cpf; exporte OMIE_CAPTURE_CNPJ_EXISTENTE."
        )
    return str(cnpj)


async def step_buscar_fornecedor_por_cnpj(cap: _Capture) -> None:
    existente = await _cnpj_existente(cap)
    await asyncio.sleep(_PAUSA_ENTRE_CHAMADAS_SECONDS)
    await cap.call(
        "listar_clientes_por_cnpj",
        service=_CLIENTES,
        call_name="ListarClientes",
        param=_param(
            ListarClientesPorCnpjRequest(clientes_filtro=ClientesFiltro(cnpj_cpf=existente))
        ),
    )
    await asyncio.sleep(_PAUSA_ENTRE_CHAMADAS_SECONDS)
    await cap.call(
        "listar_clientes_por_cnpj_inexistente",
        service=_CLIENTES,
        call_name="ListarClientes",
        param=_param(
            ListarClientesPorCnpjRequest(clientes_filtro=ClientesFiltro(cnpj_cpf=_cnpj_teste()))
        ),
    )


# ----------------------------------------------------------------------
# Passo 3 — fornecedor de teste (escrita)
# ----------------------------------------------------------------------

_CLIENTES = _Service("geral", "clientes")
_CONTAPAGAR = _Service("financas", "contapagar")
_LANCCC = _Service("financas", "contacorrentelancamentos")
_EXTRATO = _Service("financas", "extrato")
_DEPARTAMENTOS = _Service("geral", "departamentos")


def _require_write() -> None:
    if not _write_capture_enabled():
        raise SystemExit(
            "Passo de ESCRITA: exige OMIE_CAPTURE_ALLOW_WRITE=1 (cria registro REAL no Omie)."
        )


async def step_incluir_cliente(cap: _Capture) -> None:
    _require_write()
    request = IncluirClienteRequest(
        codigo_cliente_integracao=_chave("FORN"),
        razao_social=f"{_PREFIXO_TEXTO} FORNECEDOR LTDA",
        nome_fantasia=f"{_PREFIXO_TEXTO} FORNECEDOR",
        cnpj_cpf=_cnpj_teste(),
        # 1ª tentativa sem e-mail (13/10/2026): `1040 - É obrigatório o preenchimento do e-mail.`
        email="captura.teste@example.com",
    )
    param = _param(request)
    first = await cap.call(
        "incluir_cliente", call_name="IncluirCliente", param=param, service=_CLIENTES
    )
    if first is None:
        return
    await cap.repeat("incluir_cliente", call_name="IncluirCliente", param=param, service=_CLIENTES)


def _codigo_fornecedor_teste() -> int:
    resp = _load("incluir_cliente.response")
    if _is_refusal(resp) or resp is None or "codigo_cliente_omie" not in resp:
        raise SystemExit("Sem fornecedor de teste aceito: rode `--step incluir_cliente` antes.")
    return int(resp["codigo_cliente_omie"])


# ----------------------------------------------------------------------
# Passo 4 — títulos a pagar (escrita): (a) simples, (b) 2 categorias, (c) rateio
# ----------------------------------------------------------------------


def _titulo_base(sufixo: str, *, valor: Decimal, observacao: str) -> dict[str, Any]:
    hoje = _hoje()
    return {
        "codigo_lancamento_integracao": _chave(sufixo),
        "codigo_cliente_fornecedor": _codigo_fornecedor_teste(),
        "data_emissao": hoje,
        "data_vencimento": _mais_dias(hoje, 30),
        "data_previsao": _mais_dias(hoje, 30),
        "valor_documento": valor,
        "id_conta_corrente": int(_require_env("OMIE_CAPTURE_CONTA_ID")),
        "observacao": f"{_PREFIXO_TEXTO} {observacao}",
    }


async def step_incluir_conta_pagar(cap: _Capture) -> None:
    """(a) título simples — e o 2º POST com a mesma chave (idempotência)."""
    _require_write()
    request = IncluirContaPagarRequest(
        **_titulo_base("CP-A", valor=_VALOR_TESTE, observacao="titulo simples (a)"),
        codigo_categoria=_require_env("OMIE_CAPTURE_COD_CATEG"),
    )
    param = _param(request)
    first = await cap.call(
        "incluir_conta_pagar", call_name="IncluirContaPagar", param=param, service=_CONTAPAGAR
    )
    if first is None:
        return
    await cap.repeat(
        "incluir_conta_pagar", call_name="IncluirContaPagar", param=param, service=_CONTAPAGAR
    )


async def step_incluir_conta_pagar_duas_categorias(cap: _Capture) -> None:
    """(b) R$ 0,02 dividido em duas categorias de R$ 0,01 (subtask 10)."""
    _require_write()
    categorias = [
        ContaPagarCategoria(
            codigo_categoria=_require_env("OMIE_CAPTURE_COD_CATEG"),
            valor=_VALOR_TESTE,
            percentual=Decimal("50"),
        ),
        ContaPagarCategoria(
            codigo_categoria=_require_env("OMIE_CAPTURE_COD_CATEG_2"),
            valor=_VALOR_TESTE,
            percentual=Decimal("50"),
        ),
    ]
    request = IncluirContaPagarRequest(
        **_titulo_base("CP-B", valor=_VALOR_TESTE * 2, observacao="duas categorias (b)"),
        categorias=categorias,
    )
    await cap.call(
        "incluir_conta_pagar_duas_categorias",
        call_name="IncluirContaPagar",
        param=_param(request),
        service=_CONTAPAGAR,
    )


async def step_incluir_conta_pagar_distribuicao(cap: _Capture) -> None:
    """(c) R$ 0,02 rateado em dois departamentos (percentual E valor)."""
    _require_write()
    dep1, dep2 = _departamentos_do_rateio()
    distribuicao = [
        ContaPagarDistribuicao(c_cod_dep=dep1, n_val_dep=_VALOR_TESTE, n_per_dep=Decimal("50")),
        ContaPagarDistribuicao(c_cod_dep=dep2, n_val_dep=_VALOR_TESTE, n_per_dep=Decimal("50")),
    ]
    request = IncluirContaPagarRequest(
        **_titulo_base("CP-C", valor=_VALOR_TESTE * 2, observacao="rateio por departamento (c)"),
        codigo_categoria=_require_env("OMIE_CAPTURE_COD_CATEG"),
        distribuicao=distribuicao,
    )
    await cap.call(
        "incluir_conta_pagar_distribuicao",
        call_name="IncluirContaPagar",
        param=_param(request),
        service=_CONTAPAGAR,
    )


def _codigo_titulo(name: str) -> int | None:
    resp = _load(f"{name}.response")
    if _is_refusal(resp) or resp is None:
        return None
    codigo = resp.get("codigo_lancamento_omie")
    return int(codigo) if codigo is not None else None


# ----------------------------------------------------------------------
# Passo 5 — baixa do título (a) e releituras
# ----------------------------------------------------------------------


async def step_lancar_pagamento(cap: _Capture) -> None:
    _require_write()
    codigo = _codigo_titulo("incluir_conta_pagar")
    if codigo is None:
        raise SystemExit("Sem título (a) aceito: rode `--step incluir_conta_pagar` antes.")
    request = LancarPagamentoRequest(
        codigo_lancamento=codigo,
        codigo_baixa_integracao=_chave("BX-A", max_len=20),
        codigo_conta_corrente=int(_require_env("OMIE_CAPTURE_CONTA_ID")),
        valor=_VALOR_TESTE,
        data=_hoje(),
        observacao=f"{_PREFIXO_TEXTO} baixa do titulo (a)",
    )
    param = _param(request)
    first = await cap.call(
        "lancar_pagamento", call_name="LancarPagamento", param=param, service=_CONTAPAGAR
    )
    if first is None:
        return
    await cap.repeat(
        "lancar_pagamento", call_name="LancarPagamento", param=param, service=_CONTAPAGAR
    )


async def step_readback(cap: _Capture) -> None:
    """Como o título e a baixa aparecem depois: consulta, listagem e extrato do cartão."""
    codigo = _codigo_titulo("incluir_conta_pagar")
    if codigo is None:
        raise SystemExit("Sem título (a) aceito: nada para reler.")
    await cap.call(
        "consultar_conta_pagar",
        call_name="ConsultarContaPagar",
        param=_param(ContaPagarChaveRequest(codigo_lancamento_omie=codigo)),
        service=_CONTAPAGAR,
    )
    await asyncio.sleep(_PAUSA_ENTRE_CHAMADAS_SECONDS)
    await cap.call(
        "listar_contas_pagar_apos_baixa",
        call_name="ListarContasPagar",
        param={
            "pagina": 1,
            "registros_por_pagina": 50,
            "filtrar_cliente": _codigo_fornecedor_teste(),
        },
        service=_CONTAPAGAR,
    )
    await _capture_extrato_hoje(cap, "listar_extrato_baixa")


async def _capture_extrato_hoje(cap: _Capture, name: str) -> None:
    """Extrato do cartão no dia da captura — como a baixa e o crédito aparecem."""
    print(f"[capture] aguardando {_ESPERA_EXTRATO_SECONDS:.0f}s para o extrato refletir ...")
    await asyncio.sleep(_ESPERA_EXTRATO_SECONDS)
    await cap.call(
        name,
        service=_EXTRATO,
        call_name="ListarExtrato",
        param={
            "nCodCC": int(_require_env("OMIE_CAPTURE_CONTA_ID")),
            "cCodIntCC": "",
            "dPeriodoInicial": _hoje(),
            "dPeriodoFinal": _hoje(),
        },
        timeout_seconds=get_settings().OMIE_TIMEOUT_EXTRATO_SECONDS,
    )


# ----------------------------------------------------------------------
# Passo 7 (opcional) — o CRÉDITO no IncluirLancCC (épico 86e2xweaf, item 2)
# ----------------------------------------------------------------------


def _lanc_cc(sufixo: str, *, categoria: str, valor: Decimal, obs: str) -> dict[str, Any]:
    return _param(
        IncluirLancCCRequest(
            c_cod_int_lanc=_chave(sufixo, max_len=20),
            cabecalho=LancCCCabecalho(
                n_cod_cc=int(_require_env("OMIE_CAPTURE_CONTA_ID")),
                d_dt_lanc=_hoje(),
                n_valor_lanc=valor,
            ),
            detalhes=LancCCDetalhes(
                c_cod_categ=categoria, c_obs=f"{_PREFIXO_TEXTO} {obs}", c_tipo="DIN"
            ),
        )
    )


async def step_estorno(cap: _Capture) -> None:
    """Qual representação vira crédito (natureza `R`) no extrato do cartão?"""
    _require_write()
    if (os.environ.get("OMIE_CAPTURE_ESTORNO") or "").strip().lower() not in {"1", "true", "yes"}:
        raise SystemExit("Passo opcional: exige OMIE_CAPTURE_ESTORNO=1 (decisão do Pedro na hora).")
    receita = _require_env("OMIE_CAPTURE_COD_CATEG_RECEITA")
    await cap.call(
        "incluir_lanc_cc_credito_receita",
        call_name="IncluirLancCC",
        param=_lanc_cc("LC-REC", categoria=receita, valor=_VALOR_TESTE, obs="credito via receita"),
        service=_LANCCC,
    )
    await asyncio.sleep(_PAUSA_ENTRE_CHAMADAS_SECONDS)
    await cap.call(
        "incluir_lanc_cc_credito_negativo",
        call_name="IncluirLancCC",
        param=_lanc_cc(
            "LC-NEG",
            categoria=_require_env("OMIE_CAPTURE_COD_CATEG"),
            valor=-_VALOR_TESTE,
            obs="credito via valor negativo",
        ),
        service=_LANCCC,
    )
    await _capture_extrato_hoje(cap, "listar_extrato_credito")


async def step_readback_credito(cap: _Capture) -> None:
    """Só a releitura do extrato do passo 7 (quando a primeira veio antes do reflexo)."""
    await _capture_extrato_hoje(cap, "listar_extrato_credito")


# ----------------------------------------------------------------------
# Passo 6 — LIMPEZA: tudo que a captura criou sai pela API (cada exclusão capturada)
# ----------------------------------------------------------------------


def _n_cod_lanc(name: str) -> int | None:
    resp = _load(f"{name}.response")
    if _is_refusal(resp) or resp is None:
        return None
    valor = resp.get("nCodLanc")
    return int(valor) if valor is not None else None


async def step_cleanup(cap: _Capture) -> None:
    _require_write()
    pendentes: list[str] = []

    baixa = _load("lancar_pagamento.response")
    if not _is_refusal(baixa) and baixa is not None and baixa.get("codigo_baixa") is not None:
        ok = await cap.call(
            "cancelar_pagamento",
            call_name="CancelarPagamento",
            param=_param(CancelarPagamentoRequest(codigo_baixa=int(baixa["codigo_baixa"]))),
            service=_CONTAPAGAR,
        )
        if ok is None:
            pendentes.append(f"baixa codigo_baixa={baixa['codigo_baixa']}")
        await asyncio.sleep(_PAUSA_ENTRE_CHAMADAS_SECONDS)

    for titulo, name in (
        ("incluir_conta_pagar", "excluir_conta_pagar"),
        ("incluir_conta_pagar_duas_categorias", "excluir_conta_pagar_duas_categorias"),
        ("incluir_conta_pagar_distribuicao", "excluir_conta_pagar_distribuicao"),
    ):
        codigo = _codigo_titulo(titulo)
        if codigo is None:
            continue
        ok = await cap.call(
            name,
            call_name="ExcluirContaPagar",
            param=_param(ContaPagarChaveRequest(codigo_lancamento_omie=codigo)),
            service=_CONTAPAGAR,
        )
        if ok is None:
            pendentes.append(f"título codigo_lancamento_omie={codigo} ({titulo})")
        await asyncio.sleep(_PAUSA_ENTRE_CHAMADAS_SECONDS)

    for lanc, name in (
        ("incluir_lanc_cc_credito_receita", "excluir_lanc_cc_credito_receita"),
        ("incluir_lanc_cc_credito_negativo", "excluir_lanc_cc_credito_negativo"),
    ):
        n_cod = _n_cod_lanc(lanc)
        if n_cod is None:
            continue
        ok = await cap.call(
            name,
            call_name="ExcluirLancCC",
            param=_param(ExcluirLancCCRequest(nCodLanc=n_cod)),
            service=_LANCCC,
        )
        if ok is None:
            pendentes.append(f"lançamento CC nCodLanc={n_cod} ({lanc})")
        await asyncio.sleep(_PAUSA_ENTRE_CHAMADAS_SECONDS)

    fornecedor = _load("incluir_cliente.response")
    if not _is_refusal(fornecedor) and fornecedor is not None:
        codigo_f = fornecedor.get("codigo_cliente_omie")
        if codigo_f is not None:
            ok = await cap.call(
                "excluir_cliente",
                call_name="ExcluirCliente",
                param=_param(ClienteChaveRequest(codigo_cliente_omie=int(codigo_f))),
                service=_CLIENTES,
            )
            if ok is None:
                pendentes.append(f"fornecedor codigo_cliente_omie={codigo_f}")

    if pendentes:
        print("[capture] ⚠️ APAGAR À MÃO no Omie (a API recusou):")
        for item in pendentes:
            print(f"[capture]   - {item}")
    else:
        print("[capture] limpeza concluída: nada ficou para apagar à mão.")


async def step_readback_limpeza(cap: _Capture) -> None:
    """Extrato do dia DEPOIS da limpeza: prova que a baixa cancelada e o crédito
    excluído saíram do extrato (sobram só as linhas de saldo)."""
    await _capture_extrato_hoje(cap, "listar_extrato_apos_limpeza")


# ----------------------------------------------------------------------
# Orquestração
# ----------------------------------------------------------------------

STEPS: dict[str, _Step] = {
    "listar_departamentos": step_listar_departamentos,
    "buscar_fornecedor_por_cnpj": step_buscar_fornecedor_por_cnpj,
    "incluir_cliente": step_incluir_cliente,
    "incluir_conta_pagar": step_incluir_conta_pagar,
    "incluir_conta_pagar_duas_categorias": step_incluir_conta_pagar_duas_categorias,
    "incluir_conta_pagar_distribuicao": step_incluir_conta_pagar_distribuicao,
    "lancar_pagamento": step_lancar_pagamento,
    "readback": step_readback,
    "estorno": step_estorno,
    "readback_credito": step_readback_credito,
    "cleanup": step_cleanup,
    "readback_limpeza": step_readback_limpeza,
}
# Sem `--step`, roda a sequência de captura; `estorno` e `cleanup` são sempre explícitos.
_DEFAULT_SEQUENCE = tuple(
    k for k in STEPS if k not in {"estorno", "readback_credito", "cleanup", "readback_limpeza"}
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--step",
        action="append",
        choices=sorted(STEPS),
        help="passo a rodar (repetível, na ordem dada). Default: a sequência de captura.",
    )
    return parser.parse_args(argv)


async def run_steps(step_names: list[str]) -> None:
    settings = get_settings()
    credentials = OmieCredentials(
        app_key=SecretStr(_require_env("OMIE_CAPTURE_APP_KEY")),
        app_secret=SecretStr(_require_env("OMIE_CAPTURE_APP_SECRET")),
    )
    _FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    async with OmieClient(credentials, settings) as client:
        cap = _Capture(client)
        for name in step_names:
            print(f"\n[capture] ===== passo: {name} =====")
            await STEPS[name](cap)
            await asyncio.sleep(_PAUSA_ENTRE_CHAMADAS_SECONDS)
    print(
        "\n[capture] Concluído. ANONIMIZE os VALORES de PII (nomes/CNPJ/e-mail/telefone) "
        "antes de commitar, mantendo os NOMES DE CAMPO verbatim. Ver README."
    )


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    asyncio.run(run_steps(args.step or list(_DEFAULT_SEQUENCE)))


if __name__ == "__main__":
    main()
