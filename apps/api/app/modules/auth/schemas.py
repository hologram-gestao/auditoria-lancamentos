"""Schemas Pydantic do módulo de autenticação.

Request schemas validam input do cliente; response schemas controlam o que sai.
NUNCA retornar `password_hash`, `refresh_token` ou outros segredos no body —
tokens vão SEMPRE em cookies HttpOnly (Doc §7).
"""

from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


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
    """

    id: str  # UUID em string (evita parsing client-side)
    email: EmailStr
    name: str
    role: str  # "admin" | "manager"


class LoginResponse(BaseModel):
    """Body de resposta do login. Tokens são entregues em cookies, não aqui."""

    user: AuthenticatedUser


class RefreshResponse(BaseModel):
    """Body de POST /api/v1/auth/refresh — apenas confirma sucesso. Cookies foram atualizados."""

    user: AuthenticatedUser


class LogoutResponse(BaseModel):
    """Confirmação simples de logout."""

    success: bool = True
