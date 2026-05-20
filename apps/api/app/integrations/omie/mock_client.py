"""MockOmieClient — `OmieClient` que NÃO toca em rede.

Ativado automaticamente pelo `omie_factory.build_omie_client` quando a credencial
descriptografada começa com o prefixo `FAKE_DEMO_OMIE_` (gerado pelo
`scripts/seed_demo_client.py`). Esse prefixo é improvável em credencial real e
nunca seria aceito pelo Omie em produção.

Os payloads são calibrados para o cliente fictício **Padaria Pão Quente Ltda**
(seed) em conjunto com o `_MOCK_PADARIA_STATEMENT` do `parse_service.py`,
produzindo um cenário pós-processamento rico:
    - **5 conciliados** (linhas do arquivo que batem com lançamentos do Omie)
    - **26 sem_omie** (linhas do arquivo sem lançamento Omie correspondente)
    - **7 omie_sem_arquivo** (3 do extrato + 3 atrasados + 1 previsto)
    - **29 anomalias** (26 `missing_in_omie` + 3 `missing_in_file` por atrasados)

Não persiste nem loga conteúdo. **Nunca usar em produção.**
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.integrations.omie.schemas import (
    ContaCorrente,
    LancamentoExtrato,
    OmieTituloStatus,
    TituloAPagarReceber,
)

if TYPE_CHECKING:
    from app.core.config import Settings

log = get_logger(__name__)


# Prefixo usado por `scripts/seed_demo_client.py` na credencial fake.
# Mantemos a constante exportada para que o factory teste contra ela sem
# precisar conhecer detalhes internos do seed.
FAKE_DEMO_KEY_PREFIX = "FAKE_DEMO_OMIE_"

# ----------------------------------------------------------------------
# Delays artificiais — simulam latência do Omie real para que a tela de
# processamento do front (4 steps por tempo decorrido: 0-2s "Salvando",
# 2-10s "Buscando Omie", 10s+ "Cruzando") seja visível na demo.
#
# Total no fluxo de processamento: ~6.5s (1xlistar_extrato + 2xpagar +
# 2xreceber). Cabe confortavelmente na janela "Buscando lançamentos Omie".
#
# Sobrescrever em testes via monkeypatch para evitar slow tests:
#     monkeypatch.setattr(mock_client, "_DELAY_LISTAR_EXTRATO_SECONDS", 0.0)
# ----------------------------------------------------------------------
_DELAY_LISTAR_CLIENTES_SECONDS = 0.2  # test-connection
_DELAY_LISTAR_CONTAS_SECONDS = 0.1  # detalhe do cliente; minimizar latência percebida
_DELAY_LISTAR_EXTRATO_SECONDS = 2.5  # 1 chamada por sessão de conciliação
_DELAY_LISTAR_TITULOS_SECONDS = 1.0  # 4 chamadas por sessão (pagar/receber x 2 status)


# ----------------------------------------------------------------------
# Dados mockados — calibrados pra Padaria + abril/2026 + conta Itaú.
# ----------------------------------------------------------------------

# 3 contas, espelham o seed_demo_client. Formato real do Omie:
# `ListarContasCorrentes` devolve `codigo_banco` (string) e `tipo_conta_corrente`,
# sem o nome do banco por extenso.
_MOCK_CONTAS: list[ContaCorrente] = [
    ContaCorrente.model_validate(
        {
            "nCodCC": 900_000_001,
            "descricao": "Itaú 12345-6 (Principal)",
            "codigo_banco": "341",
            "tipo_conta_corrente": "CC",
        }
    ),
    ContaCorrente.model_validate(
        {
            "nCodCC": 900_000_002,
            "descricao": "Sicredi 91263-1",
            "codigo_banco": "748",
            "tipo_conta_corrente": "CC",
        }
    ),
    ContaCorrente.model_validate(
        {
            "nCodCC": 900_000_003,
            "descricao": "Cartão Visa Empresarial 4521",
            "codigo_banco": "341",
            # `CR` = Cartão de Crédito na nomenclatura Omie. A v1 do mock
            # usava `CA`, que na verdade é "Conta Aplicação" (auditoria M-1).
            "tipo_conta_corrente": "CR",
        }
    ),
]


# 8 lançamentos do extrato Itaú — 5 batem com o ParsedStatement mock + 3 órfãos.
# `cNatureza='D'` → débito (saída, signed_amount negativo).
# `cNatureza='C'` → crédito (entrada, signed_amount positivo).
# Aliases seguem o response real do Omie (cObservacoes, cSituacao, ...),
# pra que `model_validate` exercite o mesmo caminho do código de produção.
_MOCK_EXTRATO_ITAU: list[LancamentoExtrato] = [
    # === Os 5 que vão dar MATCH com o arquivo ===
    LancamentoExtrato.model_validate(
        {
            "nCodLancamento": 70001,
            "cNatureza": "D",
            "dDataLancamento": "03/04/2026",
            "nValorDocumento": Decimal("1250.00"),
            "cObservacoes": "PAG FORNECEDOR MOINHO PRADO",
            "cSituacao": "Conciliado",
        }
    ),
    LancamentoExtrato.model_validate(
        {
            "nCodLancamento": 70002,
            "cNatureza": "C",
            "dDataLancamento": "04/04/2026",
            "nValorDocumento": Decimal("1245.30"),
            "cObservacoes": "CIELO LIQUIDACAO",
            "cSituacao": "Conciliado",
        }
    ),
    LancamentoExtrato.model_validate(
        {
            "nCodLancamento": 70003,
            "cNatureza": "D",
            "dDataLancamento": "09/04/2026",
            "nValorDocumento": Decimal("487.90"),
            "cObservacoes": "ENEL CONTA ENERGIA",
            "cSituacao": "Conciliado",
        }
    ),
    LancamentoExtrato.model_validate(
        {
            "nCodLancamento": 70004,
            "cNatureza": "D",
            "dDataLancamento": "16/04/2026",
            "nValorDocumento": Decimal("6225.00"),
            "cObservacoes": "FOLHA 1A QUINZ",
            "cSituacao": "Conciliado",
        }
    ),
    LancamentoExtrato.model_validate(
        {
            "nCodLancamento": 70005,
            "cNatureza": "D",
            "dDataLancamento": "21/04/2026",
            "nValorDocumento": Decimal("1890.00"),
            "cObservacoes": "GUIA INSS",
            "cSituacao": "Conciliado",
        }
    ),
    # === 3 órfãos no Omie — vão pra `reconciliation_omie_entries` sem anomalia ===
    LancamentoExtrato.model_validate(
        {
            "nCodLancamento": 70010,
            "cNatureza": "C",
            "dDataLancamento": "05/04/2026",
            "nValorDocumento": Decimal("150.00"),
            "cObservacoes": "ESTORNO PIX",
            "cSituacao": "Conciliado",
        }
    ),
    LancamentoExtrato.model_validate(
        {
            "nCodLancamento": 70011,
            "cNatureza": "D",
            "dDataLancamento": "12/04/2026",
            "nValorDocumento": Decimal("2300.00"),
            "cObservacoes": "TRANSF INTERNA",
            "cSituacao": "Conciliado",
        }
    ),
    LancamentoExtrato.model_validate(
        {
            "nCodLancamento": 70012,
            "cNatureza": "C",
            "dDataLancamento": "19/04/2026",
            "nValorDocumento": Decimal("187.50"),
            "cObservacoes": "RENDIMENTO CDB",
            "cSituacao": "Conciliado",
        }
    ),
]

# Sicredi e Cartão devolvem vazio — Padaria só tem extrato rico no Itaú.
_MOCK_EXTRATO_BY_CONTA: dict[int, list[LancamentoExtrato]] = {
    900_000_001: _MOCK_EXTRATO_ITAU,
    900_000_002: [],
    900_000_003: [],
}


# Títulos a pagar — 2 atrasados (geram anomalia missing_in_file) + 1 AVENCER.
# As fixtures passam por `model_validate` (não instância direta) pra exercitar
# o mesmo caminho de parsing do código de produção. Os nomes refletem o
# response real do Omie — `codigo_cliente_fornecedor` e `codigo_categoria`
# em vez dos `nome_fornecedor`/`descricao_categoria` que NÃO existem na API.
_MOCK_CONTAS_PAGAR_ATRASADO: list[TituloAPagarReceber] = [
    TituloAPagarReceber.model_validate(
        {
            "codigo_lancamento_omie": 90001,
            "data_vencimento": "25/04/2026",
            "valor_documento": "890.00",
            "codigo_cliente_fornecedor": 100001,
            "codigo_categoria": "DT",
            "numero_documento": "00001",
            "observacao": "Distribuidora ABC — Insumos",
            "status_titulo": OmieTituloStatus.ATRASADO.value,
        }
    ),
    TituloAPagarReceber.model_validate(
        {
            "codigo_lancamento_omie": 90002,
            "data_vencimento": "28/04/2026",
            "valor_documento": "1567.30",
            "codigo_cliente_fornecedor": 100002,
            "codigo_categoria": "TR",
            "numero_documento": "00002",
            "observacao": "Logística XYZ — Transporte",
            "status_titulo": OmieTituloStatus.ATRASADO.value,
        }
    ),
]

_MOCK_CONTAS_PAGAR_AVENCER: list[TituloAPagarReceber] = [
    TituloAPagarReceber.model_validate(
        {
            "codigo_lancamento_omie": 90003,
            "data_vencimento": "30/04/2026",
            "valor_documento": "450.00",
            "codigo_cliente_fornecedor": 100003,
            "codigo_categoria": "SV",
            "numero_documento": "00003",
            "observacao": "Manutenção JK — Serviços",
            # `status_titulo` no response da Omie é `string3` — formato real
            # não documentado; valor genérico aqui pra não acoplar a teste a
            # uma suposição (auditoria A-2).
            "status_titulo": "ATR",
        }
    ),
]

# 1 a receber atrasado — gera anomalia missing_in_file.
_MOCK_CONTAS_RECEBER_ATRASADO: list[TituloAPagarReceber] = [
    TituloAPagarReceber.model_validate(
        {
            "codigo_lancamento_omie": 80001,
            "data_vencimento": "26/04/2026",
            "valor_documento": "3200.00",
            "codigo_cliente_fornecedor": 200001,
            "codigo_categoria": "VD",
            "numero_documento": "0801",
            "observacao": "Cliente Premium W — Vendas",
            "status_titulo": OmieTituloStatus.ATRASADO.value,
        }
    ),
]


class MockOmieClient(OmieClient):
    """`OmieClient` que devolve payloads fixos sem tocar a rede.

    Subclass de `OmieClient` para manter os mesmos type hints nos callers
    (`omie_factory.build_omie_client` retorna `OmieClient`), mas substitui
    o `__init__` para NÃO criar o `httpx.AsyncClient` interno e sobrescreve
    o ciclo de vida (`__aenter__`/`__aexit__`/`aclose`) como no-op.

    Métodos não sobrescritos (`call`, `_do_call`, `_paginate`) NÃO devem
    ser chamados — se forem, o `_http=None` resulta em `AttributeError`,
    que é melhor que silenciosamente bater na rede.
    """

    def __init__(
        self,
        credentials: OmieCredentials,
        settings: Settings,
        *,
        http_client: Any = None,
    ) -> None:
        # NÃO chama super().__init__() — evita criar httpx.AsyncClient.
        # Mantém atributos mínimos esperados por código que checa `_credentials`.
        self._credentials = credentials
        self._settings = settings
        self._base_url = ""
        self._timeout = 0
        self._http = None  # type: ignore[assignment]
        self._owns_http = False
        log.warning("omie_mock_client_built")

    async def __aenter__(self) -> MockOmieClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def aclose(self) -> None:
        return None

    # ------------------------------------------------------------------
    # Métodos tipados — payloads mockados
    # ------------------------------------------------------------------

    async def listar_clientes_minimal(self) -> dict[str, Any]:
        """Retorno mínimo válido — usado pra `test-connection` da Padaria."""
        await asyncio.sleep(_DELAY_LISTAR_CLIENTES_SECONDS)
        log.info("omie_mock_call", call="listar_clientes_minimal")
        return {"pagina": 1, "registros": 0, "total_de_registros": 0}

    async def listar_contas_correntes(self) -> list[ContaCorrente]:
        await asyncio.sleep(_DELAY_LISTAR_CONTAS_SECONDS)
        log.info("omie_mock_call", call="listar_contas_correntes", count=len(_MOCK_CONTAS))
        return list(_MOCK_CONTAS)

    async def listar_extrato(
        self,
        *,
        n_cod_cc: int,
        data_inicial: date,
        data_final: date,
    ) -> list[LancamentoExtrato]:
        # Filtra pelo período (defesa em profundidade — o caller já pede o
        # range certo, mas mantemos pra não retornar lançamentos fora da janela).
        await asyncio.sleep(_DELAY_LISTAR_EXTRATO_SECONDS)
        items = _MOCK_EXTRATO_BY_CONTA.get(n_cod_cc, [])
        filtered = [it for it in items if data_inicial <= it.d_data_lancamento <= data_final]
        log.info(
            "omie_mock_call",
            call="listar_extrato",
            n_cod_cc=n_cod_cc,
            count=len(filtered),
        )
        return filtered

    async def listar_contas_pagar(
        self,
        *,
        conta_corrente_id: int,
        data_de: date,
        data_ate: date,
        status: OmieTituloStatus,
    ) -> list[TituloAPagarReceber]:
        # Só Itaú (id 900_000_001) tem títulos no mock — outras contas vazias.
        await asyncio.sleep(_DELAY_LISTAR_TITULOS_SECONDS)
        if conta_corrente_id != 900_000_001:
            return []
        bucket = (
            _MOCK_CONTAS_PAGAR_ATRASADO
            if status == OmieTituloStatus.ATRASADO
            else _MOCK_CONTAS_PAGAR_AVENCER
        )
        filtered = [t for t in bucket if data_de <= t.data_vencimento <= data_ate]
        log.info(
            "omie_mock_call",
            call="listar_contas_pagar",
            conta_corrente_id=conta_corrente_id,
            status=status.value,
            count=len(filtered),
        )
        return filtered

    async def listar_contas_receber(
        self,
        *,
        conta_corrente_id: int,
        data_de: date,
        data_ate: date,
        status: OmieTituloStatus,
    ) -> list[TituloAPagarReceber]:
        await asyncio.sleep(_DELAY_LISTAR_TITULOS_SECONDS)
        if conta_corrente_id != 900_000_001:
            return []
        bucket = _MOCK_CONTAS_RECEBER_ATRASADO if status == OmieTituloStatus.ATRASADO else []
        filtered = [t for t in bucket if data_de <= t.data_vencimento <= data_ate]
        log.info(
            "omie_mock_call",
            call="listar_contas_receber",
            conta_corrente_id=conta_corrente_id,
            status=status.value,
            count=len(filtered),
        )
        return filtered
