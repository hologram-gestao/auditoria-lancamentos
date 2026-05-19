"""Cache L1 de contas correntes Omie — Doc §5.2.

Responsabilidades:
    - `get_or_sync(client)`: respeita TTL de 24 h. Cache hit → não chama Omie.
    - `force_sync(client)`: ignora TTL, sempre chama Omie.

Estratégia de upsert (DELETE + INSERT em transação): ver docstring de
`ClientRepository.replace_accounts_cache`. Resumo: clean-slate por cliente
mantém o cache espelhando o Omie (contas removidas lá somem aqui também).

CLAUDE.md §3 (segurança):
    - Não loga nem retorna credenciais Omie.
    - O `OmieClient` é construído via `omie_factory.build_omie_client`,
      que isola a descriptografia.

Testabilidade:
    Aceita um `OmieClient` injetado (`omie_client_override`) — o factory
    `_get_omie_client()` é só pra produção, em teste o caller passa um cliente
    já configurado com `respx`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.core.exceptions import (
    AccountsSyncError,
    OmieAuthError,
    OmieFaultError,
    OmieTimeoutError,
)
from app.core.logging import get_logger
from app.db.models import Client, OmieAccountCache
from app.integrations.omie.schemas import ContaCorrente
from app.modules.clients.omie_factory import build_omie_client
from app.modules.clients.repository import ClientRepository

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.integrations.omie.client import OmieClient

log = get_logger(__name__)

CACHE_TTL = timedelta(hours=24)


class OmieAccountsCacheService:
    """Service que coordena cache L1 + chamada ao Omie.

    Usar uma instância por request (mesma sessão DB). O `omie_client_override`
    é só pra testes — produção sempre constrói o cliente do factory.
    """

    def __init__(
        self,
        repository: ClientRepository,
        settings: Settings,
        *,
        omie_client_override: OmieClient | None = None,
    ) -> None:
        self._repo = repository
        self._settings = settings
        self._omie_client_override = omie_client_override

    async def get_or_sync(
        self, client: Client
    ) -> tuple[Sequence[OmieAccountCache], datetime | None]:
        """Retorna o cache; sincroniza com o Omie se TTL expirado ou nunca rodou.

        O TTL é decidido por `client.omie_accounts_synced_at` (coluna no
        Client) — NÃO por MAX(omie_accounts_cache.synced_at). Esta separação
        garante que clientes cujo Omie devolveu lista vazia também respeitam
        o TTL de 24 h: o sync é registrado mesmo sem nenhuma linha no cache.

        Returns:
            Tupla `(rows, synced_at)`. `synced_at=None` apenas se NUNCA
            houve sync para esse cliente (cliente recém-criado, primeiro acesso).
        """
        latest = client.omie_accounts_synced_at

        if latest is not None and self._is_fresh(latest):
            log.info(
                "accounts_cache_hit",
                client_id=str(client.id),
                synced_at=latest.isoformat(),
            )
            rows = await self._repo.get_accounts_cache(client.id)
            return rows, latest

        log.info(
            "accounts_cache_miss",
            client_id=str(client.id),
            had_sync=latest is not None,
            stale_since=latest.isoformat() if latest else None,
        )
        return await self._sync(client)

    async def force_sync(self, client: Client) -> tuple[Sequence[OmieAccountCache], datetime]:
        """Força sincronização imediata, mesmo com cache fresco.

        Sempre chama o Omie e atualiza `synced_at` para `now()`. Retorno
        garante `synced_at` non-None (a chamada Omie acabou de acontecer).
        """
        log.info("accounts_cache_force_sync", client_id=str(client.id))
        rows, synced_at = await self._sync(client)
        if synced_at is None:  # pragma: no cover  -- defensivo
            raise AccountsSyncError(
                "Sync forçado retornou synced_at=None — invariante violada.",
            )
        return rows, synced_at

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _is_fresh(synced_at: datetime) -> bool:
        """Retorna True se `synced_at` está dentro do TTL.

        O `synced_at` vem do Postgres com timezone — comparamos com `now(UTC)`
        pra evitar comparação naive/aware (que TypeError em Python).
        """
        return datetime.now(UTC) - synced_at <= CACHE_TTL

    async def _sync(self, client: Client) -> tuple[Sequence[OmieAccountCache], datetime | None]:
        """Faz a chamada Omie + replace_cache. Wrapper de erro Omie."""
        contas = await self._fetch_omie_accounts(client)
        items = [_to_cache_row(client.id, c) for c in contas]
        # `replace_accounts_cache` atualiza `client.omie_accounts_synced_at`
        # mesmo quando `items` é vazio — TTL passa a respeitar clientes Omie
        # sem contas correntes (caso real do cliente Quial).
        synced_at = await self._repo.replace_accounts_cache(client, items)

        if not items:
            log.info("accounts_sync_empty", client_id=str(client.id))
            return [], synced_at

        log.info(
            "accounts_sync_ok",
            client_id=str(client.id),
            account_count=len(items),
        )
        # Re-busca pra retornar a Sequence ordenada (ORDER BY name) que o
        # repository usa nos demais reads.
        rows = await self._repo.get_accounts_cache(client.id)
        return rows, synced_at

    async def _fetch_omie_accounts(self, client: Client) -> list[ContaCorrente]:
        """Chama Omie.listar_contas_correntes; converte exceções Omie em
        AccountsSyncError com user_message específico."""
        omie_client = self._omie_client_override or build_omie_client(client, self._settings)
        owns_client = self._omie_client_override is None
        try:
            return await omie_client.listar_contas_correntes()
        except OmieAuthError as exc:
            raise AccountsSyncError(
                f"Auth falhou ao sincronizar contas do cliente {client.id}: {exc.message}",
                user_message=(
                    "As credenciais Omie cadastradas estão inválidas. "
                    "Atualize-as para sincronizar as contas."
                ),
                metadata={"client_id": str(client.id), "cause": "auth"},
            ) from exc
        except OmieTimeoutError as exc:
            raise AccountsSyncError(
                f"Timeout ao sincronizar contas do cliente {client.id}: {exc.message}",
                user_message=(
                    "O Omie não respondeu no tempo esperado ao sincronizar as contas. "
                    "Tente novamente em instantes."
                ),
                metadata={"client_id": str(client.id), "cause": "timeout"},
            ) from exc
        except OmieFaultError as exc:
            raise AccountsSyncError(
                f"Fault Omie ao sincronizar contas do cliente {client.id}: {exc.message}",
                user_message=exc.user_message,
                metadata={"client_id": str(client.id), "cause": "fault"},
            ) from exc
        finally:
            if owns_client:
                await omie_client.aclose()


def _to_cache_row(client_id: object, conta: ContaCorrente) -> OmieAccountCache:
    """Converte um `ContaCorrente` (DTO Omie) em `OmieAccountCache` (ORM).

    `bank_name` recebe o código do banco (string de 3 dígitos) — o
    `ListarContasCorrentes` do Omie não devolve o nome do banco por extenso.
    Fallback `"—"` quando o Omie omite o `codigo_banco` (raro). Mapeamento
    código→nome é tarefa futura (tabela estática ou novo endpoint Omie).
    """
    return OmieAccountCache(
        client_id=client_id,
        omie_conta_id=conta.n_cod_cc,
        name=conta.descricao,
        bank_name=conta.codigo_banco or "—",
        account_type=conta.tipo,
    )
