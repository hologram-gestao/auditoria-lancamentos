"""Hierarquia de exceções customizadas da aplicação.

Toda exceção lançada pela camada de serviço/repositório deve herdar de `AppError`.
O exception handler global (em `app.main`) converte essas exceções no formato
de resposta padrão da API:

    {
      "error": {
        "code": "DUPLICATE_FILE",
        "message": "developer-facing message",
        "userMessage": "mensagem em PT-BR para o usuário final"
      }
    }

Códigos canônicos definidos na §9 do PLANO_IMPLEMENTACAO.md.
"""

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Códigos canônicos de erro da API. Centralizados — nunca usar strings mágicas."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"  # noqa: S105
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    DUPLICATE_FILE = "DUPLICATE_FILE"
    RATE_LIMITED = "RATE_LIMITED"
    OMIE_AUTH_ERROR = "OMIE_AUTH_ERROR"
    OMIE_TIMEOUT = "OMIE_TIMEOUT"
    OMIE_FAULT = "OMIE_FAULT"
    OMIE_SYNC_FAILED = "OMIE_SYNC_FAILED"
    PARSE_ERROR = "PARSE_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """Base de toda exceção customizada da aplicação.

    Os atributos de classe (`code`, `status_code`, `default_user_message`) podem
    ser sobrescritos por subclasses. A instância carrega `message` (developer-facing)
    e `user_message` (PT-BR para o usuário) — convertidos pelo handler global.

    Attributes:
        code: identificador canônico (ver `ErrorCode`).
        status_code: HTTP status que será retornado.
        default_user_message: fallback se o caller não passar `user_message`.
        message: mensagem técnica (logada, não exposta ao usuário).
        user_message: mensagem em PT-BR exibida ao usuário final.
        metadata: dados adicionais para logs/Sentry (não vão para a resposta HTTP).
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 500
    default_user_message: str = "Ocorreu um erro inesperado. Tente novamente."

    def __init__(
        self,
        message: str | None = None,
        *,
        user_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.message: str = message or self.default_user_message
        self.user_message: str = user_message or self.default_user_message
        self.metadata: dict[str, Any] = metadata or {}
        super().__init__(self.message)


# ----------------------------------------------------------------------
# 4xx — erros do cliente
# ----------------------------------------------------------------------


class ValidationAppError(AppError):
    """400 — dados de entrada inválidos."""

    code = ErrorCode.VALIDATION_ERROR
    status_code = 400
    default_user_message = "Dados inválidos. Verifique os campos enviados."


class UnauthorizedError(AppError):
    """401 — token ausente ou inválido."""

    code = ErrorCode.UNAUTHORIZED
    status_code = 401
    default_user_message = "Acesso não autorizado. Faça login novamente."


class TokenExpiredError(AppError):
    """401 — access token expirou (frontend deve tentar refresh)."""

    code = ErrorCode.TOKEN_EXPIRED
    status_code = 401
    default_user_message = "Sua sessão expirou. Faça login novamente."


class ForbiddenError(AppError):
    """403 — usuário autenticado mas sem permissão para o recurso."""

    code = ErrorCode.FORBIDDEN
    status_code = 403
    default_user_message = "Você não tem permissão para acessar este recurso."


class NotFoundError(AppError):
    """404 — recurso inexistente."""

    code = ErrorCode.NOT_FOUND
    status_code = 404
    default_user_message = "Recurso não encontrado."


class ConflictError(AppError):
    """409 — operação conflita com estado atual (genérica)."""

    code = ErrorCode.CONFLICT
    status_code = 409
    default_user_message = "Operação conflita com o estado atual."


class EmailAlreadyExistsError(ConflictError):
    """409 — tentou criar/atualizar usuário com e-mail já em uso."""

    default_user_message = "Este e-mail já está em uso."


class CannotDeactivateSelfError(AppError):
    """403 — admin tentou desativar a si mesmo (Doc §8.2)."""

    code = ErrorCode.FORBIDDEN
    status_code = 403
    default_user_message = "Você não pode desativar a si mesmo."


class ClientNotAccessibleError(ForbiddenError):
    """403 — manager tentou acessar cliente fora da sua carteira (S6 §3)."""

    default_user_message = "Você não tem acesso a este cliente."


class IncompleteCredentialsError(ValidationAppError):
    """400 — atualizar credenciais Omie exige App Key E App Secret juntos (S6 §3.4)."""

    default_user_message = (
        "Para atualizar as credenciais, envie tanto a App Key quanto o App Secret."
    )


class InvalidManagerError(ValidationAppError):
    """400 — assign para usuário inexistente, inativo ou não-manager (S6 §3.5)."""

    default_user_message = "O usuário selecionado não é um gerente ativo."


class DuplicateFileError(AppError):
    """409 — violação de idempotência (mesmo arquivo, conta e mês)."""

    code = ErrorCode.DUPLICATE_FILE
    status_code = 409
    default_user_message = (
        "Este arquivo já foi processado para esta conta e mês. "
        "Verifique se está enviando o extrato correto."
    )


class RateLimitedError(AppError):
    """429 — limite de requests excedido."""

    code = ErrorCode.RATE_LIMITED
    status_code = 429
    default_user_message = "Muitas tentativas. Aguarde um momento e tente novamente."


class ParseError(AppError):
    """422 — Claude API não conseguiu extrair movimentações do arquivo."""

    code = ErrorCode.PARSE_ERROR
    status_code = 422
    default_user_message = (
        "Não foi possível extrair movimentações do arquivo. "
        "Verifique se o arquivo está íntegro e sem proteção por senha."
    )


# ----------------------------------------------------------------------
# 5xx — falhas de integração / servidor
# ----------------------------------------------------------------------


class OmieAuthError(AppError):
    """502 — Omie recusou as credenciais (`faultstring` autenticação)."""

    code = ErrorCode.OMIE_AUTH_ERROR
    status_code = 502
    default_user_message = "Credenciais Omie inválidas. Verifique as configurações do cliente."


class OmieTimeoutError(AppError):
    """504 — Omie não respondeu em 15 s."""

    code = ErrorCode.OMIE_TIMEOUT
    status_code = 504
    default_user_message = "O Omie não respondeu no tempo esperado. Tente novamente."


class OmieFaultError(AppError):
    """502 — Omie retornou `faultstring` não relacionado a autenticação."""

    code = ErrorCode.OMIE_FAULT
    status_code = 502
    default_user_message = "Ocorreu um erro ao acessar o Omie."


class AccountsSyncError(AppError):
    """502 — falha ao sincronizar contas correntes com o Omie (S7).

    Wrapper sobre `OmieAuthError`/`OmieFaultError`/`OmieTimeoutError` quando o
    contexto da falha é especificamente "tentei popular o cache L1 e não
    consegui". Mantém um código próprio para o front diferenciar a mensagem
    ("Não foi possível sincronizar as contas no momento") da falha de
    test-connection.
    """

    code = ErrorCode.OMIE_SYNC_FAILED
    status_code = 502
    default_user_message = (
        "Não foi possível sincronizar as contas com o Omie no momento. Tente novamente."
    )


# ----------------------------------------------------------------------
# Serialização
# ----------------------------------------------------------------------


def to_error_response(exc: AppError) -> dict[str, dict[str, str]]:
    """Serializa `AppError` no formato de resposta padrão da API.

    Não inclui `metadata` — esses dados vão apenas para logs/Sentry.
    """
    return {
        "error": {
            "code": exc.code.value,
            "message": exc.message,
            "userMessage": exc.user_message,
        }
    }
