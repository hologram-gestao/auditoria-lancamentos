"""Schemas Pydantic do módulo de conciliações.

S8 (BACK 6.2): response do check-duplicate.
S9 (BACK 7.1): response do parsing IA.

Convenção de envelope (CLAUDE.md §6): respostas de sucesso vão dentro de
`{"data": {...}}` para que o front trate todas as rotas com o mesmo
desempacotador.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.integrations.anthropic.schemas import ExtractedStatement


class DuplicateCheckPayload(BaseModel):
    """Conteúdo do envelope `{data: ...}` do check-duplicate."""

    duplicate: bool


class CheckDuplicateResponse(BaseModel):
    """Response de GET /api/v1/reconciliations/check-duplicate."""

    data: DuplicateCheckPayload


class ParseResponse(BaseModel):
    """Response de POST /api/v1/reconciliations/parse.

    Reusa o `ExtractedStatement` do módulo de integração — o shape exposto
    para o front é exatamente o que veio do tool use, sem renomeação. Se
    quisermos divergir entre contrato externo e interno depois, é só
    introduzir um adapter aqui.
    """

    data: ExtractedStatement
