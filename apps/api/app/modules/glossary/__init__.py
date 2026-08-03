"""Glossário por tenant (Sprint 6).

Vocabulário contábil do cliente final — categorias com uso, fornecedores típicos
e regras de auditoria — mantido pela equipe do cliente e injetado como bloco de
contexto na **qualificação** (`reconciliations/qualification/semantic.py`).

Camadas:
    - `repository.py` — SQL puro sobre `client_glossary_entries` + o contador
      `clients.glossary_version`. Todo SELECT leva o `client_id` no `WHERE`.
    - `service.py` — leitura decifrada (`load_glossary_snapshot`), consumida
      pela BACK 06.4. Recebe o `client_id` por ASSINATURA; nunca estado global.
    - `schemas.py` — DTOs em claro do snapshot.

Os endpoints HTTP são da BACK 06.3.
"""

from app.modules.glossary.schemas import GlossaryEntryPlain, GlossarySnapshot
from app.modules.glossary.service import (
    apply_entry_edit,
    build_entry,
    load_glossary_snapshot,
)

__all__ = [
    "GlossaryEntryPlain",
    "GlossarySnapshot",
    "apply_entry_edit",
    "build_entry",
    "load_glossary_snapshot",
]
