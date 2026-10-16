"""Sugestão de fornecedor por linha da fatura, pela descrição da compra (86e3qhhrd).

A gaveta "Lançar no Omie" já sabe o que foi comprado ("ANYDESK*SOFTW",
"MICROSOFT 365"). Em vez de a pessoa ir atrás do CNPJ, o servidor cruza a
descrição de cada linha com o diretório de cadastros do Omie do cliente e devolve
os mais prováveis. A pessoa CONFIRMA: nada é aplicado sozinho, porque o título vai
para quem ela escolher e é dinheiro (§3.16).

**Por que no servidor, e não na tela.** A descrição é cifrada com a DEK do cliente
(§4.1) e nunca vai na URL; o servidor já a decifra em memória para o `cObs` do
lançamento. E a afinidade é a MESMA `supplier_affinity` que desempata o
cruzamento: uma cópia em TypeScript divergiria no primeiro ajuste.

**Tenant.** As linhas saem do `SELECT` com `session_id` da sessão já autorizada
(`require_session_access`); id de linha de outra sessão simplesmente não volta.
Nada persiste, nada é logado além de contagens.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from app.core.crypto_service import AAD_FILE_ENTRY_DESCRIPTION, field_locator
from app.core.logging import get_logger
from app.db.models import ReconciliationFileEntry
from app.modules.omie_data.payables_service import suggest_for_description
from app.modules.omie_data.schemas import OmieFornecedorSuggestion

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.crypto import ClientCipher
    from app.integrations.omie.schemas import ClienteOmie

log = get_logger(__name__)


async def suggest_suppliers_for_entries(
    db: AsyncSession,
    *,
    session_id: UUID,
    file_entry_ids: list[UUID],
    cipher: ClientCipher,
    cadastros: list[ClienteOmie],
) -> list[OmieFornecedorSuggestion]:
    """Uma sugestão por linha pedida que pertence à sessão, na ordem do pedido.

    Linha cuja descrição não decifra recebe `sugestoes` vazia e conta na métrica
    `decrypt_failed` do log (só o id): a sugestão é conveniência, a falha não
    derruba a gaveta.
    """
    wanted = list(dict.fromkeys(file_entry_ids))
    if not wanted:
        return []
    rows = (
        (
            await db.execute(
                select(ReconciliationFileEntry).where(
                    ReconciliationFileEntry.session_id == session_id,
                    ReconciliationFileEntry.id.in_(wanted),
                )
            )
        )
        .scalars()
        .all()
    )
    by_id = {entry.id: entry for entry in rows}
    result: list[OmieFornecedorSuggestion] = []
    decrypt_failed = 0
    with_suggestion = 0
    for entry_id in wanted:
        entry = by_id.get(entry_id)
        if entry is None:
            continue
        try:
            description = cipher.decrypt(
                entry.description_encrypted,
                entry.description_iv,
                field_locator(AAD_FILE_ENTRY_DESCRIPTION, entry.id),
            )
        except Exception:
            decrypt_failed += 1
            log.warning("omie_supplier_suggestion_decrypt_failed", file_entry_id=str(entry.id))
            description = None
        sugestoes = suggest_for_description(cadastros, description)
        if sugestoes:
            with_suggestion += 1
        result.append(OmieFornecedorSuggestion(file_entry_id=entry.id, sugestoes=sugestoes))
    log.info(
        "omie_supplier_suggestions",
        session_id=str(session_id),
        requested=len(wanted),
        lines=len(result),
        with_suggestion=with_suggestion,
        decrypt_failed=decrypt_failed,
    )
    return result
