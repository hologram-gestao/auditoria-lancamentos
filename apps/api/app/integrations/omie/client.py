"""Cliente HTTP async para a API do Omie.

Princípios (Doc §6 + CLAUDE.md):
    - **Credenciais NUNCA logadas** — o redactor do `app.core.logging` mascara
      automaticamente `app_key`/`app_secret`, mas o cliente também evita logar
      o body completo: registra apenas `module`, `endpoint`, `call_name`,
      `duration_ms`, `status`.
    - **Retry com backoff exponencial** em 5xx e timeouts — NUNCA em
      `faultstring` (erro lógico do Omie, retry seria inútil).
    - **Timeout 15s** padrão (configurável via `OMIE_TIMEOUT_SECONDS`).
    - **Resposta de erro com HTTP 200**: toda response precisa ser checada por
      `faultstring` antes de processar dados (Doc §6.3).
    - **Paginação automática** via `_paginate()` (helper async iterator).

Os métodos tipados usam `model_validate` dos schemas em `omie.schemas` para
converter as respostas raw em objetos Pydantic.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from datetime import date
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import BaseModel, SecretStr
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.exceptions import (
    OmieAuthError,
    OmieFaultError,
    OmieServerError,
    OmieTimeoutError,
)
from app.core.logging import get_logger
from app.integrations.omie.schemas import (
    ContaCorrente,
    LancamentoExtrato,
    OmieFaultPayload,
    OmieTituloStatus,
    TituloAPagarReceber,
)

if TYPE_CHECKING:
    from app.core.config import Settings

log = get_logger(__name__)

# Substrings (case-insensitive) em `faultstring` que indicam erro de
# autenticação. Mapeamos para `OmieAuthError` (sem retry) em vez do genérico
# `OmieFaultError`. Ordem não importa.
#
# Auditoria M-2 (corrigido em 20/05/2026): a v1 misturava `faultcode` aqui
# (ex: `"soap-env:client-101"`) — mas o code é um campo separado no payload,
# não cabe num matching contra `fault_string.lower()`. Separamos em duas
# listas. Doc oficial não enumera faultcodes; estes vêm da convenção SOAP
# da Omie + observação empírica (pendência: capturar fault real em dev).
_AUTH_FAULT_STRING_KEYWORDS: tuple[str, ...] = (
    "app_key",
    "app key",
    "app_secret",
    "app secret",
    "credenciais",
    "credencial inválida",
    "acesso negado",
    "unauthorized",
)

# Faultcodes (case-insensitive) que o Omie devolve quando a autenticação
# falhou. Convenção SOAP: `SOAP-ENV:Client-101` (credencial), `-102`/`-103`
# (escopo da APP), etc. A doc oficial não enumera — quando capturarmos
# uma fixture real, adicionar aqui com confiança maior.
_AUTH_FAULT_CODES: tuple[str, ...] = (
    "soap-env:client-101",
    "soap-env:client-102",
    "soap-env:client-103",
)

# Prefixos numéricos do `OmieAPI-Error` que indicam erro **transitório** —
# vale retry com backoff (Tenacity já cuida da espera entre tentativas).
# A maioria dos OmieAPI-Error é permanente (5001 tag inválida, etc.); os
# códigos abaixo são exceções confirmadas em prod:
#   - `1880`: "Já existe uma requisição desse método sendo executada e
#     você pode tentar novamente em alguns instantes." → o Omie tem rate
#     limit por método em paralelo (`X-Omie-ParallelRateLimit: 1/4`); se
#     2 chamadas do mesmo método chegam em quick succession, a 2ª pode
#     bater nisso.
#   - `6`: "Consumo redundante detectado. Aguarde N segundos para tentar
#     novamente (REDUNDANT)." → mesma família, com janela maior.
# Visto no Austral em 20/05/2026 ao processar mês com várias chamadas
# de ListarContasPagar/Receber em sequência.
_RETRYABLE_OMIE_API_ERROR_PREFIXES: tuple[str, ...] = (
    "1880",
    "6 -",
)

# Regex pra extrair o tempo de cooldown do código `6` do Omie:
#   "6 - Consumo redundante detectado. Aguarde 58 segundos..."
# A Omie literalmente nos diz quanto esperar — dormir esse tempo antes do
# retry é mais eficaz que o backoff exponencial padrão. Sem isso, o
# Tenacity vai retentar antes do cooldown expirar e a Omie só pune mais.
_REDUNDANT_WAIT_PATTERN = re.compile(r"Aguarde\s+(\d+)\s+segundos?", re.IGNORECASE)

# Sleep máximo respeitando "Aguarde N" do Omie. Cap em 70s — cobre o
# cooldown observado (58s) com folga, mas não trava o job indefinidamente
# se a Omie um dia devolver um número absurdo.
_REDUNDANT_MAX_SLEEP_SECONDS = 70.0


class OmieCredentials(BaseModel):
    """Par de credenciais descriptografadas em memória.

    NUNCA persistir em log/banco/response. SecretStr garante que `repr()` mostra
    `**********` em vez do valor; combinado ao redactor do logging, é
    extremamente difícil vazar acidentalmente.
    """

    app_key: SecretStr
    app_secret: SecretStr


class _RetryableHttpError(Exception):
    """Erro HTTP transitório (5xx) — sinaliza para Tenacity reagendar."""


class OmieClient:
    """Cliente async para a API Omie de um único cliente BPO.

    Uma instância por par de credenciais. Reusar o mesmo `httpx.AsyncClient`
    intra-instância (connection pooling) — instanciar de novo para
    cliente BPO diferente.
    """

    def __init__(
        self,
        credentials: OmieCredentials,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._credentials = credentials
        self._settings = settings
        self._base_url = settings.OMIE_BASE_URL.rstrip("/")
        self._timeout = settings.OMIE_TIMEOUT_SECONDS
        # Permite injeção em testes; senão criamos próprio com pool razoável
        self._http = http_client or httpx.AsyncClient(timeout=self._timeout)
        self._owns_http = http_client is None

    async def __aenter__(self) -> OmieClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    # ------------------------------------------------------------------
    # Chamada genérica
    # ------------------------------------------------------------------

    async def call(
        self,
        *,
        module: str,
        endpoint: str,
        call_name: str,
        param: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Faz uma chamada genérica à API Omie.

        Monta o envelope com app_key + app_secret e POSTa em
        `${base_url}/${module}/${endpoint}/`.

        Args:
            module: ex. `"geral"`, `"financas"`.
            endpoint: ex. `"clientes"`, `"contacorrente"`, `"extrato"`.
            call_name: nome do método Omie, ex. `"ListarClientes"`.
            param: dict do parâmetro (será embrulhado em lista pela API).
            timeout_seconds: override do timeout default desta chamada
                específica. Útil pra endpoints conhecidos por demorar mais
                (ex.: `ListarExtrato` sem paginação). Se omitido, usa o
                `OMIE_TIMEOUT_SECONDS` do `Settings`.

        Returns:
            Body parseado como dict (com `faultstring` removido — se houvesse,
            uma exceção foi levantada).

        Raises:
            OmieAuthError: faultstring relacionado a credenciais.
            OmieFaultError: faultstring genérico (não retryable).
            OmieTimeoutError: timeout após retries esgotarem.
        """
        url = f"{self._base_url}/{module}/{endpoint}/"
        body = {
            "call": call_name,
            "app_key": self._credentials.app_key.get_secret_value(),
            "app_secret": self._credentials.app_secret.get_secret_value(),
            "param": [param],
        }
        effective_timeout = timeout_seconds if timeout_seconds is not None else self._timeout

        # Budget de retry calibrado pra suportar o rate limit Omie:
        #   - 5 tentativas com backoff 1s/2s/4s/8s/16s totalizam ~30s antes
        #     de desistir. Foi calibrado contra o `1880 - Já existe uma
        #     requisição` + `6 - Consumo redundante (aguarde 58s)`: a janela
        #     anterior (3 tentativas, max 8s) esgotava em ~7s e ainda dentro
        #     do cooldown da Omie. 30s dão margem pro slot paralelo liberar.
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(5),
                wait=wait_exponential(multiplier=1, min=1, max=16),
                retry=retry_if_exception_type((httpx.TimeoutException, _RetryableHttpError)),
                reraise=True,
            ):
                with attempt:
                    return await self._do_call(
                        url, body, module, endpoint, call_name, effective_timeout
                    )
        except httpx.TimeoutException as exc:
            raise OmieTimeoutError(
                f"Timeout após retries em {call_name} ({module}/{endpoint})",
            ) from exc
        except _RetryableHttpError as exc:
            # 5xx persistente após esgotar retries — Tenacity reraise=True relança
            # a exceção original (não a RetryError). Usa `OmieServerError` em vez
            # de `OmieTimeoutError`: a Omie respondeu, só com 5xx; chamar isso de
            # "timeout" no log e na UI engana o oncall (caso real em 19/05/2026).
            raise OmieServerError(
                f"Erro 5xx persistente em {call_name} ({module}/{endpoint})",
            ) from exc
        except RetryError as exc:  # pragma: no cover  -- fallback defensivo
            raise OmieTimeoutError(
                f"Falha persistente em {call_name} ({module}/{endpoint})",
            ) from exc

        # Inalcançável — Tenacity sempre retorna ou levanta. Pylint não enxerga.
        raise RuntimeError("unreachable")  # pragma: no cover

    async def _do_call(
        self,
        url: str,
        body: dict[str, Any],
        module: str,
        endpoint: str,
        call_name: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """Executa a request real. Encapsulada para Tenacity wrappar."""
        started = time.monotonic()

        try:
            # Per-request timeout — sobrescreve o timeout do AsyncClient quando
            # endpoint precisa de janela diferente (ex.: ListarExtrato).
            response = await self._http.post(url, json=body, timeout=timeout_seconds)
        except httpx.TimeoutException:
            log.warning(
                "omie_call_timeout",
                module=module,
                endpoint=endpoint,
                call=call_name,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            raise

        duration_ms = round((time.monotonic() - started) * 1000)

        # 5xx ambíguo no Omie: pode ser infra (retryable) OU erro de aplicação
        # que o Omie sinaliza no header `OmieAPI-Error`. Estratégia:
        #   - Header presente + código em `_RETRYABLE_OMIE_API_ERROR_PREFIXES`
        #     (`1880` "já em execução", `6` "consumo redundante") → retryable
        #     com backoff (Tenacity).
        #   - Header presente com qualquer outro código (ex: `5001` "tag
        #     inválida") → erro permanente; propaga a mensagem do header.
        #   - Sem header → infra Omie genuína (retryable).
        if 500 <= response.status_code < 600:
            omie_api_error = response.headers.get("OmieAPI-Error")
            if omie_api_error:
                is_retryable = any(
                    omie_api_error.startswith(prefix)
                    for prefix in _RETRYABLE_OMIE_API_ERROR_PREFIXES
                )
                if is_retryable:
                    # Quando o erro vem com "Aguarde N segundos" (código `6`),
                    # respeitamos esse tempo antes de propagar pro Tenacity.
                    # O Omie SOMA cada retry feito dentro do cooldown ativo
                    # (escala 1880 → 6/58s → 6/54s → ...) e mais retries
                    # cedo só prolongam a punição. Caso real Austral
                    # 20/05/2026: 5 retries em 16s não bastam, mas dormir
                    # ~60s e tentar 1x mais funciona.
                    cooldown_match = _REDUNDANT_WAIT_PATTERN.search(omie_api_error)
                    if cooldown_match is not None:
                        cooldown_seconds = min(
                            float(int(cooldown_match.group(1)) + 1),
                            _REDUNDANT_MAX_SLEEP_SECONDS,
                        )
                        log.warning(
                            "omie_call_redundant_cooldown_sleep",
                            module=module,
                            endpoint=endpoint,
                            call=call_name,
                            requested_wait_seconds=int(cooldown_match.group(1)),
                            effective_sleep_seconds=cooldown_seconds,
                            omie_api_error=omie_api_error,
                        )
                        await asyncio.sleep(cooldown_seconds)
                    log.warning(
                        "omie_call_5xx_retryable",
                        module=module,
                        endpoint=endpoint,
                        call=call_name,
                        status=response.status_code,
                        omie_api_error=omie_api_error,
                        duration_ms=duration_ms,
                    )
                    raise _RetryableHttpError(
                        f"5xx retryable em {call_name} ({module}/{endpoint}): {omie_api_error}"
                    )
                log.warning(
                    "omie_call_5xx_permanent",
                    module=module,
                    endpoint=endpoint,
                    call=call_name,
                    status=response.status_code,
                    omie_api_error=omie_api_error,
                    duration_ms=duration_ms,
                )
                raise OmieFaultError(
                    f"5xx permanente em {call_name} ({module}/{endpoint}): {omie_api_error}",
                    user_message=f"Erro ao acessar o Omie: {omie_api_error}",
                    metadata={
                        "status": response.status_code,
                        "omie_api_error": omie_api_error,
                    },
                )
            log.warning(
                "omie_call_5xx",
                module=module,
                endpoint=endpoint,
                call=call_name,
                status=response.status_code,
                duration_ms=duration_ms,
            )
            raise _RetryableHttpError(f"HTTP {response.status_code} em {module}/{endpoint}")
        if response.status_code != 200:
            log.warning(
                "omie_call_unexpected_status",
                module=module,
                endpoint=endpoint,
                call=call_name,
                status=response.status_code,
                duration_ms=duration_ms,
            )
            raise OmieFaultError(
                f"Status inesperado {response.status_code} em {module}/{endpoint}",
            )

        try:
            data = response.json()
        except ValueError as exc:
            log.warning(
                "omie_call_invalid_json",
                module=module,
                endpoint=endpoint,
                call=call_name,
                duration_ms=duration_ms,
            )
            raise OmieFaultError(
                f"Resposta não-JSON do Omie em {module}/{endpoint}",
            ) from exc

        # Toda resposta 200 do Omie pode conter faultstring (Doc §6.3)
        fault = OmieFaultPayload.model_validate(data)
        if fault.fault_string:
            log.info(
                "omie_call_fault",
                module=module,
                endpoint=endpoint,
                call=call_name,
                fault_code=fault.fault_code,
                duration_ms=duration_ms,
            )
            self._raise_for_fault(fault, call_name)

        log.info(
            "omie_call_ok",
            module=module,
            endpoint=endpoint,
            call=call_name,
            duration_ms=duration_ms,
        )
        return dict(data)

    @staticmethod
    def _raise_for_fault(fault: OmieFaultPayload, call_name: str) -> None:
        """Mapeia faultstring/faultcode para a exceção correta (auth vs genérico).

        Checagem dupla (auditoria M-2): casa o faultcode contra o enum de
        códigos SOAP de auth E o faultstring contra os keywords humanos.
        Antes a v1 misturava ambos em `_AUTH_FAULT_KEYWORDS`, então um
        fault com `faultcode='SOAP-ENV:Client-101'` mas `faultstring` em
        português sem nenhuma keyword conhecida caía em `OmieFaultError`
        e o usuário via "Ocorreu um erro ao acessar o Omie" em vez de
        "Credenciais Omie inválidas".
        """
        message = fault.fault_string or "Erro Omie sem mensagem"
        normalized_string = message.lower()
        normalized_code = (fault.fault_code or "").lower()

        is_auth_by_code = normalized_code in _AUTH_FAULT_CODES
        is_auth_by_string = any(kw in normalized_string for kw in _AUTH_FAULT_STRING_KEYWORDS)

        if is_auth_by_code or is_auth_by_string:
            raise OmieAuthError(
                f"Auth fault em {call_name}: {message}",
                metadata={"fault_code": fault.fault_code},
            )
        raise OmieFaultError(
            f"Fault em {call_name}: {message}",
            user_message=f"Ocorreu um erro ao acessar o Omie: {message}",
            metadata={"fault_code": fault.fault_code},
        )

    # ------------------------------------------------------------------
    # Paginação genérica
    # ------------------------------------------------------------------

    async def _paginate(
        self,
        *,
        module: str,
        endpoint: str,
        call_name: str,
        list_key: str,
        extra_param: dict[str, Any] | None = None,
        page_size: int = 100,
        max_pages: int = 1000,
    ) -> AsyncIterator[dict[str, Any]]:
        """Helper de paginação para endpoints Omie que usam `pagina/registros_por_pagina`.

        Funciona para `ListarContasCorrentes` (`ListarContasCorrentes` — o
        Omie reusa o nome do método como chave da lista) e
        `ListarContasPagar/Receber` (`conta_pagar_cadastro` /
        `conta_receber_cadastro`). O `list_key` indica qual chave do
        response contém os items.

        Critério de parada (em ordem de prioridade):
          1. `total_de_paginas` no envelope: se a página atual >= total,
             encerra. Auditoria M-3: economiza 1 request quando a última
             página vem cheia (ex: cliente com exatamente 100 contas).
          2. Fallback heurístico `len(items) < page_size`: cobre o caso
             do Omie não devolver `total_de_paginas` (legado/raro).
          3. `max_pages`: proteção defensiva contra loop infinito.

        Args:
            module/endpoint/call_name: como em `call()`.
            list_key: chave do dict de resposta com a lista.
            extra_param: parâmetros adicionais além de pagina/registros.
            page_size: tamanho da página (max 100 para a maioria dos endpoints).
            max_pages: proteção contra loop infinito.
        """
        extra = extra_param or {}
        for pagina in range(1, max_pages + 1):
            param = {"pagina": pagina, "registros_por_pagina": page_size, **extra}
            resp = await self.call(
                module=module, endpoint=endpoint, call_name=call_name, param=param
            )
            items: list[dict[str, Any]] = resp.get(list_key) or []
            for item in items:
                yield item

            # Critério primário: total_de_paginas declarado no envelope.
            total_paginas_raw = resp.get("total_de_paginas")
            if isinstance(total_paginas_raw, int) and total_paginas_raw > 0:
                if pagina >= total_paginas_raw:
                    return
                continue  # Omie disse que tem mais — segue pra próxima.

            # Fallback heurístico para envelopes sem `total_de_paginas`.
            if len(items) < page_size:
                return
        log.warning(
            "omie_paginate_max_pages_reached",
            module=module,
            endpoint=endpoint,
            call=call_name,
            max_pages=max_pages,
        )

    # ------------------------------------------------------------------
    # Métodos tipados — endpoints utilizados pelo sistema
    # ------------------------------------------------------------------

    async def listar_clientes_minimal(self) -> dict[str, Any]:
        """Chamada mínima ao Omie usada para validar credenciais (S6 BACK 3.3).

        Retorna o dict raw — caller decide se considera "sucesso" pela ausência
        de exceção. Páginas de tamanho 1 para minimizar latência/custo.
        """
        return await self.call(
            module="geral",
            endpoint="clientes",
            call_name="ListarClientes",
            param={"pagina": 1, "registros_por_pagina": 1},
        )

    async def listar_contas_correntes(self) -> list[ContaCorrente]:
        """Lista todas as contas correntes do cliente, com paginação automática.

        Inclui contas tipo `CC` (corrente) e `CA` (cartão) — ambas conciliáveis.
        """
        items: list[ContaCorrente] = []
        async for raw in self._paginate(
            module="geral",
            endpoint="contacorrente",
            call_name="ListarContasCorrentes",
            list_key="ListarContasCorrentes",
            extra_param={"apenas_importado_api": "N"},
            page_size=100,
        ):
            items.append(ContaCorrente.model_validate(raw))
        return items

    async def listar_extrato(
        self,
        *,
        n_cod_cc: int,
        data_inicial: date,
        data_final: date,
    ) -> list[LancamentoExtrato]:
        """Lista lançamentos do extrato para uma conta no período.

        Doc oficial: https://app.omie.com.br/api/v1/financas/extrato/
        Envelope `eccListarExtratoResponse`, chave do array é
        `listaMovimentos` (NÃO `extrato`, como a doc interna v1 dizia).

        **Sem paginação documentada** — a doc oficial não declara `pagina`
        nem `total_de_paginas`. Assumimos response completa em uma chamada.
        Pra absorver clientes com muitos lançamentos no período, usamos um
        timeout próprio (`OMIE_TIMEOUT_EXTRATO_SECONDS`, default 60s) em
        vez do default global (15s) — auditoria A-3. Logamos `omie_extrato_size`
        pra criar telemetria; se virar gargalo, partir pra split por
        intervalos menores no caller.

        Args:
            n_cod_cc: ID da conta corrente (`nCodCC`).
            data_inicial: início do período (já com tolerância subtraída).
            data_final: fim do período (já com tolerância adicionada).
        """
        resp = await self.call(
            module="financas",
            endpoint="extrato",
            call_name="ListarExtrato",
            param={
                "nCodCC": n_cod_cc,
                "cCodIntCC": "",
                "dPeriodoInicial": data_inicial.strftime("%d/%m/%Y"),
                "dPeriodoFinal": data_final.strftime("%d/%m/%Y"),
            },
            timeout_seconds=self._settings.OMIE_TIMEOUT_EXTRATO_SECONDS,
        )
        raw_items: list[dict[str, Any]] = resp.get("listaMovimentos") or []
        # Omie inclui linhas-resumo de saldo (ex: "SALDO ANTERIOR",
        # "SALDO POSTERIOR") no `listaMovimentos`. Essas linhas NÃO são
        # lançamentos — vêm sem `nCodLancamento`, sem `cNatureza` e sem
        # `cSituacao`, com `nValorDocumento=0` e `cDesCliente="SALDO ..."`.
        # Se passarem pelo `model_validate`, viram 3 erros de "Field
        # required" e o job inteiro morre. Filtramos antes do parse —
        # caso real observado em 20/05/2026 com cliente Austral (extrato
        # de março/2026, 36 linhas no array, ~2 delas de saldo).
        lancamentos = [it for it in raw_items if it.get("nCodLancamento") is not None]
        summary_rows_skipped = len(raw_items) - len(lancamentos)
        # Sinal forte de bug: todos os items foram descartados pelo filtro.
        # Possibilidades:
        #   (a) Extrato realmente vazio com só linhas de saldo (conta nova
        #       ou Conta Aplicação no Omie, onde a movimentação diária é
        #       só atualização de `nSaldo`/`nSaldoPrev`).
        #   (b) Response usa um campo diferente de `nCodLancamento` pro ID
        #       (nome divergiu da doc oficial).
        # Loga as chaves DISTINTAS observadas + amostra das 3 primeiras
        # linhas (truncando texto a 30 chars; valores numéricos passam) —
        # a diferença entre (a) e (b) fica visível: se todas as 36 linhas
        # têm as mesmas chaves (`cDesCliente, dDataLancamento, nSaldo,
        # nSaldoPrev, nValorDocumento`) e os valores em `cDesCliente`
        # começam com "SALDO", é (a). Se aparecer alguma chave nova
        # entre as amostras, é (b).
        if not lancamentos and raw_items:
            distinct_keys: set[str] = set()
            for it in raw_items:
                distinct_keys.update(it.keys())
            sample = [
                {
                    k: (v[:30] + "…" if isinstance(v, str) and len(v) > 30 else v)
                    for k, v in raw_items[idx].items()
                }
                for idx in (0, min(len(raw_items) // 2, len(raw_items) - 1), len(raw_items) - 1)
                if idx < len(raw_items)
            ]
            log.warning(
                "omie_extrato_all_rows_skipped",
                n_cod_cc=n_cod_cc,
                raw_count=len(raw_items),
                distinct_keys=sorted(distinct_keys),
                sample=sample,
            )
        log.info(
            "omie_extrato_size",
            n_cod_cc=n_cod_cc,
            item_count=len(lancamentos),
            raw_count=len(raw_items),
            summary_rows_skipped=summary_rows_skipped,
            period_start=data_inicial.isoformat(),
            period_end=data_final.isoformat(),
        )
        return [LancamentoExtrato.model_validate(it) for it in lancamentos]

    async def listar_contas_pagar(
        self,
        *,
        conta_corrente_id: int,
        data_de: date,
        data_ate: date,
        status: OmieTituloStatus,
    ) -> list[TituloAPagarReceber]:
        """Lista contas a pagar com `status_titulo` filtrado.

        A doc oficial Omie indica que `filtrar_por_status` aceita CSV (ex.:
        `"AVENCER,ATRASADO"`). Por ora mantemos chamadas separadas por status
        para preservar a granularidade do log (uma falha em ATRASADO não
        invalida o batch de AVENCER) — pode virar otimização futura.
        """
        return await self._listar_titulos(
            endpoint="contapagar",
            call_name="ListarContasPagar",
            list_key="conta_pagar_cadastro",
            conta_corrente_id=conta_corrente_id,
            data_de=data_de,
            data_ate=data_ate,
            status=status,
        )

    async def listar_contas_receber(
        self,
        *,
        conta_corrente_id: int,
        data_de: date,
        data_ate: date,
        status: OmieTituloStatus,
    ) -> list[TituloAPagarReceber]:
        """Lista contas a receber com `status_titulo` filtrado. Estrutura igual
        a `listar_contas_pagar`, mas envelope usa chave própria."""
        return await self._listar_titulos(
            endpoint="contareceber",
            call_name="ListarContasReceber",
            list_key="conta_receber_cadastro",
            conta_corrente_id=conta_corrente_id,
            data_de=data_de,
            data_ate=data_ate,
            status=status,
        )

    async def _listar_titulos(
        self,
        *,
        endpoint: str,
        call_name: str,
        list_key: str,
        conta_corrente_id: int,
        data_de: date,
        data_ate: date,
        status: OmieTituloStatus,
    ) -> list[TituloAPagarReceber]:
        """Implementação compartilhada entre `listar_contas_pagar` e `_receber`.

        O nome do filtro de conta corrente é `filtrar_conta_corrente` (sem
        `por_`) — vimos em prod que `filtrar_por_conta_corrente` faz a Omie
        responder 5001 "Tag não faz parte da estrutura do tipo complexo".
        """
        extra = {
            "filtrar_por_data_de": data_de.strftime("%d/%m/%Y"),
            "filtrar_por_data_ate": data_ate.strftime("%d/%m/%Y"),
            "filtrar_conta_corrente": conta_corrente_id,
            "filtrar_por_status": status.value,
        }
        items: list[TituloAPagarReceber] = []
        async for raw in self._paginate(
            module="financas",
            endpoint=endpoint,
            call_name=call_name,
            list_key=list_key,
            extra_param=extra,
            page_size=50,
        ):
            items.append(TituloAPagarReceber.model_validate(raw))
        return items
