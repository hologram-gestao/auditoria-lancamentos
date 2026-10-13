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


class OmieDepartamentoItem(BaseModel):
    """Item de GET /api/v1/clients/{id}/omie/departamentos (86e3n70q0, bloco B).

    `codigo` é o que vai em `distribuicao.cCodDep` do título; `descricao` é o que
    a pessoa lê, e vem de um cache em memória (nunca do banco, §4.5: o nome do
    departamento carrega nome de cliente). `estrutura` é a posição na árvore do
    Omie (`001.007.001`), para a tela indentar.
    """

    codigo: str
    descricao: str
    estrutura: str | None = None


class OmieDepartamentoListResponse(BaseModel):
    """Lista COMPLETA dos departamentos ativos — sem paginação, como as categorias:
    o consumidor é uma escolha com busca local sobre algumas centenas de itens."""

    data: list[OmieDepartamentoItem]
    total: int


class OmieFornecedorItem(BaseModel):
    """O cadastro do fornecedor de um CNPJ no Omie do cliente (86e3n70q0, bloco B).

    `codigo` é o `codigo_cliente_omie` que o título recebe em
    `codigo_cliente_fornecedor`. Os nomes vêm da Omie na hora e não persistem.
    """

    codigo: int
    razao_social: str | None = None
    nome_fantasia: str | None = None


class OmieFornecedorResponse(BaseModel):
    data: OmieFornecedorItem
