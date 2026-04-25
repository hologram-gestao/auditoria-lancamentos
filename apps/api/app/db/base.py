"""DeclarativeBase do SQLAlchemy 2.0 com naming convention padronizada.

Naming convention é OBRIGATÓRIA — sem ela, o Alembic gera nomes auto inconsistentes
(`users_email_key` vs `ix_users_email`), e migrations entre dev/prod podem divergir.

Convenção segue a recomendação oficial do Alembic:
    https://alembic.sqlalchemy.org/en/latest/naming.html

Padrão dos identificadores gerados:
    ix_<table>_<col>           — índice
    uq_<table>_<col>           — unique constraint
    ck_<table>_<constraint>    — check constraint
    fk_<table>_<col>_<ref>     — foreign key
    pk_<table>                 — primary key
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base de todos os modelos ORM.

    Usar `from app.db.base import Base` e `class MeuModel(Base): __tablename__ = "..."`.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
