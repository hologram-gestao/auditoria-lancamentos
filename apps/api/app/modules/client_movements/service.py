"""Ingestão da BASE DE MOVIMENTOS de uma competência (Sprint 12, BACK 12.1 — R0).

**A leitura é a que já existe.** `OriginProvider.list_entries` (no Omie, o
`ListarExtrato` do `OmieClient`, verificado contra fixture real) — nenhum cliente de
integração novo. O que muda é o RECORTE: todas as contas conhecidas do cliente, de
qualquer tipo, do primeiro ao último dia da competência. **Não** é a janela
expandida de ±3 dias da §5.3 (aquela é do matcher).

**Ordem que sustenta "nunca base parcial passando por completa".** A origem é lida
INTEIRA — todas as contas — antes de qualquer escrita. Se uma conta falhar no meio,
não existe meia-gravação para desfazer: o serviço carimba a falha da competência,
deixa o carimbo de sucesso **intocado** e re-levanta. Se der certo, o ciclo (upsert
+ marcar ausentes + carimbar sucesso) roda na MESMA transação.

⚠️ **A marcação de falha precisa de `commit()` explícito** (ADR-053-BE/ADR-065-BE):
a política de `get_db_session` é `except: rollback(); raise`, então sem a barreira
de durabilidade o carimbo morreria junto com a exceção que o motivou — e isso é
invisível no teste de integração, cuja fixture não tem o `rollback()` da produção.

**Os três 409 da S9 NÃO carimbam nada** (nem o 409 de contas desconhecidas): não é
a origem que falhou, é a configuração do cliente que não permite tentar.

**Um lock, o mesmo** (`origin_client_locks`, ADR-064-BE), e **nunca aninhado**: a
Omie processa uma requisição por método por credencial, e a Tela de Revisão
enriquecendo lançamentos chama o MESMO `ListarExtrato` com a mesma credencial.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from app.core.exceptions import ClientClosedError, MovementAccountsUnknownError
from app.core.logging import get_logger
from app.integrations.omie.client_locks import origin_client_locks
from app.integrations.providers.base import Capability
from app.modules.client_connections.origin import (
    build_origin_provider,
    resolve_capable_connection,
)
from app.modules.client_movements.competence import competence_bounds, competence_of
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.db.models import Client
    from app.integrations.omie.client_locks import OriginClientLocks
    from app.integrations.providers.base import ProviderEntry
    from app.modules.client_movements.repository import ClientMovementsRepository
    from app.modules.clients.repository import ClientRepository

log = get_logger(__name__)

#: Pausa entre extratos consecutivos (uma conta depois da outra) — o MESMO número
#: de `omie_adapter._INTER_CALL_DELAY_SECONDS`, copiado e não redescoberto: sem o
#: intervalo, a segunda chamada do mesmo método com a mesma credencial volta
#: `1880`. Mora aqui (e não no adaptador) porque é a iteração por conta que produz
#: chamadas adjacentes do mesmo método.
INTER_ACCOUNT_DELAY_SECONDS = 1.5


@dataclass(frozen=True, slots=True)
class MovementSyncResult:
    """O desfecho de UMA sincronização íntegra — só contagens.

    As grandezas do evento `movimentos_sincronizados` (BACK 12.2) saem daqui, e são
    calculadas **uma vez**, sobre o que a origem devolveu. Recontá-las no emissor
    abriria a porta para a métrica e o banco discordarem. O mesmo resultado volta
    na resposta da rota de sincronização.
    """

    competence: date
    synced_at: datetime
    #: Movimentos que a origem devolveu na competência (depois do dedup).
    movimentos: int
    #: Deles, quantos sem código de categoria — o "sem categoria de origem" do R3.
    sem_categoria: int
    #: Contas lidas.
    contas: int
    #: Quantos estavam presentes e saíram nesta passada. Diagnóstico de log — não
    #: vai no evento.
    ausentes: int


class ClientMovementsSyncService:
    """Sincroniza a base de movimentos de UM cliente em UMA competência.

    Não decide permissão nem rota — isso é da BACK 12.2.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        repository: ClientMovementsRepository,
        clients: ClientRepository,
        settings: Settings,
        locks: OriginClientLocks | None = None,
        usage_events: UsageEventService | None = None,
    ) -> None:
        self._db = db
        self._repo = repository
        self._clients = clients
        self._settings = settings
        # O lock por cliente da fonte única (`client_locks`), o MESMO que o cache
        # de lançamentos e a carteira consomem. Injetável só para o teste observar.
        self._locks = locks or origin_client_locks
        # O emissor que já existe, construído aqui quando não vem pronto: nenhum
        # caller pode "esquecer" a instrumentação — o default é emitir.
        self._usage_events = usage_events or UsageEventService(UsageEventRepository(db))

    async def sync(self, client: Client, competence: date) -> MovementSyncResult:
        """Traz a competência da origem para a base. Sem TTL: quem chama decide.

        Cliente ENCERRADO é recusado aqui, antes de qualquer leitura — encerrado
        não opera nem tem origem (§4.12). A rota (BACK 12.2) também o recusa via
        `OpenClientDep`; o check aqui existe porque o serviço ESCREVE, e é ele que
        a task pede que se prove sem rota.
        """
        if client.closed_at is not None:
            raise ClientClosedError(
                f"Cliente {client.id} está encerrado; sincronização de movimentos recusada."
            )
        start, end = competence_bounds(competence)
        source_type, entries, contas = await self._fetch(client, competence, start=start, end=end)
        return await self._persist(
            client, competence, source_type=source_type, entries=entries, contas=contas
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch(
        self, client: Client, competence: date, *, start: date, end: date
    ) -> tuple[str, list[tuple[str, ProviderEntry]], int]:
        """A origem INTEIRA da competência — todas as contas, em série, sob o lock.

        A conexão capaz é resolvida ANTES de qualquer coisa: cliente sem origem
        recebe um dos três 409 da S9 e **nada** é carimbado.
        """
        connection = await resolve_capable_connection(
            self._db, client, Capability.LISTAR_LANCAMENTOS, settings=self._settings
        )
        accounts = await self._known_account_ids(client)
        if not accounts and connection.accounts_synced_at is None:
            raise MovementAccountsUnknownError(
                f"Cliente {client.id}: cache de contas vazio e nunca sincronizado."
            )
        provider = await build_origin_provider(client, connection, settings=self._settings)

        entries: list[tuple[str, ProviderEntry]] = []
        try:
            # O lock é por CLIENTE porque o limite da origem é por credencial.
            # Uma aquisição só, em volta da iteração inteira — e nada aqui dentro
            # pede o lock de novo (NUNCA aninhar, ADR-064-BE).
            async with self._locks.for_client(client.id):
                for index, account in enumerate(accounts):
                    if index > 0:
                        await asyncio.sleep(INTER_ACCOUNT_DELAY_SECONDS)
                    found = await provider.list_entries(
                        account_external_id=account, start=start, end=end
                    )
                    entries.extend((account, entry) for entry in found)
        except Exception:
            # Falha da origem (fault com HTTP 200, auth, timeout, 5xx) em QUALQUER
            # conta. Nada foi escrito: a base anterior continua inteira, e o único
            # efeito é o carimbo de falha — que NUNCA toca o do último sucesso. O
            # `commit` é barreira de durabilidade (ver o docstring do módulo).
            await self._repo.mark_sync_failed(client.id, competence, at=datetime.now(UTC))
            await self._db.commit()
            # Só IDs: a mensagem do fornecedor é texto livre de terceiro (§3.3).
            log.warning(
                "client_movements_sync_failed",
                client_id=str(client.id),
                competence=competence.isoformat(),
            )
            raise
        finally:
            await provider.aclose()
        return provider.provider_type, entries, len(accounts)

    async def _known_account_ids(self, client: Client) -> list[str]:
        """As contas que o cache de contas do cliente já conhece — de QUALQUER tipo.

        Conta corrente, cartão e aplicação: o de-para classifica o que o cliente
        movimentou, e a fatura do cartão também é movimento. Sem chamada nova à
        origem para descobrir contas: o cache é populado pelo fluxo de conexão.
        """
        rows = await self._clients.get_accounts_cache(client.id)
        return [str(row.omie_conta_id) for row in rows]

    async def _persist(
        self,
        client: Client,
        competence: date,
        *,
        source_type: str,
        entries: Sequence[tuple[str, ProviderEntry]],
        contas: int,
    ) -> MovementSyncResult:
        """Grava o ciclo inteiro numa transação — e só então carimba o sucesso.

        Lista VAZIA da origem é competência vazia de verdade: quem converte erro do
        fornecedor em exceção é o `OmieClient` (a Omie responde HTTP 200 com
        `faultstring`), então `[]` aqui não é "a leitura falhou".
        """
        now = datetime.now(UTC)
        rows = _dedupe_by_source_id(
            [movement_row(entry, source_type=source_type, account=acc) for acc, entry in entries],
            client_id=client.id,
        )
        outcome = await self._repo.reconcile_cycle(
            client.id,
            source_type=source_type,
            competence=competence,
            rows=rows,
            synced_at=now,
        )
        await self._repo.mark_sync_succeeded(client.id, competence, at=now)

        result = MovementSyncResult(
            competence=competence,
            synced_at=now,
            movimentos=len(rows),
            sem_categoria=sum(1 for row in rows if row["category_code"] is None),
            contas=contas,
            ausentes=outcome.absent,
        )
        log.info(
            "client_movements_synced",
            client_id=str(client.id),
            competence=competence.isoformat(),
            movimentos=result.movimentos,
            sem_categoria=result.sem_categoria,
            contas=result.contas,
            ausentes=result.ausentes,
        )
        # Instrumentação do R0 (BACK 12.2). Fail-soft por construção — `emit`
        # engole a falha sob SAVEPOINT: telemetria com defeito não reverte a
        # sincronização que deu certo. Emitido só AQUI, no fim de uma sincronização
        # ÍNTEGRA: o caminho de falha (`_fetch`) re-levanta antes, e os 409 nem
        # começam — é isso que garante "falha e 409 não emitem". Sem dedup.
        await self._usage_events.emit_movimentos_sincronizados(
            client_id=client.id,
            competencia=competence,
            movimentos=result.movimentos,
            sem_categoria=result.sem_categoria,
            contas=result.contas,
        )
        return result


def movement_row(entry: ProviderEntry, *, source_type: str, account: str | None) -> dict[str, Any]:
    """`ProviderEntry` → linha de `client_movements`. **Só códigos** (§4.5).

    Fica de fora, de propósito: `description` (texto livre em que a origem ecoa
    nome de fornecedor) e `status` (rótulo de situação do terceiro — guardá-lo
    criaria um segundo vocabulário de estado ao lado do nosso).

    Código vazio vira `None` aqui, na borda (ADR-062-BE da S10): `''` e ausência
    são o MESMO estado ("sem categoria de origem"), e deixar `''` chegar ao banco
    faria o R3 contar dois estados onde há um.
    """
    return {
        "source_type": source_type,
        "source_movement_id": entry.external_id,
        # A competência é a da DATA do movimento, não a pedida: se a origem mudou
        # o movimento de mês, ele muda de competência no upsert.
        "competence": competence_of(entry.entry_date),
        "movement_date": entry.entry_date,
        "amount": entry.amount,
        "category_code": _code_or_none(entry.category_code),
        "supplier_code": _code_or_none(entry.supplier_code),
        "source_account_id": account,
    }


def _code_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _dedupe_by_source_id(rows: list[dict[str, Any]], *, client_id: object) -> list[dict[str, Any]]:
    """Mantém a ÚLTIMA ocorrência de cada identificador (ADR-066-BE da S10).

    O mesmo `source_movement_id` duas vezes no MESMO `INSERT ... ON CONFLICT DO
    UPDATE` é erro do Postgres (`cannot affect row a second time`) — a
    sincronização inteira morreria por um problema que não é do cliente. O log
    (só contagens) existe para o dia em que isso virar rotina não passar calado.
    """
    by_id = {str(row["source_movement_id"]): row for row in rows}
    if len(by_id) != len(rows):
        log.info(
            "client_movements_duplicate_ids",
            client_id=str(client_id),
            received=len(rows),
            kept=len(by_id),
        )
    return list(by_id.values())
