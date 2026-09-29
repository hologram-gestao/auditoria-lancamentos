"""Serviço da conta do banco de cada conta de origem (Sprint 16, BACK 16.3 — R3).

Duas operações, sem HTTP:

    - `list_entries` — as contas de origem CONHECIDAS do cliente (as distintas da base
      de movimentos, as que já têm associação e o slot PADRÃO de cada tipo que pode ter
      linha sem conta), cada uma com a conta do banco associada (código + nome
      decifrado na leitura) ou PENDENTE;
    - `set_binding` — define ou TROCA a associação de uma conta de origem (ou do slot
      padrão), validando a conta pelo validador ÚNICO da 16.1 (outro cliente 404,
      sintética ou inativa 422). É configuração: upsert, nunca vigência.

A RESOLUÇÃO de qual conta do banco vale para uma linha NÃO mora aqui: é a função pura
`client_mapping.partida.resolve_bank_code`, e a materialização a aplica.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.exceptions import ClientClosedError
from app.core.logging import get_logger
from app.db.models import ProviderType
from app.modules.client_accounting_chart.service import AccountingChartService
from app.modules.client_source_accounts.repository import SourceAccountBindingRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.core.config import Settings
    from app.db.models import Client
    from app.modules.client_accounting_chart.service import AccountRef
    from app.modules.client_mapping.partida import BindingKey

log = get_logger(__name__)

#: Tipos de origem cujas linhas podem vir SEM conta de origem — ganham o slot padrão
#: mesmo antes do primeiro arquivo (o arquivo sem coluna de conta, caso da MSFG). Um
#: tipo que sempre traz conta (o Omie) só mostra o slot se alguém o associou ou se a
#: base tiver linha sem conta.
_TYPES_WITH_DEFAULT_SLOT = frozenset({ProviderType.ARQUIVO.value})


@dataclass(frozen=True, slots=True)
class SourceAccountEntry:
    """Uma conta de origem (ou o slot padrão) com a conta do banco ou pendente."""

    source_type: str
    source_account_id: str | None
    account: AccountRef | None

    @property
    def is_default(self) -> bool:
        return self.source_account_id is None

    @property
    def pending(self) -> bool:
        return self.account is None


class SourceAccountBindingService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings,
        repository: SourceAccountBindingRepository | None = None,
        chart: AccountingChartService | None = None,
    ) -> None:
        self._db = db
        self._repo = repository or SourceAccountBindingRepository(db)
        self._chart = chart or AccountingChartService(db, settings=settings)

    async def list_entries(self, client: Client) -> list[SourceAccountEntry]:
        bindings = await self._repo.list_bindings(client.id)
        keys: set[BindingKey] = await self._repo.movement_source_keys(client.id)
        keys |= {(b.source_type, b.source_account_id) for b in bindings}
        source_types = {k[0] for k in keys} | await self._repo.connection_types(client.id)
        keys |= {(t, None) for t in source_types if t in _TYPES_WITH_DEFAULT_SLOT}
        by_key = {(b.source_type, b.source_account_id): b for b in bindings}
        refs = await self._chart.account_refs(client, (b.accounting_account_id for b in bindings))
        ordered = sorted(keys, key=lambda k: (k[0], k[1] is not None, k[1] or ""))
        entries: list[SourceAccountEntry] = []
        for key in ordered:
            binding = by_key.get(key)
            entries.append(
                SourceAccountEntry(
                    source_type=key[0],
                    source_account_id=key[1],
                    account=refs.get(binding.accounting_account_id) if binding else None,
                )
            )
        return entries

    async def set_binding(
        self,
        client: Client,
        *,
        actor: CurrentUser,
        source_type: str,
        source_account_id: str | None,
        accounting_account_id: UUID,
    ) -> tuple[SourceAccountEntry, bool]:
        """Define ou troca a conta do banco de uma conta de origem. `(entrada, criou?)`."""
        if client.closed_at is not None:
            raise ClientClosedError(
                f"Cliente {client.id} está encerrado desde {client.closed_at.isoformat()}."
            )
        # O validador ÚNICO da 16.1: outro cliente → 404; sintética ou inativa → 422.
        await self._chart.require_postable_account(client.id, accounting_account_id)
        binding, created = await self._repo.upsert(
            client.id,
            source_type=source_type,
            source_account_id=source_account_id,
            accounting_account_id=accounting_account_id,
            author_id=UUID(actor.id),
        )
        log.info(
            "source_account_binding_set",
            client_id=str(client.id),
            binding_id=str(binding.id),
            default_slot=source_account_id is None,
            created=created,
        )
        refs = await self._chart.account_refs(client, [binding.accounting_account_id])
        return (
            SourceAccountEntry(
                source_type=binding.source_type,
                source_account_id=binding.source_account_id,
                account=refs.get(binding.accounting_account_id),
            ),
            created,
        )
