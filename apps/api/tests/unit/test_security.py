"""Testes de bcrypt + JWT (access/refresh tokens).

Critérios:
    - bcrypt: round-trip, cost respeitado, hashes diferentes para mesma senha.
    - JWT: emissão com claims corretos, decode válido, rejeição de
      assinatura inválida, expirado, claims ausentes, tipo errado.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from jose import jwt
from pydantic import SecretStr

from app.core.exceptions import TokenExpiredError, UnauthorizedError
from app.core.security import (
    JWT_ALGORITHM,
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

if TYPE_CHECKING:
    from app.core.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings stub apenas com os campos usados pelo módulo security."""
    from app.core.config import Settings

    # Bypass do validator hex porque ele aceita qualquer hex válido de 64 chars
    return Settings(
        DATABASE_URL="postgresql+psycopg://t:t@localhost:5432/t",
        OMIE_ENCRYPTION_KEY=SecretStr("a" * 64),
        JWT_SECRET=SecretStr("b" * 64),
    )  # type: ignore[call-arg]


# ----------------------------------------------------------------------
# bcrypt
# ----------------------------------------------------------------------


class TestPasswordHashing:
    def test_hash_and_verify(self) -> None:
        password = "MinhaSenh@Forte123"
        hashed = hash_password(password)
        assert verify_password(password, hashed)

    def test_wrong_password_fails(self) -> None:
        hashed = hash_password("correta")
        assert not verify_password("errada", hashed)

    def test_same_password_different_hashes(self) -> None:
        """bcrypt usa salt aleatório → hashes sempre diferentes."""
        h1 = hash_password("senha")
        h2 = hash_password("senha")
        assert h1 != h2
        assert verify_password("senha", h1)
        assert verify_password("senha", h2)

    def test_unicode_password(self) -> None:
        password = "Senh@_açaí_🔐"
        hashed = hash_password(password)
        assert verify_password(password, hashed)

    def test_cost_is_respected(self) -> None:
        """Cost embutido no hash deve refletir o que pedimos."""
        hashed = hash_password("x", cost=10)
        # Formato bcrypt: $2b$<cost>$<salt+hash>
        assert hashed.startswith("$2b$10$") or hashed.startswith("$2a$10$")


# ----------------------------------------------------------------------
# JWT
# ----------------------------------------------------------------------


class TestJwtCreate:
    def test_access_token_has_required_claims(self, settings: Settings) -> None:
        token = create_access_token(subject="user-123", role="admin", settings=settings)
        decoded = jwt.decode(
            token,
            settings.JWT_SECRET.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
        )
        assert decoded["sub"] == "user-123"
        assert decoded["role"] == "admin"
        assert decoded["type"] == TOKEN_TYPE_ACCESS
        assert "jti" in decoded
        assert "iat" in decoded
        assert "exp" in decoded
        assert decoded["exp"] > decoded["iat"]

    def test_refresh_token_has_correct_type(self, settings: Settings) -> None:
        token = create_refresh_token(subject="user-1", role="manager", settings=settings)
        decoded = jwt.decode(
            token,
            settings.JWT_SECRET.get_secret_value(),
            algorithms=[JWT_ALGORITHM],
        )
        assert decoded["type"] == TOKEN_TYPE_REFRESH

    def test_jti_is_unique(self, settings: Settings) -> None:
        tokens = {
            jwt.decode(
                create_access_token(subject="u", role="admin", settings=settings),
                settings.JWT_SECRET.get_secret_value(),
                algorithms=[JWT_ALGORITHM],
            )["jti"]
            for _ in range(50)
        }
        assert len(tokens) == 50


class TestJwtDecode:
    def test_decode_valid_access(self, settings: Settings) -> None:
        token = create_access_token(subject="user-1", role="admin", settings=settings)
        payload = decode_token(token, settings, expected_type=TOKEN_TYPE_ACCESS)
        assert payload.sub == "user-1"
        assert payload.role == "admin"
        assert payload.type == TOKEN_TYPE_ACCESS

    def test_decode_valid_refresh(self, settings: Settings) -> None:
        token = create_refresh_token(subject="user-2", role="manager", settings=settings)
        payload = decode_token(token, settings, expected_type=TOKEN_TYPE_REFRESH)
        assert payload.type == TOKEN_TYPE_REFRESH

    def test_invalid_signature_fails(self, settings: Settings) -> None:
        token = create_access_token(subject="u", role="admin", settings=settings)
        # Flipa último char (parte da assinatura)
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with pytest.raises(UnauthorizedError, match="inválido"):
            decode_token(tampered, settings)

    def test_garbage_token_fails(self, settings: Settings) -> None:
        with pytest.raises(UnauthorizedError):
            decode_token("not.a.jwt", settings)

    def test_expired_token_raises_token_expired(self, settings: Settings) -> None:
        # Cria token "no passado" mockando o relógio
        past = datetime.now(UTC) - timedelta(hours=2)
        with patch("app.core.security.datetime") as mock_dt:
            mock_dt.now.return_value = past
            mock_dt.UTC = UTC
            token = create_access_token(subject="u", role="admin", settings=settings)
        with pytest.raises(TokenExpiredError):
            decode_token(token, settings)

    def test_wrong_type_fails(self, settings: Settings) -> None:
        token = create_access_token(subject="u", role="admin", settings=settings)
        with pytest.raises(UnauthorizedError, match="Tipo de token incorreto"):
            decode_token(token, settings, expected_type=TOKEN_TYPE_REFRESH)

    def test_missing_claims_fails(self, settings: Settings) -> None:
        # JWT manualmente forjado SEM `role`, `type`, `jti`
        bad_payload = {
            "sub": "u",
            "exp": int((datetime.now(UTC) + timedelta(hours=1)).timestamp()),
            "iat": int(datetime.now(UTC).timestamp()),
        }
        token = jwt.encode(
            bad_payload,
            settings.JWT_SECRET.get_secret_value(),
            algorithm=JWT_ALGORITHM,
        )
        with pytest.raises(UnauthorizedError, match="claims inválidos"):
            decode_token(token, settings)
