"""Sincronização do plano de contas do cliente (Sprint 10, BACK 10.2).

**Reusa a leitura que já existe.** As categorias do cliente já são lidas por
`OmieCategoriasService` sobre `OmieCategoriasCache` (in-memory, TTL 6h) sobre
`OmieClient.listar_categorias`. Este serviço chama a MESMA função — a 10.2
acrescentou só o acessor que devolve os objetos completos
(`list_raw_categorias`). Se em algum momento parecer necessária uma segunda
chamada à origem, o desenho está errado: a tela de revisão e a tela do plano de
contas passariam a ver catálogos diferentes, e o "contrato fonte única" do PRD
seria falso.

**Dois relógios diferentes, de propósito.** O cache in-memory de 6h resolve
NOMES em tempo de tela e continua exatamente como estava. A **persistência**
tem validade de 24h (`clients.chart_of_accounts_synced_at`), alinhada ao cache
de contas correntes (`clients/accounts_cache.py`) para não existir um terceiro
regime de validade no produto. "Sincronizar agora" (`force=True`) ignora os
dois: invalida o cache de 6h e rebusca.

**Ordem que sustenta "nada pela metade".** A chamada à origem acontece ANTES de
qualquer escrita. Se ela falhar, não há meia-gravação para desfazer: o serviço
carimba a falha, deixa o carimbo do último sucesso intacto e re-levanta. Se ela
der certo, as três escritas (upsert, marcar ausentes, carimbar sucesso)
acontecem na MESMA transação do request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.logging import get_logger
from app.integrations.providers.base import Capability
from app.modules.client_chart_of_accounts.schemas import ResolvedNames, chart_of_accounts_row
from app.modules.client_connections.origin import (
    build_origin_client,
    resolve_capable_connection,
)
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.db.models import Client
    from app.integrations.omie.client import OmieClient
    from app.integrations.omie.schemas import CategoriaOmie
    from app.modules.client_chart_of_accounts.repository import (
        ChartOfAccountsCoverage,
        ClientChartOfAccountsRepository,
    )
    from app.modules.omie_data.categorias_service import OmieCategoriasService

log = get_logger(__name__)

#: Validade da PERSISTÊNCIA. Decisão declarada no PRD, alinhada ao
#: `CACHE_TTL` de `clients/accounts_cache.py` — um regime de validade só.
#: O TTL de 6h do cache in-memory de NOMES não é tocado por esta sprint.
CHART_OF_ACCOUNTS_TTL = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class ChartOfAccountsSyncResult:
    """O desfecho de uma sincronização — inclusive quando ela não aconteceu.

    `from_origin=False` é o caminho "dentro da validade": nenhuma chamada à
    origem, e `synced_at` é o carimbo antigo. A rota responde 200 do mesmo
    jeito; o que muda é que o usuário está vendo dado local, e a data diz qual.
    """

    synced_at: datetime
    from_origin: bool
    coverage: ChartOfAccountsCoverage
    marked_absent: int = 0


class ChartOfAccountsSyncService:
    """Sincroniza o plano de contas de UM cliente. Não decide permissão nem rota."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        repository: ClientChartOfAccountsRepository,
        categorias_service: OmieCategoriasService,
        settings: Settings,
        usage_events: UsageEventService | None = None,
    ) -> None:
        self._db = db
        self._repo = repository
        self._categorias = categorias_service
        self._settings = settings
        # Reusa o emissor que já existe — um segundo canal de telemetria seria
        # uma segunda verdade sobre o mesmo fato. Construído aqui quando não
        # vem pronto para que nenhum caller possa "esquecer" a métrica da
        # sprint: o default é emitir.
        self._usage_events = usage_events or UsageEventService(UsageEventRepository(db))

    async def sync(self, client: Client, *, force: bool = False) -> ChartOfAccountsSyncResult:
        """Traz o plano de contas da origem para o banco, ou serve do local.

        ⚠️ **A trava de cliente ENCERRADO é de ROTA** (`OpenClientDep`, 10.3).
        Um segundo check aqui poderia divergir dela — e o dia em que
        divergisse, um dos dois estaria errado sem ninguém saber qual.
        """
        last_ok, _last_failure = await self._repo.get_sync_state(client.id)

        if not force and last_ok is not None and self._is_fresh(last_ok):
            log.info(
                "chart_of_accounts_within_ttl",
                client_id=str(client.id),
                synced_at=last_ok.isoformat(),
            )
            return ChartOfAccountsSyncResult(
                synced_at=last_ok,
                from_origin=False,
                coverage=await self._repo.coverage(client.id),
            )

        categorias = await self._fetch(client, force=force)
        return await self._persist(client, categorias)

    async def resolve_names(self, client: Client) -> ResolvedNames:
        """Nomes de categoria e de conta de demonstrativo, do MESMO cache (R3).

        É o que mantém a tela nova e a tela de revisão mostrando **a mesma
        descrição para o mesmo código**: uma fonte só, o
        `OmieCategoriasService`. Persistir o nome resolveria a latência e
        quebraria a §4.5 — e faria as duas telas divergirem no dia em que uma
        descrição mudasse no Omie.

        **Fail-soft por decisão de produto.** Origem indisponível devolve os
        dois mapas VAZIOS, e a lista sai com `name: null`. A alternativa
        (propagar 502/409) tornaria o plano de contas ilegível justamente
        quando o usuário mais precisa dele: a credencial expirou, e o que ele
        tem de ver é a lista com o aviso, não uma tela de erro. Códigos,
        destinos e cobertura são LOCAIS e continuam corretos.
        """
        try:
            categorias = await self._fetch_names(client)
        except Exception:
            # Só IDs: a mensagem do fornecedor é texto livre de terceiro (§3.3).
            log.warning("chart_of_accounts_names_unavailable", client_id=str(client.id))
            return ResolvedNames(categories={}, dre={})

        categories = {c.codigo: c.descricao for c in categorias}
        dre = {
            destino: c.dados_dre.descricao_dre
            for c in categorias
            if (destino := c.destino_code) is not None and c.dados_dre.descricao_dre
        }
        return ResolvedNames(categories=categories, dre=dre)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _fetch_names(self, client: Client) -> list[CategoriaOmie]:
        """Leitura para EXIBIÇÃO — sempre pelo cache, nunca com `refresh`.

        Separada de `_fetch` de propósito: esta não carimba falha. Uma rota de
        LEITURA que marcasse `sync_failed_at` poria na tela um aviso de "a
        sincronização falhou" que nunca aconteceu.
        """
        connection = await resolve_capable_connection(
            self._db, client, Capability.LISTAR_LANCAMENTOS, settings=self._settings
        )

        async def build_client() -> OmieClient:
            return await build_origin_client(client, connection, settings=self._settings)

        return await self._categorias.list_raw_categorias(
            client_id=client.id, omie_client_factory=build_client
        )

    @staticmethod
    def _is_fresh(synced_at: datetime) -> bool:
        """`synced_at` vem do Postgres com timezone — comparar com `now(UTC)`."""
        return datetime.now(UTC) - synced_at <= CHART_OF_ACCOUNTS_TTL

    async def _fetch(self, client: Client, *, force: bool) -> list[CategoriaOmie]:
        """Uma chamada à origem, pelo serviço de categorias que já existe.

        A conexão capaz é resolvida ANTES da fábrica: cliente sem origem recebe
        um dos três 409 da taxonomia da S9 (`SEM_CONEXAO`, `ORIGEM_COM_ERRO`,
        `CAPACIDADE_AUSENTE`) sem que nada seja marcado como falha — não é a
        origem que falhou, é a configuração do cliente que não permite tentar.

        `force=True` invalida o cache de 6h: "Sincronizar agora" existe
        justamente para quem acabou de criar a categoria no Omie.
        """
        connection = await resolve_capable_connection(
            self._db, client, Capability.LISTAR_LANCAMENTOS, settings=self._settings
        )

        async def build_client() -> OmieClient:
            """Só chamada no MISS do cache — em staging/prod o unwrap da DEK é
            uma ida ao Cloud KMS."""
            return await build_origin_client(client, connection, settings=self._settings)

        try:
            return await self._categorias.list_raw_categorias(
                client_id=client.id,
                omie_client_factory=build_client,
                refresh=force,
            )
        except Exception:
            # Falha da origem (fault com HTTP 200, auth, timeout, 5xx). Nada foi
            # escrito ainda, então não há meia-gravação: só o carimbo de falha,
            # que NUNCA toca o do último sucesso. O `commit` é barreira de
            # durabilidade — a política de `get_db_session` é
            # `except: rollback(); raise`, e sem ele a marcação morreria junto
            # com a exceção (mesmo caso do ADR-053-BE).
            await self._repo.mark_sync_failed(client.id, at=datetime.now(UTC))
            await self._db.commit()
            # Só IDs: a mensagem do fornecedor é texto livre de terceiro (§3.3)
            # e a `faultstring` da Omie ecoa conteúdo do cadastro do cliente.
            log.warning("chart_of_accounts_sync_failed", client_id=str(client.id))
            raise

    async def _persist(
        self, client: Client, categorias: list[CategoriaOmie]
    ) -> ChartOfAccountsSyncResult:
        """Grava o resultado inteiro numa transação — a do request.

        Origem que devolve lista VAZIA é tratada como vazia de verdade: quem
        converte erro do fornecedor em exceção é o `OmieCategoriasService`, que
        nunca devolve `[]` por falha (a Omie responde HTTP 200 com
        `faultstring`, e tratar isso como "cliente sem categorias" apagaria o
        plano de contas inteiro de quem teve uma credencial expirar).
        """
        rows = _dedupe_by_code(categorias, client_id=client.id)
        now = datetime.now(UTC)

        await self._repo.upsert_many(client.id, rows, synced_at=now)
        marked_absent = await self._repo.mark_absent_from_origin(
            client.id, keep_codes=[row["category_code"] for row in rows]
        )
        await self._repo.mark_sync_succeeded(client.id, at=now)

        coverage = await self._repo.coverage(client.id)
        log.info(
            "chart_of_accounts_synced",
            client_id=str(client.id),
            total=coverage.total,
            ativas=coverage.ativas,
            com_destino=coverage.com_destino,
            com_conta_contabil=coverage.com_conta_contabil,
            marked_absent=marked_absent,
        )
        # A MÉTRICA DA SPRINT (10.4). Fail-soft por construção — `emit` engole a
        # falha e devolve `False`, protegido por SAVEPOINT no repository: uma
        # telemetria com defeito não pode reverter a sincronização que acabou de
        # dar certo. Emitido em TODA sincronização bem-sucedida (inclusive a
        # forçada) e SEM dedup: a fórmula lê a ÚLTIMA linha por `client_id`, e
        # 30 sincronizações precisam gerar 30 linhas.
        await self._usage_events.emit_plano_contas_sincronizado(
            client_id=client.id,
            total_categorias=coverage.total,
            ativas=coverage.ativas,
            com_destino=coverage.com_destino,
            com_conta_contabil=coverage.com_conta_contabil,
        )
        return ChartOfAccountsSyncResult(
            synced_at=now,
            from_origin=True,
            coverage=coverage,
            marked_absent=marked_absent,
        )


def _dedupe_by_code(categorias: list[CategoriaOmie], *, client_id: UUID) -> list[dict[str, Any]]:
    """Traduz e remove código repetido, mantendo a ÚLTIMA ocorrência.

    `listar_categorias` é paginado, e página repetida por instabilidade do
    fornecedor devolveria o mesmo código duas vezes no mesmo lote. Um
    `ON CONFLICT DO UPDATE` com a mesma chave duas vezes no MESMO comando é
    erro do Postgres (`cannot affect row a second time`) — a sincronização
    inteira morreria por um problema que não é do cliente.
    """
    by_code: dict[str, dict[str, Any]] = {}
    for categoria in categorias:
        row = chart_of_accounts_row(categoria)
        by_code[str(row["category_code"])] = row

    if len(by_code) != len(categorias):
        log.warning(
            "chart_of_accounts_duplicate_codes",
            client_id=str(client_id),
            recebidas=len(categorias),
            distintas=len(by_code),
        )
    return list(by_code.values())
