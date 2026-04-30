"""Schemas Pydantic do módulo de conciliações.

S8 (BACK 6.2): apenas o response do check-duplicate. Sessões posteriores
adicionam request/response de criação, listagem de entries, etc.

Convenção de envelope (CLAUDE.md §6): respostas de sucesso vão dentro de
`{"data": {...}}` para que o front trate todas as rotas com o mesmo
desempacotador.
"""

from __future__ import annotations

from pydantic import BaseModel


class DuplicateCheckPayload(BaseModel):
    """Conteúdo do envelope `{data: ...}` do check-duplicate."""

    duplicate: bool


class CheckDuplicateResponse(BaseModel):
    """Response de GET /api/v1/reconciliations/check-duplicate."""

    data: DuplicateCheckPayload
