"""Cliente async para a API da Anthropic — extração estruturada via tool use.

Princípios (CLAUDE.md §3 + Doc §12):
    - **Chave em env var.** Lida via `Settings.ANTHROPIC_API_KEY` (SecretStr).
      Construtor recusa chave vazia → `AnthropicAuthError`.
    - **NUNCA logar** prompt, resposta da IA ou chave. Logs trazem somente
      `model`, `duration_ms`, `bytes_in`, `transaction_count`, `attempt`.
    - **Timeout total = `ANTHROPIC_TIMEOUT_SECONDS`** (padrão 150 s).
    - **1 retry** em 5xx / 429 / connection error (checklist do BACK 7.1; o 429
      entrou com a extração em blocos paralelos, que o torna mais provável).
    - Após esgotar retries, mapeia para `AnthropicTimeoutError`.
    - **HTTP 400 por crédito esgotado** vira `AnthropicCreditError` e dispara o
      alerta de plantão `AlertCode.ANTHROPIC_CREDIT` (deduplicado por processo):
      antes caía no 4xx genérico e virava "arquivo inválido" (86e39yzxc).
    - **Três chamadas, um caminho.** `extract_movements` (a extração),
      `identify_document` (86e3ff8xd: banco e tipo de conta pela primeira página
      de um PDF dividido) e `call_tool` (86e3mpmcx: a qualificação) passam pelo
      MESMO retry e pelo MESMO mapeamento de erro (`_call_for_tool` →
      `_call_with_retry` → `_invoke`); mudam só tool, prompt e `max_tokens`.
    - **Tool sem `tool_choice` forçado** (86e3wg8et, claude-sonnet-5-5): `auto`,
      `strict` na definição e a exigência na mensagem do usuário. Resposta sem a
      tool tem UMA nova tentativa (`check_tool_response`); recusa
      (`stop_reason == "refusal"`) vira `AnthropicRefusalError` sem nova tentativa.

Estilo espelha `OmieClient`: tenacity para retry com backoff exponencial,
exceção tipada por classe de erro, redactor do `app.core.logging` faz a
defesa em profundidade contra logs acidentais (qualquer chave contendo
`api_key`/`token`/`secret`/`authorization` vira `[REDACTED]`).
"""

from __future__ import annotations

import base64
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from anthropic import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    AuthenticationError,
    PermissionDeniedError,
)
from pydantic import SecretStr, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.alerting import Alert, AlertCode, send_alert
from app.core.exceptions import (
    AnthropicAuthError,
    AnthropicCreditError,
    AnthropicParseError,
    AnthropicRefusalError,
    AnthropicTimeoutError,
)
from app.core.logging import get_logger
from app.integrations.anthropic.prompts import (
    IDENTIFY_SYSTEM_PROMPT,
    IDENTIFY_USER_PROMPT,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.integrations.anthropic.schemas import (
    DocumentIdentity,
    ExtractedStatement,
    ExtractedStatementBlock,
)
from app.integrations.anthropic.tools import (
    EXTRACT_MOVEMENTS_TOOL,
    EXTRACT_MOVEMENTS_TOOL_NAME,
    IDENTIFY_DOCUMENT_TOOL,
    IDENTIFY_DOCUMENT_TOOL_NAME,
)

if TYPE_CHECKING:
    from app.core.config import Settings

log = get_logger(__name__)

# Crédito esgotado: a Anthropic responde 400 `invalid_request_error` com este
# texto (comportamento observado — não há tipo de erro dedicado na documentação
# de erros). Casamos pelo texto porque é o único sinal disponível.
_CREDIT_EXHAUSTED_MARKER = "credit balance"

# Um alerta a cada janela, por processo: com blocos em paralelo, N chamadas
# falham de uma vez pelo MESMO motivo, e o plantão não precisa de N mensagens.
_CREDIT_ALERT_INTERVAL_SECONDS = 15 * 60
_credit_alert_last_at: float | None = None

# Teto de tokens de saída — a soma de TODOS os transactions extraídos deve caber
# aqui, senão o `tool_use` é truncado e a extração falha (Report #3: extrato de
# conta corrente com ~220 PIX exigia ~10-13k tokens e estourava o antigo 8192,
# devolvendo `AnthropicParseError` com mensagem genérica). 32k cobre extratos
# bem grandes (~480 linhas). É só um TETO — extrato pequeno não consome mais nem
# custa mais. Acima disso, `extract_movements` detecta `stop_reason=max_tokens`
# e devolve um erro claro ("divida o período") em vez de falha silenciosa.
#
# BACK 02.1 — o valor virou configurável (`Settings.ADL_PARSE_MAX_OUTPUT_TOKENS`,
# injetado no construtor); esta constante é só o default. O `lifespan` valida na
# subida que o configurado não excede o cap de saída do modelo (`model_limits`),
# em vez de descobrir isso num HTTP 400 da Anthropic no meio de uma conciliação.
_MAX_OUTPUT_TOKENS = 32768

# Teto de saída da IDENTIFICAÇÃO (86e3ff8xd, D2): a resposta são dois campos
# curtos (banco e tipo de conta). Pequeno de propósito — é o que torna a chamada
# barata e rápida (~5 s) antes dos blocos.
_IDENTIFY_MAX_OUTPUT_TOKENS = 256

# Pensamento no MÍNIMO nas três chamadas (extração, identificação e
# qualificação). O claude-sonnet-5-5 pensa por padrão (adaptativo), o que conta
# dentro do `max_tokens` (comeria os 256 da identificação) e é cobrado como
# saída; `{"type": "disabled"}` ele recusa com 400. `between_tools` é o
# desligado dele: sem pensamento estendido, aceito com effort `high` ou abaixo
# (o padrão do modelo é `high`, por isso nenhum `output_config.effort` vai junto)
# e sem nenhum outro campo no objeto (86e3wg8et).
THINKING_BETWEEN_TOOLS: Final[dict[str, str]] = {"type": "between_tools"}

# Só o claude-sonnet-5-5 aceita `between_tools`; o claude-sonnet-5 (a ponte de
# 86e3wg8ek, ainda na tabela de tetos) o recusa com 400 e desliga o pensamento
# com `disabled`. Esta tabela é o que deixa voltar para a ponte pela variável
# `ANTHROPIC_MODEL_DEFAULT`, sem redeploy. `tool_choice` auto e `strict` valem
# nos dois.
_THINKING_BY_MODEL: Final[dict[str, dict[str, str]]] = {
    "claude-sonnet-5": {"type": "disabled"},
}


def thinking_for(model: str) -> dict[str, str]:
    """O parâmetro `thinking` que desliga o pensamento estendido em `model`."""
    return _THINKING_BY_MODEL.get(model, THINKING_BETWEEN_TOOLS)


# O claude-sonnet-5-5 recusa `tool_choice` forçado (`tool`/`any`) com 400. Com
# `auto` a chamada à tool não é garantida: a mensagem do usuário a exige
# (`prompts.tool_call_required_note`) e, se a resposta vier sem ela, o MESMO
# pedido vai uma segunda vez. Duas respostas sem a tool viram o erro de sempre.
TOOL_CALL_AUTO: Final[dict[str, str]] = {"type": "auto"}
TOOL_CALL_ATTEMPTS: Final[int] = 2


def check_tool_response(message: Any, *, tool_name: str, model: str, attempt: int) -> bool:
    """Decide o que fazer com uma resposta que deveria trazer a tool `tool_name`.

    Fonte única para a extração, a identificação e a qualificação (86e3wg8et).

    Returns:
        `True` quando a resposta segue para o caller: trouxe o `tool_use` de
        `tool_name`, ou parou por `max_tokens` (cada caller já trata o
        truncamento; repetir o pedido truncaria de novo). `False` quando veio
        sem a tool e merece UMA nova tentativa; loga `anthropic_tool_not_called`
        com `stop_reason`, modelo e tentativa, nunca o texto da resposta (pode
        ecoar descrição de transação, §3.3 e §4.5).

    Raises:
        AnthropicRefusalError: `stop_reason == "refusal"`. Sem nova tentativa;
            o log leva só a `stop_details.category`, nunca o `explanation`.
    """
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == "refusal":
        category = getattr(getattr(message, "stop_details", None), "category", None)
        log.warning("anthropic_refusal", model=model, attempt=attempt, category=category)
        raise AnthropicRefusalError(f"Modelo recusou o pedido (categoria {category}).")
    if stop_reason == "max_tokens" or has_tool_use(message, tool_name):
        return True
    log.warning(
        "anthropic_tool_not_called",
        model=model,
        attempt=attempt,
        stop_reason=stop_reason,
    )
    return False


def has_tool_use(message: Any, tool_name: str) -> bool:
    """A resposta tem um bloco `tool_use` de `tool_name` com `input` dict?

    `input` que não é dict conta como sem a tool: merece a nova tentativa em vez
    de cair direto no erro do `_tool_input`.
    """
    return any(
        getattr(block, "type", None) == "tool_use"
        and getattr(block, "name", None) == tool_name
        and isinstance(getattr(block, "input", None), dict)
        for block in (getattr(message, "content", None) or [])
    )


async def call_until_tool(
    send: Callable[[], Awaitable[Any]], *, tool_name: str, model: str
) -> list[Any]:
    """Faz o pedido até `TOOL_CALL_ATTEMPTS` vezes, pela regra de `check_tool_response`.

    Fonte única do laço para a extração, a identificação e a qualificação.
    `send` faz UM pedido (o mesmo a cada vez). Devolve TODAS as respostas, na
    ordem: a última é a que vale; as anteriores servem para contar tokens.
    """
    messages: list[Any] = []
    for attempt in range(1, TOOL_CALL_ATTEMPTS + 1):
        message = await send()
        messages.append(message)
        if check_tool_response(message, tool_name=tool_name, model=model, attempt=attempt):
            break
    return messages


class _RetryableAnthropicError(Exception):
    """Erro transitório (5xx, 429 ou conexão) — sinaliza para Tenacity reagendar."""


def _is_credit_exhausted(exc: APIStatusError) -> bool:
    """400 cujo texto acusa saldo insuficiente na conta da Anthropic."""
    text = f"{exc.message} {exc.body}".lower()
    return exc.status_code == 400 and _CREDIT_EXHAUSTED_MARKER in text


class _MessageCreateLike(Protocol):
    """Protocolo mínimo do `client.messages` usado nos testes (mock-friendly)."""

    async def create(self, **kwargs: Any) -> Any: ...


class _AsyncAnthropicLike(Protocol):
    """Protocolo mínimo do `AsyncAnthropic` para injeção em testes."""

    @property
    def messages(self) -> _MessageCreateLike: ...


class AnthropicClient:
    """Cliente async wrapper sobre `AsyncAnthropic`.

    Uma instância é segura para reuso entre requests (o SDK gerencia o pool
    HTTP internamente via httpx). Por enquanto o app cria uma instância nova
    por request — refatoração para singleton sai junto com a injeção via
    Depends quando S10 (worker async) também precisar.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str,
        timeout: float,
        max_output_tokens: int = _MAX_OUTPUT_TOKENS,
        anthropic_client: _AsyncAnthropicLike | None = None,
        alert_settings: Settings | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._max_output_tokens = max_output_tokens
        self._injected_client: _AsyncAnthropicLike | None = anthropic_client
        # Só para o alerta de crédito esgotado. `None` (testes, scripts) = sem
        # alerta; o erro tipado sobe do mesmo jeito.
        self._alert_settings = alert_settings

    # ------------------------------------------------------------------
    # Lazy SDK client
    # ------------------------------------------------------------------

    def _get_client(self) -> _AsyncAnthropicLike:
        """Cria/retorna o `AsyncAnthropic` lazy.

        Lazy porque a chave pode estar vazia em ambiente dev quando esta
        rota não é exercitada — falha tardia mantém startup leve. Quando a
        rota é chamada com chave vazia, levantamos `AnthropicAuthError`
        ANTES de qualquer chamada de rede para não vazar `401` da Anthropic
        no log.
        """
        if self._injected_client is not None:
            return self._injected_client

        key_value = self._api_key.get_secret_value()
        if not key_value:
            raise AnthropicAuthError(
                "ANTHROPIC_API_KEY não configurada no ambiente.",
            )
        # max_retries=0: tenacity controla o retry para que o limite seja
        # uniforme com OmieClient (e logável por tentativa). `cast` para o
        # Protocol porque o SDK real tem assinaturas com overloads tipados que
        # não unificam com nosso `**kwargs: Any` — duck typing em runtime,
        # estrutural em tempo de checagem.
        return cast(
            "_AsyncAnthropicLike",
            AsyncAnthropic(
                api_key=key_value,
                timeout=self._timeout,
                max_retries=0,
            ),
        )

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    async def extract_movements(
        self,
        *,
        content: bytes,
        mime_type: str,
        document_kind: str,
        model: str | None = None,
        part: tuple[int, int] | None = None,
        identity: DocumentIdentity | None = None,
    ) -> ExtractedStatement:
        """Extrai `ExtractedStatement` chamando a Anthropic via tool use.

        Args:
            content: bytes brutos do PDF *ou* bytes do conteúdo já convertido
                para texto (CSV decodificado, XLSX renderizado).
            mime_type: discriminador. `application/pdf` → bloco `document`
                base64. Qualquer outro → bloco `text` (decodifica utf-8 com
                fallback latin-1).
            document_kind: descrição curta usada no user prompt. Ex:
                `"extrato bancário em PDF"`, `"fatura de cartão CSV"`.
            model: override opcional do modelo. `None` usa o default do
                construtor (`ANTHROPIC_MODEL_DEFAULT`, hoje `claude-sonnet-5-5`).
            part: `(índice, total)` quando `content` é um bloco de um arquivo
                dividido (`parse_chunking`, `parse_pdf_pages`); entra como nota
                no user prompt, e em modo bloco a validação aceita
                `transactions` vazio (`ExtractedStatementBlock`, D4).
            identity: banco e tipo de conta já identificados pela primeira
                página (`identify_document`); entra como nota no user prompt.

        Returns:
            `ExtractedStatement` validado (`ExtractedStatementBlock` em modo bloco).

        Raises:
            AnthropicAuthError: chave inválida/ausente, 401, 403.
            AnthropicTimeoutError: timeout esgotado após retry.
            AnthropicParseError: modelo não chamou a tool, ou tool input
                não passa na validação Pydantic.
        """
        user_content = self._build_user_content(
            content, mime_type, document_kind, part, identity=identity
        )
        chosen_model = model or self._model

        started = time.monotonic()
        messages = await self._call_for_tool(
            model=chosen_model,
            system_blocks=self._build_system_blocks(),
            user_content=user_content,
            tools=[EXTRACT_MOVEMENTS_TOOL],
            tool_name=EXTRACT_MOVEMENTS_TOOL_NAME,
            max_tokens=self._max_output_tokens,
            bytes_in=len(content),
        )
        message = messages[-1]
        duration_ms = round((time.monotonic() - started) * 1000)

        # Truncamento: se o modelo bateu no teto de tokens, o `tool_use` volta
        # cortado e o `_extract_tool_payload` falharia com a mensagem genérica
        # (que ainda fala em "proteção por senha" — confunde no caso de CSV).
        # Detectamos explicitamente e devolvemos um erro acionável (Report #3).
        if getattr(message, "stop_reason", None) == "max_tokens":
            log.warning(
                "anthropic_extract_truncated",
                model=chosen_model,
                bytes_in=len(content),
                duration_ms=duration_ms,
                max_tokens=self._max_output_tokens,
            )
            raise AnthropicParseError(
                "Extração truncada: stop_reason=max_tokens.",
                user_message=(
                    "O extrato tem movimentações demais para processar de uma vez. "
                    "Envie um período menor (por exemplo, divida o mês em duas "
                    "quinzenas) e tente novamente."
                ),
            )

        statement = self._extract_tool_payload(message, block=part is not None)

        log.info(
            "anthropic_extract_ok",
            model=chosen_model,
            duration_ms=duration_ms,
            bytes_in=len(content),
            transaction_count=len(statement.transactions),
        )
        return statement

    async def identify_document(self, content: bytes) -> DocumentIdentity:
        """Identifica banco e tipo de conta pela PRIMEIRA página de um PDF (D2).

        Chamada curta antes dos blocos de um PDF dividido (`parse_pdf_pages`):
        `document` base64 de uma página, tool `identify_document` exigida,
        `max_tokens` pequeno. Mesmo `_invoke` (retry, mapeamento de erro) da
        extração.

        Raises:
            AnthropicAuthError / AnthropicTimeoutError / AnthropicCreditError:
                como em `extract_movements`.
            AnthropicParseError: modelo não chamou a tool, ou o input não passa
                na validação.
        """
        user_content = [
            self._document_block(content),
            {"type": "text", "text": IDENTIFY_USER_PROMPT},
        ]
        started = time.monotonic()
        messages = await self._call_for_tool(
            model=self._model,
            system_blocks=[{"type": "text", "text": IDENTIFY_SYSTEM_PROMPT}],
            user_content=user_content,
            tools=[IDENTIFY_DOCUMENT_TOOL],
            tool_name=IDENTIFY_DOCUMENT_TOOL_NAME,
            max_tokens=_IDENTIFY_MAX_OUTPUT_TOKENS,
            bytes_in=len(content),
        )
        message = messages[-1]
        duration_ms = round((time.monotonic() - started) * 1000)
        if getattr(message, "stop_reason", None) == "max_tokens":
            raise AnthropicParseError("Identificação truncada: stop_reason=max_tokens.")

        raw_input = self._tool_input(message, IDENTIFY_DOCUMENT_TOOL_NAME)
        try:
            identity = DocumentIdentity.model_validate(raw_input)
        except ValidationError as exc:
            log.warning("anthropic_tool_validation_failed", error_count=len(exc.errors()))
            raise AnthropicParseError(
                f"Tool input inválido: {exc.errors()[0]['msg']}",
            ) from exc

        log.info(
            "anthropic_identify_ok",
            model=self._model,
            duration_ms=duration_ms,
            bytes_in=len(content),
        )
        return identity

    async def call_tool(
        self,
        *,
        system_blocks: list[dict[str, Any]],
        user_content: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_name: str,
        max_tokens: int,
    ) -> list[Any]:
        """Chamada pública com tool, no modelo do construtor (86e3mpmcx).

        É o caminho da qualificação (`qualification/semantic.py`): o MESMO
        `_call_for_tool` → `_call_with_retry` → `_invoke` da extração e da
        identificação, com o retry de 429/5xx/conexão, o mapeamento de timeout e
        de autenticação e o reconhecimento do crédito esgotado com o alerta de
        plantão. `thinking` (`thinking_for`) e `tool_choice` auto vêm do `_invoke`.

        Returns:
            TODAS as respostas, na ordem (`call_until_tool`): a última é a que
            vale; as anteriores servem para somar tokens.

        Raises:
            AnthropicAuthError / AnthropicTimeoutError / AnthropicCreditError /
            AnthropicParseError / AnthropicRefusalError: como em `extract_movements`.
        """
        return await self._call_for_tool(
            model=self._model,
            system_blocks=system_blocks,
            user_content=user_content,
            tools=tools,
            tool_name=tool_name,
            max_tokens=max_tokens,
            # Só para o log do 5xx persistente: o tamanho do texto enviado.
            bytes_in=sum(len(str(block.get("text", "")).encode()) for block in user_content),
        )

    async def _call_for_tool(
        self,
        *,
        model: str,
        system_blocks: list[dict[str, Any]],
        user_content: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_name: str,
        max_tokens: int,
        bytes_in: int,
    ) -> list[Any]:
        """Chamada que precisa da tool `tool_name`: até `TOOL_CALL_ATTEMPTS` pedidos.

        Com `tool_choice` auto a resposta pode vir sem a tool; o MESMO pedido vai
        de novo uma vez (`check_tool_response`). A segunda resposta sem a tool
        segue para o caller, que levanta o `AnthropicParseError` de sempre
        (`_tool_input`). Recusa levanta `AnthropicRefusalError` na primeira.
        Devolve todas as respostas; a extração e a identificação usam a última.
        """

        async def send() -> Any:
            return await self._call_with_retry(
                model=model,
                system_blocks=system_blocks,
                user_content=user_content,
                tools=tools,
                max_tokens=max_tokens,
                bytes_in=bytes_in,
            )

        return await call_until_tool(send, tool_name=tool_name, model=model)

    async def _call_with_retry(
        self,
        *,
        model: str,
        system_blocks: list[dict[str, Any]],
        user_content: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        bytes_in: int,
    ) -> Any:
        """1 chamada + 1 retry em erro transitório; o resto do mapeamento é do `_invoke`."""
        client = self._get_client()
        started = time.monotonic()
        message: Any
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(2),  # 1 chamada + 1 retry
                wait=wait_exponential(multiplier=1, min=1, max=4),
                retry=retry_if_exception_type(_RetryableAnthropicError),
                reraise=True,
            ):
                with attempt:
                    message = await self._invoke(
                        client=client,
                        model=model,
                        system_blocks=system_blocks,
                        user_content=user_content,
                        tools=tools,
                        max_tokens=max_tokens,
                        attempt_number=attempt.retry_state.attempt_number,
                    )
        except _RetryableAnthropicError as exc:
            # 5xx persistente após retry esgotar → mapeia para timeout (a UX
            # final é a mesma: "tente novamente"). Mensagem técnica fica em
            # `message`, não exposta ao usuário.
            log.warning(
                "anthropic_call_5xx_persistent",
                model=model,
                bytes_in=bytes_in,
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            raise AnthropicTimeoutError(
                "Erro 5xx persistente da Anthropic após retry.",
            ) from exc
        except RetryError as exc:  # pragma: no cover  -- defensivo
            raise AnthropicTimeoutError(
                "Falha persistente ao chamar a Anthropic.",
            ) from exc
        return message

    # ------------------------------------------------------------------
    # Construção de mensagens
    # ------------------------------------------------------------------

    def _build_system_blocks(self) -> list[dict[str, Any]]:
        """System prompt como bloco com `cache_control: ephemeral` (P1-008).

        O SYSTEM_PROMPT é imutável. Marcar como `ephemeral` ativa o prompt
        caching da Anthropic — após a 1ª chamada, calls subsequentes na
        janela de 5min reusam o cache do prompt sem reprocessar tokens.
        Reduz custo em ~90% no padrão "muitas conciliações na mesma janela".
        Ver: PLANO §6.2 #2.
        """
        return [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def _build_user_content(
        self,
        content: bytes,
        mime_type: str,
        document_kind: str,
        part: tuple[int, int] | None = None,
        *,
        identity: DocumentIdentity | None = None,
    ) -> list[dict[str, Any]]:
        """Constrói a lista de blocos de conteúdo do `user` message.

        - PDF: `document` block base64.
        - Outros mime_types: `text` block (caller é responsável por
          decodificar XLSX/XLS para texto antes; CSV vem cru).

        O bloco textual de instrução vem por último para que o modelo
        processe o documento primeiro (best practice da Anthropic).
        """
        blocks: list[dict[str, Any]] = []

        if mime_type == "application/pdf":
            blocks.append(self._document_block(content))
        else:
            text = self._decode_text(content)
            blocks.append({"type": "text", "text": text})

        identity_note = (identity.bank_name, identity.account_type) if identity else None
        blocks.append(
            {
                "type": "text",
                "text": build_user_prompt(document_kind, part=part, identity=identity_note),
            }
        )
        return blocks

    @staticmethod
    def _document_block(content: bytes) -> dict[str, Any]:
        """PDF como bloco `document` base64."""
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(content).decode("ascii"),
            },
        }

    @staticmethod
    def _decode_text(content: bytes) -> str:
        """Decodifica bytes para str. UTF-8 com fallback latin-1.

        Latin-1 é usado em alguns extratos exportados por sistemas legados;
        nunca falha (mapeia byte 1:1 para code point).
        """
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("latin-1", errors="replace")

    # ------------------------------------------------------------------
    # Invocação do SDK + tratamento de erros
    # ------------------------------------------------------------------

    async def _invoke(
        self,
        *,
        client: _AsyncAnthropicLike,
        model: str,
        system_blocks: list[dict[str, Any]],
        user_content: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_tokens: int,
        attempt_number: int,
    ) -> Any:
        """Faz a chamada concreta ao SDK e mapeia exceções."""
        try:
            return await client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_blocks,
                tools=tools,
                tool_choice=TOOL_CALL_AUTO,
                thinking=thinking_for(model),
                messages=[{"role": "user", "content": user_content}],
            )
        except APITimeoutError as exc:
            log.warning(
                "anthropic_call_timeout",
                model=model,
                attempt=attempt_number,
            )
            raise AnthropicTimeoutError(
                "Timeout na chamada à Anthropic.",
            ) from exc
        except (AuthenticationError, PermissionDeniedError) as exc:
            # Mensagem técnica vai pro log; user_message é genérica para não
            # vazar configuração interna do BPO.
            log.warning(
                "anthropic_call_auth_failed",
                model=model,
                attempt=attempt_number,
                status=getattr(exc, "status_code", None),
            )
            raise AnthropicAuthError(
                f"Anthropic recusou a chave (status {getattr(exc, 'status_code', '?')}).",
            ) from exc
        except APIConnectionError as exc:
            log.warning(
                "anthropic_call_connection_error",
                model=model,
                attempt=attempt_number,
            )
            raise _RetryableAnthropicError(str(exc)) from exc
        except APIStatusError as exc:
            status = exc.status_code
            log.warning(
                "anthropic_call_api_status_error",
                model=model,
                attempt=attempt_number,
                status=status,
            )
            if status == 429 or 500 <= status < 600:
                raise _RetryableAnthropicError(f"HTTP {status}") from exc
            if _is_credit_exhausted(exc):
                # Só o código sai no log — nunca o texto da Anthropic (texto
                # livre de terceiro; o log não é lugar para ele).
                log.warning("anthropic_call_credit_exhausted", model=model, attempt=attempt_number)
                await self._alert_credit_exhausted()
                raise AnthropicCreditError(
                    "Anthropic recusou por crédito insuficiente (HTTP 400).",
                ) from exc
            # 4xx que não auth — request mal formado / model_not_found / etc.
            # Tratamos como parse error porque normalmente é problema do payload
            # (ex: arquivo grande demais → 413). Mensagem genérica ao usuário.
            raise AnthropicParseError(
                f"Anthropic retornou {status}.",
            ) from exc

    async def _alert_credit_exhausted(self) -> None:
        """Alerta de plantão, uma vez por janela e por processo. Nunca levanta."""
        # Estado de módulo de propósito: a dedup é por PROCESSO, não por instância
        # (o app cria um client por request).
        global _credit_alert_last_at
        if self._alert_settings is None:
            return
        now = time.monotonic()
        if (
            _credit_alert_last_at is not None
            and now - _credit_alert_last_at < _CREDIT_ALERT_INTERVAL_SECONDS
        ):
            return
        _credit_alert_last_at = now
        await send_alert(
            Alert(
                code=AlertCode.ANTHROPIC_CREDIT,
                message=(
                    "Conta da Anthropic sem crédito: o envio de arquivos falha e a "
                    "qualificação das conciliações não roda até recarregar."
                ),
            ),
            self._alert_settings,
        )

    # ------------------------------------------------------------------
    # Extração e validação do tool_use
    # ------------------------------------------------------------------

    @staticmethod
    def _tool_input(message: Any, tool_name: str) -> dict[str, Any]:
        """Localiza o bloco `tool_use` de `tool_name` e devolve o `input` dict.

        Raises:
            AnthropicParseError: modelo respondeu free-text, chamou outra tool
                ou o input não é dict.
        """
        content_blocks: list[Any] = list(getattr(message, "content", []) or [])
        for block in content_blocks:
            block_type = getattr(block, "type", None)
            block_name = getattr(block, "name", None)
            if block_type == "tool_use" and block_name == tool_name:
                raw_input: Any = getattr(block, "input", None)
                if not isinstance(raw_input, dict):
                    raise AnthropicParseError(
                        "Tool use sem input dict.",
                    )
                return raw_input

        # Modelo não emitiu o tool_use esperado.
        raise AnthropicParseError(
            f"Modelo não chamou a tool {tool_name}.",
        )

    @classmethod
    def _extract_tool_payload(cls, message: Any, *, block: bool = False) -> ExtractedStatement:
        """Localiza o bloco `tool_use` da extração e valida via Pydantic.

        Edge cases (Doc §12.2 — Tratamento de erros do parsing):
            - Modelo respondeu free-text → `AnthropicParseError`.
            - Tool input com data PT-BR / valor inválido → `AnthropicParseError`.
            - `transactions` vazio → bloqueado pelo `min_length=1` no schema do
              arquivo inteiro; aceito em modo bloco (`block=True`, D4), onde o
              total é conferido na junção.
        """
        raw_input = cls._tool_input(message, EXTRACT_MOVEMENTS_TOOL_NAME)
        try:
            if block:
                return ExtractedStatementBlock.model_validate(raw_input)
            return ExtractedStatement.model_validate(raw_input)
        except ValidationError as exc:
            # `errors()` é estruturado e seguro para log — nenhum
            # valor financeiro completo, só caminhos e tipos. Nunca
            # logamos `raw_input` porque pode conter conteúdo
            # extraído do extrato.
            log.warning(
                "anthropic_tool_validation_failed",
                error_count=len(exc.errors()),
            )
            raise AnthropicParseError(
                f"Tool input inválido: {exc.errors()[0]['msg']}",
            ) from exc
