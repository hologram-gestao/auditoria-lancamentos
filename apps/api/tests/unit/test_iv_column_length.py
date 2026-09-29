"""Toda coluna de IV tem o MESMO tamanho, e é o do IV de verdade (86e3g9v0j).

O IV é `os.urandom(IV_SIZE_BYTES)` gravado em hex, então todo valor tem
`IV_SIZE_BYTES * 2` caracteres — 24 hoje. `IV_HEX_LENGTH` existe para isso, e 16
das 17 colunas `_iv` do sistema a usam.

A 17ª não usava: a migration da S15 (`b8ee8f368914`) declarou um literal `32` e
gravou `title_contexts.text_iv` com esse tamanho. Nada quebrou (24 cabe em 32),
mas o `alembic check` passou a acusar drift em toda entrega, e drift que aparece
sempre é drift que ninguém lê.

Este teste é o que impede a terceira ocorrência. Ele NÃO olha o banco: compara o
modelo com a constante, e a constante com o tamanho real do IV. O
`test_migrations.py` e o `alembic check` do CI cuidam do outro lado.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.core.crypto import IV_SIZE_BYTES
from app.db.base import Base
from app.db.models.client import IV_HEX_LENGTH


def test_iv_hex_length_e_o_dobro_do_iv_em_bytes() -> None:
    """Hex dobra o tamanho: mudar `IV_SIZE_BYTES` sem mudar a constante é bug."""
    assert IV_HEX_LENGTH == IV_SIZE_BYTES * 2


def test_toda_coluna_iv_do_modelo_usa_a_constante() -> None:
    """Varre o metadata inteiro: coluna `_iv` nova com literal reprova aqui."""
    divergentes: list[str] = []
    encontradas = 0
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not column.name.endswith("_iv"):
                continue
            encontradas += 1
            tipo = column.type
            assert isinstance(tipo, sa.String), f"{table.name}.{column.name} não é String"
            if tipo.length != IV_HEX_LENGTH:
                divergentes.append(f"{table.name}.{column.name}={tipo.length}")
    assert divergentes == [], (
        f"colunas de IV fora de IV_HEX_LENGTH={IV_HEX_LENGTH}: {divergentes}. "
        "Use a constante, nunca um literal — foi assim que a S15 nasceu com 32."
    )
    # Piso de sanidade: se a varredura parar de achar colunas (rename do sufixo,
    # metadata não carregado), o teste passaria vazio e não guardaria mais nada.
    assert encontradas >= 15, f"só {encontradas} colunas `_iv` varridas — a busca quebrou?"
