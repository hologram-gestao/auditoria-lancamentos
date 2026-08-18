"""Schemas do módulo omie_data (BACK 9.2)."""

from __future__ import annotations

from datetime import date as _date
from decimal import Decimal

from pydantic import BaseModel


class OmieLancamentoItem(BaseModel):
    """Item de GET /api/v1/omie/lancamentos.

    Inclui só campos relevantes pra UI da revisão: identificador,
    descrição, valor (com sinal), fornecedor/categoria, status.
    """

    omie_id: int
    transaction_date: _date
    description: str
    supplier: str | None
    category: str | None
    amount: Decimal
    status: str


class OmieLancamentoListResponse(BaseModel):
    data: list[OmieLancamentoItem]


class OmieCategoriaItem(BaseModel):
    """Item de GET /api/v1/omie/categorias (Sprint 7 / BACK 07.3).

    Só o par que o combobox de classificação precisa: `codigo` é o valor que
    vai em `cCodCateg` no lançamento, `descricao` é o que o operador lê.
    """

    codigo: str
    descricao: str


class OmieCategoriaListResponse(BaseModel):
    """Lista COMPLETA de categorias ativas do cliente — sem paginação.

    Escolha deliberada (a menor solução que atende R2): o consumidor é um
    combobox com busca, e a lista inteira já vem de um cache em memória por
    cliente. Paginar significaria uma ida ao servidor por tecla digitada,
    que é exatamente o que a task manda evitar. `total` existe para que a
    tela possa dizer "300 categorias" sem contar o array na mão.
    """

    data: list[OmieCategoriaItem]
    total: int
