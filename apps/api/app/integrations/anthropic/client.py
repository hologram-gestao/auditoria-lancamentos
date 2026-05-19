"""Cliente async para a API da Anthropic — extração estruturada via tool use.

Princípios (CLAUDE.md §3 + Doc §12):
    - **Chave em env var.** Lida via `Settings.ANTHROPIC_API_KEY` (SecretStr).
      Construtor recusa chave vazia → `AnthropicAuthError`.
    - **NUNCA logar** prompt, resposta da IA ou chave. Logs trazem somente
      `model`, `duration_ms`, `bytes_in`, `transaction_count`, `attempt`.
    - **Timeout total = `ANTHROPIC_TIMEOUT_SECONDS`** (padrão 60 s).
    - **1 retry** em 5xx / timeout / connection error (checklist do BACK 7.1).
    - Após esgotar retries, mapeia para `AnthropicTimeoutError`.

Estilo espelha `OmieClient`: tenacity para retry com backoff exponencial,
exceção tipada por classe de erro, redactor do `app.core.logging` faz a
defesa em profundidade contra logs acidentais (qualquer chave contendo
`api_key`/`token`/`secret`/`authorization` vira `[REDACTED]`).
"""

from __future__ import annotations

import base64
import time
from typing import Any, Protocol, cast

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

from app.core.exceptions import (
    AnthropicAuthError,
    AnthropicParseError,
    AnthropicTimeoutError,
)
from app.core.logging import get_logger
from app.integrations.anthropic.prompts import SYSTEM_PROMPT, build_user_prompt
from app.integrations.anthropic.schemas import ExtractedStatement
from app.integrations.anthropic.tools import (
    EXTRACT_MOVEMENTS_TOOL,
    EXTRACT_MOVEMENTS_TOOL_NAME,
)

log = get_logger(__name__)

# Limite defensivo de tokens de saída — a soma de todos os transactions deve
# caber. Faturas/extratos típicos têm < 200 linhas; com folga generosa para
# faturas grandes mantemos 8k.
_MAX_OUTPUT_TOKENS = 8192


class _RetryableAnthropicError(Exception):
    """Erro transitório (5xx ou conexão) — sinaliza para Tenacity reagendar."""


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
        anthropic_client: _AsyncAnthropicLike | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._injected_client: _AsyncAnthropicLike | None = anthropic_client

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
                construtor (ex. `claude-sonnet-4-5`).

        Returns:
            `ExtractedStatement` validado.

        Raises:
            AnthropicAuthError: chave inválida/ausente, 401, 403.
            AnthropicTimeoutError: timeout esgotado após retry.
            AnthropicParseError: modelo não chamou a tool, ou tool input
                não passa na validação Pydantic.
        """
        client = self._get_client()
        user_content = self._build_user_content(content, mime_type, document_kind)
        system_blocks = self._build_system_blocks()
        chosen_model = model or self._model

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
                        model=chosen_model,
                        system_blocks=system_blocks,
                        user_content=user_content,
                        attempt_number=attempt.retry_state.attempt_number,
                    )
        except _RetryableAnthropicError as exc:
            # 5xx persistente após retry esgotar → mapeia para timeout (a UX
            # final é a mesma: "tente novamente"). Mensagem técnica fica em
            # `message`, não exposta ao usuário.
            log.warning(
                "anthropic_call_5xx_persistent",
                model=chosen_model,
                bytes_in=len(content),
                duration_ms=round((time.monotonic() - started) * 1000),
            )
            raise AnthropicTimeoutError(
                "Erro 5xx persistente da Anthropic após retry.",
            ) from exc
        except RetryError as exc:  # pragma: no cover  -- defensivo
            raise AnthropicTimeoutError(
                "Falha persistente ao chamar a Anthropic.",
            ) from exc

        duration_ms = round((time.monotonic() - started) * 1000)
        statement = self._extract_tool_payload(message)

        log.info(
            "anthropic_extract_ok",
            model=chosen_model,
            duration_ms=duration_ms,
            bytes_in=len(content),
            transaction_count=len(statement.transactions),
        )
        return statement

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
            encoded = base64.b64encode(content).decode("ascii")
            blocks.append(
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": encoded,
                    },
                }
            )
        else:
            text = self._decode_text(content)
            blocks.append({"type": "text", "text": text})

        blocks.append({"type": "text", "text": build_user_prompt(document_kind)})
        return blocks

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
        attempt_number: int,
    ) -> Any:
        """Faz a chamada concreta ao SDK e mapeia exceções."""
        try:
            return await client.messages.create(
                model=model,
                max_tokens=_MAX_OUTPUT_TOKENS,
                system=system_blocks,
                tools=[EXTRACT_MOVEMENTS_TOOL],
                tool_choice={"type": "tool", "name": EXTRACT_MOVEMENTS_TOOL_NAME},
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
            if 500 <= status < 600:
                raise _RetryableAnthropicError(f"HTTP {status}") from exc
            # 4xx que não auth — request mal formado / model_not_found / etc.
            # Tratamos como parse error porque normalmente é problema do payload
            # (ex: arquivo grande demais → 413). Mensagem genérica ao usuário.
            raise AnthropicParseError(
                f"Anthropic retornou {status}.",
            ) from exc

    # ------------------------------------------------------------------
    # Extração e validação do tool_use
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_tool_payload(message: Any) -> ExtractedStatement:
        """Localiza o bloco `tool_use` esperado e valida via Pydantic.

        Edge cases (Doc §12.2 — Tratamento de erros do parsing):
            - Modelo respondeu free-text → `AnthropicParseError`.
            - Tool input com data PT-BR / valor inválido → `AnthropicParseError`.
            - `transactions` vazio → bloqueado pelo `min_length=1` no schema.
        """
        content_blocks: list[Any] = list(getattr(message, "content", []) or [])
        for block in content_blocks:
            block_type = getattr(block, "type", None)
            block_name = getattr(block, "name", None)
            if block_type == "tool_use" and block_name == EXTRACT_MOVEMENTS_TOOL_NAME:
                raw_input: Any = getattr(block, "input", None)
                if not isinstance(raw_input, dict):
                    raise AnthropicParseError(
                        "Tool use sem input dict.",
                    )
                try:
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

        # Modelo não emitiu o tool_use esperado.
        raise AnthropicParseError(
            "Modelo não chamou a tool extract_movements.",
        )
