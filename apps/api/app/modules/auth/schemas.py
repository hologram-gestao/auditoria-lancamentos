"""Schemas Pydantic do módulo de autenticação.

Request schemas validam input do cliente; response schemas controlam o que sai.
NUNCA retornar `password_hash`, `refresh_token` ou outros segredos no body —
tokens vão SEMPRE em cookies HttpOnly (Doc §7).
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from app.db.models import UserRole, UserScope


class LoginRequest(BaseModel):
    """Body de POST /api/v1/auth/login."""

    email: EmailStr = Field(..., description="E-mail de login do usuário interno.")
    password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Senha em texto plano (verificada com bcrypt).",
    )


class AuthenticatedUser(BaseModel):
    """Dados públicos do usuário autenticado, enviados ao frontend para popular o store.

    NUNCA inclui `password_hash`, `created_at`, etc.
    `email` é `str` (não `EmailStr`) — validação estrita só na entrada
    (`LoginRequest`); na saída precisa serializar qualquer linha existente.

    Sprint 5 (R2): `role`/`scope` são os enums do backend (contrato é fonte
    única — o front faz o gating de UI a partir daqui, sem redigitar união de
    strings) e `client_id` diz a que tenant o usuário pertence (`None` para a
    equipe Hologram). Nenhum deles é a fonte da decisão de acesso: o servidor
    decide pela linha (`app.core.authz`).
    """

    id: str  # UUID em string (evita parsing client-side)
    email: str
    name: str
    role: UserRole
    scope: UserScope
    client_id: UUID | None = None


class LoginResponse(BaseModel):
    """Body de resposta do login. Tokens são entregues em cookies, não aqui."""

    user: AuthenticatedUser


class RefreshResponse(BaseModel):
    """Body de POST /api/v1/auth/refresh — apenas confirma sucesso. Cookies foram atualizados."""

    user: AuthenticatedUser


class LogoutResponse(BaseModel):
    """Confirmação simples de logout."""

    success: bool = True
