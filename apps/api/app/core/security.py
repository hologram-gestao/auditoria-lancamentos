"""Hash de senha (bcrypt) + emissão e validação de JWT.

Usado pelo módulo de autenticação (S3) e pela dependency `get_current_user`
(`app.core.dependencies`).

Padrões obrigatórios (CLAUDE.md §3):
    - bcrypt cost ≥ 12 (recomendação OWASP).
    - JWT HS256 com claims `sub`, `role`, `type`, `jti`, `iat`, `exp`.
    - Access token: 1 h. Refresh token: 7 dias. Configurável via Settings.
    - Token desativado em DB perde acesso na próxima request (validação em
      `dependencies.get_current_user`, S3+).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import bcrypt
from jose import ExpiredSignatureError, JWTError, jwt
from pydantic import BaseModel, ValidationError

from app.core.exceptions import TokenExpiredError, UnauthorizedError

if TYPE_CHECKING:
    from app.core.config import Settings

JWT_ALGORITHM = "HS256"
# Não são senhas — são literais do claim `type` do JWT. Ruff S105 falso positivo.
TOKEN_TYPE_ACCESS = "access"  # noqa: S105
TOKEN_TYPE_REFRESH = "refresh"  # noqa: S105


class TokenPayload(BaseModel):
    """Payload validado de um JWT decodificado."""

    sub: str  # user_id (UUID em string)
    role: str  # "admin" | "manager"
    type: str  # TOKEN_TYPE_ACCESS | TOKEN_TYPE_REFRESH
    jti: str  # token id único (UUID) — usado para revogação futura
    iat: int  # issued at (epoch)
    exp: int  # expira em (epoch)


# ----------------------------------------------------------------------
# bcrypt — uso direto (sem passlib, que tem incompatibilidade com bcrypt 5.x)
# ----------------------------------------------------------------------

# bcrypt internamente trunca senhas em 72 bytes. Aplicamos truncate explícito
# para evitar ValueError em bcrypt 5.x e mensagem clara para o caller.
BCRYPT_MAX_BYTES = 72


def _normalize_password(password: str) -> bytes:
    """Codifica senha em UTF-8 e trunca em 72 bytes (limite do bcrypt)."""
    return password.encode("utf-8")[:BCRYPT_MAX_BYTES]


def hash_password(password: str, *, cost: int = 12) -> str:
    """Gera hash bcrypt de `password`. Cost padrão 12 (OWASP).

    Senhas com mais de 72 bytes em UTF-8 são truncadas automaticamente —
    o bcrypt já faria isso internamente, mas é melhor controlar explicitamente.
    """
    salt = bcrypt.gensalt(rounds=cost)
    hashed = bcrypt.hashpw(_normalize_password(password), salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se `plain_password` bate com `hashed_password`.

    Retorna False (não levanta) se o hash estiver malformado — comportamento
    seguro: hash corrompido nunca deve permitir login.
    """
    try:
        return bcrypt.checkpw(
            _normalize_password(plain_password),
            hashed_password.encode("utf-8"),
        )
    except (ValueError, TypeError):
        return False


# ----------------------------------------------------------------------
# JWT
# ----------------------------------------------------------------------


def _create_token(
    *,
    subject: str,
    role: str,
    token_type: str,
    expires_delta: timedelta,
    secret: str,
) -> str:
    """Helper interno — emite JWT assinado com claims padronizados."""
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "role": role,
        "type": token_type,
        "jti": str(uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM)


def create_access_token(*, subject: str, role: str, settings: Settings) -> str:
    """Emite access token (validade `JWT_ACCESS_EXPIRE_MINUTES`, padrão 60)."""
    return _create_token(
        subject=subject,
        role=role,
        token_type=TOKEN_TYPE_ACCESS,
        expires_delta=timedelta(minutes=settings.JWT_ACCESS_EXPIRE_MINUTES),
        secret=settings.JWT_SECRET.get_secret_value(),
    )


def create_refresh_token(*, subject: str, role: str, settings: Settings) -> str:
    """Emite refresh token (validade `JWT_REFRESH_EXPIRE_DAYS`, padrão 7)."""
    return _create_token(
        subject=subject,
        role=role,
        token_type=TOKEN_TYPE_REFRESH,
        expires_delta=timedelta(days=settings.JWT_REFRESH_EXPIRE_DAYS),
        secret=settings.JWT_SECRET.get_secret_value(),
    )


def decode_token(
    token: str,
    settings: Settings,
    *,
    expected_type: str | None = None,
) -> TokenPayload:
    """Decodifica e valida um JWT. Retorna o payload tipado.

    Args:
        token: JWT em compact serialization.
        settings: configuração com JWT_SECRET.
        expected_type: se passado, valida que o claim `type` bate
                       (use `TOKEN_TYPE_ACCESS` ou `TOKEN_TYPE_REFRESH`).

    Raises:
        TokenExpiredError: assinatura válida mas `exp` no passado.
        UnauthorizedError: assinatura inválida, formato corrompido, claims
                           ausentes ou tipo inesperado.
    """
    secret = settings.JWT_SECRET.get_secret_value()
    try:
        raw = jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except ExpiredSignatureError as exc:
        raise TokenExpiredError("Token expirou.") from exc
    except JWTError as exc:
        raise UnauthorizedError("Token inválido.") from exc

    try:
        payload = TokenPayload.model_validate(raw)
    except ValidationError as exc:
        raise UnauthorizedError("Token com claims inválidos ou ausentes.") from exc

    if expected_type and payload.type != expected_type:
        raise UnauthorizedError(
            f"Tipo de token incorreto. Esperado '{expected_type}', recebido '{payload.type}'."
        )

    return payload
