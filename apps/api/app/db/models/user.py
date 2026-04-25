"""Modelo User — usuários internos da Hologram (admin / manager).

Schema oficial: Docs/documentation/0. Schema do Banco de Dados e Cache-*.md §users.

CLAUDE.md §3 — RBAC:
    - admin: acesso total a todos os clientes.
    - manager: acesso apenas via `client_assignments`.

Senhas: hash bcrypt (cost ≥ 12) gerado por `app.core.security.hash_password`.
NUNCA armazenar senha em claro nem retornar `password_hash` em response.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models._mixins import TimestampMixin, UUIDPrimaryKeyMixin


class UserRole(StrEnum):
    """Perfis de usuário interno."""

    ADMIN = "admin"
    MANAGER = "manager"


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(150), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=UserRole.MANAGER.value,
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"
