"""Ingestão da CARTEIRA de títulos em aberto (Sprint 11, BACK 11.2).

**O recorte é o oposto do da conciliação, e é só isso que muda.** A leitura
(`OmieClient.listar_contas_pagar`/`listar_contas_receber`) é a MESMA — nenhum
cliente de integração novo. O que a carteira faz de diferente é não filtrar por
conta corrente e não filtrar por competência, e é por isso que um título vencido
há quatro meses volta. `processing/omie_fetch.py` não é tocado: a conciliação
continua pedindo uma conta e um mês, e a bateria dela continua verde.

**Ordem que sustenta "nunca carteira parcial".** A origem é consultada INTEIRA
antes de qualquer escrita. Se a leitura falhar no meio, não existe meia-gravação
para desfazer: o serviço carimba `titles_sync_failed_at`, deixa
`titles_synced_at` **intocado** e re-levanta. Se der certo, o ciclo (upsert +
fechar quem saiu + carimbar sucesso) roda na MESMA transação.

⚠️ **A marcação de falha precisa de `commit()` explícito** (ADR-053-BE): a
política de `get_db_session` é `except: rollback(); raise`, então sem a barreira
de durabilidade o carimbo morreria junto com a exceção que o motivou — escrita
seguida de `raise` é desfeita em produção e **invisível** no teste de integração,
cuja fixture não tem o `rollback()` da produção.

⚠️ **Suposição S-1 do PRD, status EM TESTE:** "o limite de requisições da origem
comporta a ingestão diária da carteira de todos os clientes de uma organização".
Este serviço serializa por cliente e alterna os endpoints (o que o produto já
aprendeu a fazer na conciliação), mas o tempo de uma ingestão completa com o
maior cliente de dev **não foi medido** — é a task de QA. Se S-1 for falsa, a
janela de sincronização estoura.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.db.models.client_title import TitleType
from app.integrations.omie.client_locks import origin_client_locks
from app.integrations.providers.base import Capability, ProviderTitleKind
from app.modules.client_connections.origin import (
    build_origin_provider,
    resolve_capable_connection,
)
from app.modules.client_titles.schemas import client_title_row
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.db.models import Client, OmieAccountCache
    from app.integrations.omie.client_locks import OriginClientLocks
    from app.integrations.providers.base import ProviderOpenTitle
    from app.modules.client_titles.repository import ClientTitlesRepository, TitlesSummary
    from app.modules.clients.repository import ClientRepository

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class TitleSyncResult:
    """O desfecho de UMA sincronização íntegra — só contagens.

    As cinco grandezas do evento `carteira_sincronizada` (11.3) saem daqui, e
    são calculadas **uma vez**, sobre o que a origem devolveu. Recontá-las no
    emissor abriria a porta para a métrica e o banco discordarem — e a métrica é
    o único jeito de saber se a sprint funcionou.
    """

    synced_at: datetime
    titulos_pagar: int
    titulos_receber: int
    vencidos: int
    mais_antigo_dias: int
    #: Quantos saíram do conjunto em aberto nesta passada (liquidados na origem
    #: ou simplesmente ausentes). Não vai no evento — é diagnóstico de log.
    fechados: int

    @property
    def total(self) -> int:
        return self.titulos_pagar + self.titulos_receber


class ClientTitlesSyncService:
    """Sincroniza a carteira de UM cliente. Não decide permissão nem rota."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        repository: ClientTitlesRepository,
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
        # de lançamentos consome. Injetável só para o teste poder observá-lo.
        self._locks = locks or origin_client_locks
        # Reusa o emissor que já existe — um segundo canal de telemetria seria
        # uma segunda verdade sobre o mesmo fato. Construído aqui quando não vem
        # pronto para que nenhum caller possa "esquecer" a métrica da sprint: o
        # default é emitir.
        self._usage_events = usage_events or UsageEventService(UsageEventRepository(db))

    async def sync(self, client: Client) -> TitleSyncResult:
        """Traz a carteira da origem para o banco. Sem TTL: quem chama decide.

        Diferente do plano de contas (S10), aqui **não** há janela de validade.
        Dois motivos: a cadência é decidida fora (o job diário da INFRA 11.6 e o
        botão manual), e um TTL faria o botão "Sincronizar" mentir para quem
        acabou de registrar um pagamento no ERP.

        ⚠️ **A trava de cliente ENCERRADO é de ROTA** (`OpenClientDep`, 11.5) e do
        filtro do comando em lote. Um terceiro check aqui poderia divergir dos
        dois — e o dia em que divergisse, um deles estaria errado sem ninguém
        saber qual.
        """
        titles = await self._fetch(client)
        return await self._persist(client, titles)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch(self, client: Client) -> list[ProviderOpenTitle]:
        """Uma leitura da origem, serializada por cliente.

        A conexão capaz é resolvida ANTES de qualquer coisa: cliente sem origem
        recebe um dos três 409 da taxonomia da S9 (`SEM_CONEXAO`,
        `ORIGEM_COM_ERRO`, `CAPACIDADE_AUSENTE`) e **nada é marcado como falha**
        — não é a origem que falhou, é a configuração do cliente que não permite
        tentar. Marcar falha aqui poria na tela "a sincronização falhou" para um
        cliente que nunca conectou nada.
        """
        connection = await resolve_capable_connection(
            self._db, client, Capability.LISTAR_TITULOS_EM_ABERTO, settings=self._settings
        )
        known_accounts = await self._known_account_ids(client)
        provider = await build_origin_provider(client, connection, settings=self._settings)

        try:
            # O lock é por CLIENTE porque o limite da origem é por credencial:
            # a Tela de Revisão enriquecendo lançamentos e esta ingestão são
            # dois caminhos contra a MESMA `app_key`, e paralelizá-los devolve
            # `8020`/`1880`. Mesmo mecanismo do cache — não um segundo.
            async with self._locks.for_client(client.id):
                return await provider.list_open_titles(known_account_external_ids=known_accounts)
        except Exception:
            # Falha da origem (fault com HTTP 200, auth, timeout, 5xx). Nada foi
            # escrito ainda: a carteira ANTERIOR continua inteira, e o único
            # efeito é o carimbo de falha — que NUNCA toca o do último sucesso.
            # O `commit` é barreira de durabilidade (ver o docstring do módulo).
            await self._repo.mark_sync_failed(client.id, at=datetime.now(UTC))
            await self._db.commit()
            # Só IDs: a mensagem do fornecedor é texto livre de terceiro (§3.3) e
            # a `faultstring` da Omie ecoa conteúdo do cadastro do cliente.
            log.warning("client_titles_sync_failed", client_id=str(client.id))
            raise
        finally:
            await provider.aclose()

    async def _known_account_ids(self, client: Client) -> list[str]:
        """As contas correntes JÁ CONHECIDAS do cliente — o ramo (b) do R1.

        Vem do cache que já existe (`omie_accounts_cache`), **sem** chamada nova
        à origem: descobrir as contas custaria a requisição que o ramo (a) está
        justamente tentando economizar, e o cache é populado pelo fluxo de
        conexão. Cache vazio simplesmente deixa o ramo (b) sem por onde iterar —
        e aí a falha da origem propaga, que é o desfecho honesto.
        """
        rows: Sequence[OmieAccountCache] = await self._clients.get_accounts_cache(client.id)
        return [str(row.omie_conta_id) for row in rows]

    async def _persist(self, client: Client, titles: list[ProviderOpenTitle]) -> TitleSyncResult:
        """Grava o ciclo inteiro numa transação — e só então carimba o sucesso.

        Origem que devolve lista VAZIA é tratada como vazia de verdade: quem
        converte erro do fornecedor em exceção é o `OmieClient` (a Omie responde
        **HTTP 200 com `faultstring`**), então `[]` aqui significa "este cliente
        não tem título em aberto", não "a leitura falhou". Tratar o contrário
        fecharia a carteira inteira de quem teve uma credencial expirar.
        """
        now = datetime.now(UTC)
        rows = [client_title_row(title) for title in titles]

        outcome = await self._repo.reconcile_cycle(client.id, rows, synced_at=now)
        await self._repo.mark_sync_succeeded(client.id, at=now)

        result = _summarize(titles, synced_at=now, today=now.date(), fechados=outcome.closed)
        log.info(
            "client_titles_synced",
            client_id=str(client.id),
            total=result.total,
            titulos_pagar=result.titulos_pagar,
            titulos_receber=result.titulos_receber,
            vencidos=result.vencidos,
            mais_antigo_dias=result.mais_antigo_dias,
            fechados=result.fechados,
        )
        # A MÉTRICA DA SPRINT (11.3). Fail-soft por construção — `emit` engole a
        # falha e devolve `False`, protegido por SAVEPOINT no repository: uma
        # telemetria com defeito não pode reverter a sincronização que acabou de
        # dar certo. Emitido só AQUI, no fim de uma sincronização ÍNTEGRA: o
        # caminho de falha (`_fetch`) re-levanta antes de chegar neste método, e é
        # isso que garante "sincronização que falhou no meio NÃO emite o evento".
        # Sem dedup: a fórmula lê a ÚLTIMA linha por `client_id`, e 30
        # sincronizações precisam gerar 30 linhas.
        await self._usage_events.emit_carteira_sincronizada(
            client_id=client.id,
            titulos_receber=result.titulos_receber,
            titulos_pagar=result.titulos_pagar,
            vencidos=result.vencidos,
            mais_antigo_dias=result.mais_antigo_dias,
        )
        return result


class ClientTitlesReadService:
    """A LEITURA da carteira — agregados e página. Nunca fala com a origem.

    Separado do serviço de sincronização de propósito, e pelo mesmo motivo que
    `_fetch_names` é separado de `_fetch` no plano de contas (S10): uma rota de
    LEITURA que pudesse carimbar `titles_sync_failed_at` poria na tela um aviso
    de "a sincronização falhou" que nunca aconteceu.

    **Uma função calcula cada agregado**, e é a do repositório
    (`ClientTitlesRepository.aging`). Esta classe só junta os agregados com o
    estado do sync e a data de referência — a tela, o export futuro e qualquer
    outro consumidor consultam ELA, nunca recalculam.
    """

    def __init__(self, repository: ClientTitlesRepository) -> None:
        self._repo = repository

    async def summary(self, client: Client, *, today: date | None = None) -> TitlesSummary:
        """O bloco de agregados do R3, com os três estados resolvidos.

        `today` é a data corrente do **SERVIDOR** (R3) — o parâmetro existe para o
        teste poder plantar títulos em cada balde sem depender do calendário do
        dia em que roda, nunca para a rota aceitar uma data do cliente. Deixar o
        navegador escolher o "hoje" poria o mesmo título em baldes diferentes para
        duas pessoas olhando a mesma tela.

        **Os agregados são calculados mesmo quando a última tentativa falhou**, e
        isso é a decisão do R3: eles descrevem a última carteira ÍNTEGRA (a falha
        não escreveu nada), e `sync_failed_at` junto com `synced_at` é o que
        permite à tela dizer "falhou agora, e estes números são de tal dia".
        Descartá-los deixaria a tela vazia justamente quando o usuário precisa
        dela.
        """
        reference = today or datetime.now(UTC).date()
        aging = await self._repo.aging(client.id, today=reference)
        synced_at, failed_at = await self._repo.get_sync_state(client.id)
        return TitlesSummary(
            a_pagar=aging[TitleType.A_PAGAR],
            a_receber=aging[TitleType.A_RECEBER],
            synced_at=synced_at,
            sync_failed_at=failed_at,
            referencia=reference,
        )


def _summarize(
    titles: Sequence[ProviderOpenTitle],
    *,
    synced_at: datetime,
    today: date,
    fechados: int,
) -> TitleSyncResult:
    """As contagens do evento, sobre o que a origem devolveu.

    `mais_antigo_dias` é o **maior atraso** da carteira, em dias — `0` quando
    nada está vencido (inclusive na carteira vazia). Zero aqui é informação
    honesta: "nada vencido". Quem precisa distinguir "carteira vazia" de "nada
    vencido" olha `total`, e quem precisa distinguir das duas "nunca
    sincronizou" olha `clients.titles_synced_at` — que é justamente por isso que
    ele existe (§R3).

    Só contagens e números: nenhum identificador de título, nenhum código de
    fornecedor, nada de nome. O payload do evento é público por construção.
    """
    pagar = sum(1 for t in titles if t.kind is ProviderTitleKind.A_PAGAR)
    receber = sum(1 for t in titles if t.kind is ProviderTitleKind.A_RECEBER)
    atrasos = [(today - t.due_date).days for t in titles if t.due_date < today]
    return TitleSyncResult(
        synced_at=synced_at,
        titulos_pagar=pagar,
        titulos_receber=receber,
        vencidos=len(atrasos),
        mais_antigo_dias=max(atrasos, default=0),
        fechados=fechados,
    )


#: Gate de vocabulário: o DTO neutro e o enum do banco falam a MESMA língua.
#: Importado pelo teste; declarado aqui para que o dia em que alguém renomear um
#: dos dois lados a falha apareça em `client_title_row`, que é quem depende disso.
TITLE_KIND_TO_TYPE = {
    ProviderTitleKind.A_PAGAR: TitleType.A_PAGAR,
    ProviderTitleKind.A_RECEBER: TitleType.A_RECEBER,
}
