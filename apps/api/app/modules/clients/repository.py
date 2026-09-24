"""Acesso ao DB do módulo de clientes BPO.

Responsabilidades (CLAUDE.md §7):
    - **Apenas SQL/ORM** — regras de negócio, criptografia e RBAC ficam no
      service / dependencies.
    - Listagem com filtro RBAC já aplicado pelo caller (passa `manager_id` ou
      `None` para admin).
    - Conta de conciliações via subquery escalar correlacionada — uma query
      por listagem total, sem N+1.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Row,
    ScalarSelect,
    Select,
    String,
    and_,
    case,
    cast,
    delete,
    false,
    func,
    literal,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSessionTransaction
from sqlalchemy.orm import aliased, selectinload

from app.core.authz import CurrentUser, reach_filter
from app.db.models import (
    UQ_CLIENT_ASSIGNMENT_CLIENT_USER,
    UQ_USER_CLIENT_FAVORITE,
    Client,
    ClientAssignment,
    ClientCategory,
    ClientChartOfAccount,
    ClientConnection,
    ClientGlossaryEntry,
    ClientTitle,
    ConnectionStatus,
    Notification,
    OmieAccountCache,
    Organization,
    ReconciliationFile,
    ReconciliationSession,
    ReconciliationStatus,
    User,
    UserClientFavorite,
    UserRole,
    UserScope,
)
from app.modules.client_connections.legacy_fallback import legacy_origin_available


class ClientRow(NamedTuple):
    """Linha agregada da listagem: cliente + manager responsável + contagem.

    `manager` é `None` quando o cliente está órfão (não deveria acontecer em
    produção pela auto-criação do assignment, mas a listagem precisa ser
    resiliente — não derrubamos a tela por dado inconsistente).
    """

    client: Client
    manager: User | None
    reconciliation_count: int
    #: Organização dona (86e36ecqz): coluna NOT NULL + join interno, então
    #: sempre presente — sem `None`, sem `assert` na serialização.
    organization: Organization
    #: Favorito de QUEM pede (86e34jd5a). `False` quando não há viewer.
    is_favorite: bool = False
    #: Categoria do catálogo (86e34jd8m); `None` = sem categoria.
    category: ClientCategory | None = None
    #: Quantas pessoas têm acesso ao cliente, responsável incluído (86e390kz8).
    manager_count: int = 0
    #: S9 (BACK 09.4): contagens de ORIGEM, para derivar `origin_status` sem
    #: uma consulta por linha. Vêm de subqueries escalares na MESMA query.
    connections_total: int = 0
    connections_active: int = 0


class _FavoriteJoin(NamedTuple):
    """Outer join com `user_client_favorites` restrito ao viewer (86e34jd5a).

    `table` é um alias para o join não colidir com nada; `is_favorite` é a
    expressão booleana selecionável (`id IS NOT NULL`). Sem viewer, o `ON`
    é `false`: nenhuma linha casa e `is_favorite` sai `False` para todas —
    mesma forma do SELECT, sem `if` espalhado nos dois lugares que o usam.
    """

    table: Any
    on_clause: Any
    is_favorite: Any


def _favorite_join_for(viewer_user_id: UUID | None) -> _FavoriteJoin:
    fav = aliased(UserClientFavorite)
    on_clause = (
        and_(fav.client_id == Client.id, fav.user_id == viewer_user_id)
        if viewer_user_id is not None
        else false()
    )
    return _FavoriteJoin(
        table=fav,
        on_clause=on_clause,
        is_favorite=fav.id.is_not(None).label("is_favorite"),
    )


def _responsible_join_clause() -> ColumnElement[bool]:
    """`ON` do join de EXIBIÇÃO: só a linha do RESPONSÁVEL (86e390kz8).

    Com N gerentes por cliente, um join sem essa restrição devolveria o mesmo
    cliente N vezes — total certo (o count não passa pelo join), linhas
    repetidas, paginação furada. O índice parcial garante no máximo uma linha
    com `is_primary`, então o join continua 1:1 por construção.
    """
    return and_(ClientAssignment.client_id == Client.id, ClientAssignment.is_primary.is_(True))


def _manager_count_subquery() -> ScalarSelect[int]:
    """Subquery escalar: quantas pessoas têm acesso ao cliente (responsável incluído)."""
    access = aliased(ClientAssignment)
    return (
        select(func.count(access.id))
        .where(access.client_id == Client.id)
        .correlate(Client)
        .scalar_subquery()
    )


def _connection_counts_subqueries(
    *, include_legacy_origin: bool
) -> tuple[ColumnElement[int], ColumnElement[int]]:
    """(total de conexões, conexões ATIVAS) do cliente — S9 (BACK 09.4 + 09.5).

    Duas subqueries escalares e não um `JOIN`: a lista do escritório parceiro
    precisa mostrar quem está sem origem, e um join com `client_connections`
    multiplicaria a linha do cliente por conexão (o cliente com duas contas no
    mesmo ERP apareceria duas vezes). Contadas na MESMA query da listagem —
    sem N+1, que é o que uma consulta por linha viraria numa carteira de 40.

    São DOIS números porque os três estados de origem precisam deles: nenhuma
    conexão (`sem_origem`), alguma ativa (`ativa`), existe mas nenhuma ativa
    (`erro`). Um contador só não distinguiria os dois últimos.

    `include_legacy_origin` (09.5) faz a contagem enxergar o cliente que ainda
    opera pelas colunas antigas: com o fallback efetivamente ligado, ele vale
    por UMA conexão ativa — o mesmo que `resolve_origin_connections` devolve
    para ele. Sem isso, toda a base existente responderia `sem_origem` entre o
    deploy e o fim da conversão, e a tela bloquearia "Nova conciliação" e
    "Sincronizar contas" de quem o backend serve normalmente. O predicado é o
    de `legacy_fallback` — aqui não se escreve um segundo. Quem decide o
    booleano é o service, que sabe o estado EFETIVO da flag.

    É `case` e não soma: o ramo legado exige ZERO conexões gravadas
    (precedência), então os dois nunca se acumulam.
    """
    total = aliased(ClientConnection)
    ativa = aliased(ClientConnection)
    total_sq = (
        select(func.count(total.id))
        .where(total.client_id == Client.id)
        .correlate(Client)
        .scalar_subquery()
    )
    ativa_sq = (
        select(func.count(ativa.id))
        .where(ativa.client_id == Client.id, ativa.status == ConnectionStatus.ATIVA.value)
        .correlate(Client)
        .scalar_subquery()
    )
    if not include_legacy_origin:
        return total_sq, ativa_sq
    legado = legacy_origin_available()
    return (
        case((legado, literal(1)), else_=total_sq),
        case((legado, literal(1)), else_=ativa_sq),
    )


class _ClientRowQuery(NamedTuple):
    """O SELECT de uma linha de cliente + a expressão de favorito para o ORDER BY."""

    stmt: Select[Any]
    is_favorite: Any


def _client_row_query(
    viewer_user_id: UUID | None, *, include_legacy_origin: bool = False
) -> _ClientRowQuery:
    """SELECT base de UMA linha: cliente + responsável + contagens + favorito + categoria.

    Lista e detalhe partem DAQUI — coluna nova entra uma vez e aparece nos dois,
    na mesma posição (o mapeamento em `_to_client_row` é posicional). O join de
    exibição é só do RESPONSÁVEL (`_responsible_join_clause`): com N gerentes o
    join continua 1:1 e o cliente sai uma vez.

    `include_legacy_origin` chega até `_connection_counts_subqueries`; o default
    `False` é o estado pós-conversão (só conexões gravadas contam) — quem sabe
    que a janela de fallback está aberta é o service, e é ele que liga.
    """
    manager = aliased(User)
    favorite = _favorite_join_for(viewer_user_id)
    # Subquery escalar: conta de sessões ATIVAS por cliente (descarte de erros
    # não infla o contador). Correlate evita o SQLAlchemy referenciar `clients`
    # da query externa duas vezes.
    recon_count_sq = (
        select(func.count(ReconciliationSession.id))
        .where(
            ReconciliationSession.client_id == Client.id,
            ReconciliationSession.deleted_at.is_(None),
        )
        .correlate(Client)
        .scalar_subquery()
    )
    connections_total_sq, connections_active_sq = _connection_counts_subqueries(
        include_legacy_origin=include_legacy_origin
    )
    stmt = (
        select(
            Client,
            manager,
            recon_count_sq.label("recon_count"),
            favorite.is_favorite,
            ClientCategory,
            _manager_count_subquery().label("manager_count"),
            Organization,
            connections_total_sq.label("connections_total"),
            connections_active_sq.label("connections_active"),
        )
        .outerjoin(ClientAssignment, _responsible_join_clause())
        .outerjoin(manager, manager.id == ClientAssignment.user_id)
        .outerjoin(favorite.table, favorite.on_clause)
        .outerjoin(ClientCategory, ClientCategory.id == Client.category_id)
        # Interno: todo cliente tem organização (NOT NULL, 86e36ec7p).
        .join(Organization, Organization.id == Client.organization_id)
    )
    return _ClientRowQuery(stmt=stmt, is_favorite=favorite.is_favorite)


def _to_client_row(row: Row[Any]) -> ClientRow:
    """Mapeia a linha do SELECT de `_client_row_query` — na ordem das colunas de lá."""
    return ClientRow(
        client=row[0],
        manager=row[1],
        reconciliation_count=int(row[2] or 0),
        is_favorite=bool(row[3]),
        category=row[4],
        manager_count=int(row[5] or 0),
        organization=row[6],
        connections_total=int(row[7] or 0),
        connections_active=int(row[8] or 0),
    )


class ClientRepository:
    """Operações de leitura/escrita sobre `clients` e `client_assignments`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def savepoint(self) -> AsyncSessionTransaction:
        """SAVEPOINT (`begin_nested`) para o service desfazer um grupo de escritas
        sem derrubar a transação da request — mesmo padrão de `usage_events` e do
        job de processamento."""
        return self._session.begin_nested()

    # ------------------------------ READ ------------------------------

    async def list_paginated(
        self,
        *,
        user: CurrentUser,
        page: int,
        page_size: int,
        search: str | None = None,
        category_id: UUID | None = None,
        organization_id: UUID | None = None,
        include_legacy_origin: bool = False,
    ) -> tuple[Sequence[ClientRow], int]:
        """Lista paginada de clientes com manager + count de conciliações.

        Args:
            user: quem pede — a LINHA do usuário autenticado, nunca URL/payload
                (§3.15). Dela derivam o ALCANCE (`authz.reach_filter`: plataforma
                tudo; admin a própria organização; manager a carteira, por
                `EXISTS` em `client_assignments`, responsável ou colaborador —
                86e390kz8; cliente o próprio tenant) e os favoritos
                (`is_favorite` por linha, os DESSE usuário no topo — 86e34jd5a).
            page/page_size: paginação 1-based.
            search: ILIKE em `clients.name` (case-insensitive).
            organization_id: filtro OPCIONAL da plataforma (86e36ecqz), já
                decidido por `resolve_organization_filter` no service.
            include_legacy_origin: o fallback da 09.5 está EFETIVAMENTE ligado?
                Se sim, o cliente ainda não convertido conta como origem ativa
                na derivação de `origin_status` — o mesmo que ele recebe ao
                operar.

        Returns:
            Tupla `(rows, total_count)`. Total é a contagem ANTES da paginação.
        """
        query = _client_row_query(UUID(user.id), include_legacy_origin=include_legacy_origin)
        base = query.stmt
        count_base = select(func.count(Client.id)).select_from(Client)

        # O alcance entra na página E no count (senão o rodapé mente). É a
        # decisão única do authz projetada em WHERE — independente do join de
        # exibição (que é só do responsável): sem essa separação o colaborador
        # sumiria da própria lista.
        reach = reach_filter(user, Client.id)
        if reach is not None:
            base = base.where(reach)
            count_base = count_base.where(reach)

        if search:
            term = f"%{search.strip().lower()}%"
            base = base.where(func.lower(Client.name).like(term))
            count_base = count_base.where(func.lower(Client.name).like(term))

        if category_id is not None:
            # Filtro server-side (86e34jd8m): a paginação continua contando certo.
            base = base.where(Client.category_id == category_id)
            count_base = count_base.where(Client.category_id == category_id)

        if organization_id is not None:
            base = base.where(Client.organization_id == organization_id)
            count_base = count_base.where(Client.organization_id == organization_id)

        # Favoritos de quem pede primeiro (86e34jd5a); depois a ordem estável de
        # sempre: created_at desc, id desc (desempate determinístico). O favorito
        # da página 3 sobe para a página 1 porque a ordenação é do SELECT, não
        # da página já cortada.
        base = base.order_by(query.is_favorite.desc(), Client.created_at.desc(), Client.id.desc())
        offset = (page - 1) * page_size
        base = base.offset(offset).limit(page_size)

        total = (await self._session.execute(count_base)).scalar_one()
        result = await self._session.execute(base)
        rows = [_to_client_row(row) for row in result.all()]
        return rows, int(total)

    async def get_detail(
        self,
        client_id: UUID,
        *,
        viewer_user_id: UUID | None = None,
        include_legacy_origin: bool = False,
    ) -> ClientRow | None:
        """Carrega 1 cliente com manager + count — usado em endpoints de retorno.

        `viewer_user_id` resolve `is_favorite` para quem pede (86e34jd5a); sem
        viewer a linha sai com `False`. `include_legacy_origin` é o mesmo da
        listagem: o detalhe TEM de concordar com ela e com a lista de
        `connections` do mesmo corpo.
        """
        stmt = _client_row_query(
            viewer_user_id, include_legacy_origin=include_legacy_origin
        ).stmt.where(Client.id == client_id)
        row = (await self._session.execute(stmt)).first()
        return None if row is None else _to_client_row(row)

    async def get_by_id(self, client_id: UUID) -> Client | None:
        """Retorna o `Client` cru, sem joins — usado para writes (PATCH, assign)."""
        result = await self._session.execute(select(Client).where(Client.id == client_id))
        return result.scalar_one_or_none()

    async def has_responsible(self, client_id: UUID) -> bool:
        """O cliente tem responsável? (cliente criado por admin nasce sem — §4.13)."""
        result = await self._session.execute(
            select(ClientAssignment.id).where(
                ClientAssignment.client_id == client_id,
                ClientAssignment.is_primary.is_(True),
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_assignment_for_user(
        self, client_id: UUID, user_id: UUID
    ) -> ClientAssignment | None:
        """Vínculo de UMA pessoa com o cliente (no máximo um — UNIQUE do par).

        Substitui o antigo `get_assignment(client_id)`, que filtrava só por
        cliente com `scalar_one_or_none()` e estouraria `MultipleResultsFound`
        na primeira carteira com dois gerentes.
        """
        result = await self._session.execute(
            select(ClientAssignment).where(
                ClientAssignment.client_id == client_id,
                ClientAssignment.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_assignments_with_users(
        self, client_id: UUID
    ) -> Sequence[tuple[ClientAssignment, User]]:
        """Todos com acesso ao cliente: responsável primeiro, depois por nome."""
        stmt = (
            select(ClientAssignment, User)
            .join(User, User.id == ClientAssignment.user_id)
            .where(ClientAssignment.client_id == client_id)
            .order_by(ClientAssignment.is_primary.desc(), User.name.asc(), User.id.asc())
        )
        result = await self._session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def get_category_by_id(
        self, category_id: UUID, *, organization_id: UUID
    ) -> ClientCategory | None:
        """Categoria do catálogo DA ORGANIZAÇÃO do cliente (86e34jd8m + 86e36ecqz).

        O `AND organization_id` mora no SELECT: categoria de outra organização
        "não existe" para este cliente — mesmo 400 de categoria inexistente,
        sem oráculo de enumeração entre BPOs.
        """
        result = await self._session.execute(
            select(ClientCategory).where(
                ClientCategory.id == category_id,
                ClientCategory.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none()

    async def is_active_manager(self, user_id: UUID, *, organization_id: UUID) -> bool:
        """Alvo válido de carteira: existe, ativo, `manager` E DA MESMA ORGANIZAÇÃO
        do cliente — decidido no banco (86e36ecjp: a carteira é intra-org).

        Só `id` no SELECT: não hidrata a linha inteira de `users` (com hash de
        senha) para responder um booleano. Gerente de outra organização é
        indistinguível de "não é gerente" de propósito (anti-enumeração).
        """
        result = await self._session.execute(
            select(User.id).where(
                User.id == user_id,
                User.active.is_(True),
                User.role == UserRole.MANAGER.value,
                User.organization_id == organization_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def get_organization(self, organization_id: UUID) -> Organization | None:
        """Organização pela PK — para a plataforma escolher onde o cliente nasce."""
        return await self._session.get(Organization, organization_id)

    # ------------------------------ FAVORITOS (86e34jd5a) -------------

    async def add_favorite(self, *, user_id: UUID, client_id: UUID) -> None:
        """Marca o cliente como favorito do usuário. Idempotente.

        `ON CONFLICT DO NOTHING` na UNIQUE `(user_id, client_id)`: dois cliques
        (ou duas abas) não viram erro nem linha duplicada — a garantia é do
        banco, não da aplicação.
        """
        stmt = (
            pg_insert(UserClientFavorite)
            .values(user_id=user_id, client_id=client_id)
            .on_conflict_do_nothing(constraint=UQ_USER_CLIENT_FAVORITE)
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def remove_favorite(self, *, user_id: UUID, client_id: UUID) -> None:
        """Desfaz o favorito. Idempotente: não existir já é o estado final."""
        await self._session.execute(
            delete(UserClientFavorite).where(
                UserClientFavorite.user_id == user_id,
                UserClientFavorite.client_id == client_id,
            )
        )
        await self._session.flush()

    # ------------------------------ EXCLUSÃO (86e34jd1d) --------------

    async def count_sessions(self, client_id: UUID, *, processing_only: bool = False) -> int:
        """Conciliações do cliente (inclui as soft-deleted: vão junto na exclusão)."""
        stmt = select(func.count(ReconciliationSession.id)).where(
            ReconciliationSession.client_id == client_id
        )
        if processing_only:
            stmt = stmt.where(
                ReconciliationSession.status == ReconciliationStatus.PROCESSING.value,
                ReconciliationSession.deleted_at.is_(None),
            )
        return int((await self._session.execute(stmt)).scalar_one())

    async def count_tenant_users(self, client_id: UUID) -> int:
        stmt = select(func.count(User.id)).where(
            User.client_id == client_id, User.scope == UserScope.CLIENT.value
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def delete_client_cascade(self, client: Client) -> None:
        """Exclusão DEFINITIVA, na ordem que o grafo de FKs exige (86e34jd1d).

        Tudo numa transação (a do request). A ordem não é estética:
            1. conciliações do cliente — cascateiam arquivos, linhas, divergências,
               anomalias e postings, e SOLTAM o `created_by` (RESTRICT) que
               travaria a remoção dos usuários do tenant;
            2. usuários DO tenant (`scope='client'`) — a FK `users.client_id` é
               RESTRICT e travaria o passo 4; levam os próprios favoritos;
            3. notificações do cliente — sem FK (só IDs), mas são item de UI de
               um recurso que deixa de existir;
            4. as CONEXÕES de origem (S9) — o FK já é `CASCADE`, mas a lista
               declarada é a fonte: o `DELETE` explícito mantém as duas
               coerentes e faz a credencial cifrada da origem sumir junto;
            5. a linha de `clients` — cascateia atribuições, glossário, cache de
               contas Omie, **plano de contas** (S10), **carteira de títulos**
               (S11) e favoritos; a DEK morre com ela e tudo que ela cifrava
               vira indecifrável por construção (§4.1).

        `access_audit` e `usage_events` FICAM: são trilhas só de IDs (§4.7) e
        sobrevivem ao cliente de propósito.

        Core `DELETE`, não `session.delete(client)`: os relationships do model
        são `lazy="raise"` e o cascade do ORM tentaria carregá-los.
        """
        s = self._session
        await s.execute(
            delete(ReconciliationSession).where(ReconciliationSession.client_id == client.id)
        )
        await s.execute(
            delete(User).where(User.client_id == client.id, User.scope == UserScope.CLIENT.value)
        )
        await s.execute(delete(Notification).where(Notification.client_id == client.id))
        await s.execute(delete(ClientConnection).where(ClientConnection.client_id == client.id))
        await s.execute(delete(Client).where(Client.id == client.id))
        await s.flush()
        # A instância carregada pelo guard da rota não pode ficar viva na sessão
        # apontando para uma linha que já não existe.
        s.expunge(client)

    # --------------------- ENCERRAMENTO (86e36pm1z) --------------------

    async def close_client_purge(self, client_id: UUID) -> None:
        """Encerramento: remove o que NÃO tem valor operacional retido.

        Glossário (cifrado — já morreu com a DEK; linha de ciphertext morto não
        serve para nada), **conexões de origem** (S9 — a credencial cifrada
        delas morre pelo mesmo motivo, e uma origem de um cliente encerrado não
        tem o que operar), cache de contas Omie (nomes de contas do cliente,
        TTL), notificações (UI de um recurso que não opera mais), favoritos (o
        cliente sai do dia a dia) e o **plano de contas** (S10 — configuração do
        cliente final: nada nele é cifrado, então nada morreu com a DEK, mas
        cliente encerrado não tem plano de contas a operar).
        `client_assignments` FICA de propósito (decisão 09/09): o manager da
        carteira continua vendo o histórico. Conciliações, postings,
        `usage_events` e `access_audit` ficam — são a retenção que motivou o
        encerramento.

        A **carteira de títulos** (S11) entra pelo MESMO precedente do plano de
        contas, e a decisão está tomada aqui: nada nela é cifrado (só códigos,
        §4.5), então nada morreu com a DEK — mas a carteira é o espelho
        OPERACIONAL do que está em aberto na origem, e cliente encerrado não
        opera nem tem origem a consultar. Deixá-la ficaria com uma lista de
        cobranças vivas de um tenant morto. O histórico que o encerramento
        protege é o das conciliações e da trilha, não o de um espelho que a
        origem reconstrói em uma sincronização.
        """
        s = self._session
        await s.execute(
            delete(ClientGlossaryEntry).where(ClientGlossaryEntry.client_id == client_id)
        )
        await s.execute(delete(ClientConnection).where(ClientConnection.client_id == client_id))
        await s.execute(
            delete(ClientChartOfAccount).where(ClientChartOfAccount.client_id == client_id)
        )
        await s.execute(delete(ClientTitle).where(ClientTitle.client_id == client_id))
        await s.execute(delete(OmieAccountCache).where(OmieAccountCache.client_id == client_id))
        await s.execute(delete(Notification).where(Notification.client_id == client_id))
        await s.execute(delete(UserClientFavorite).where(UserClientFavorite.client_id == client_id))

    async def anonymize_tenant_users(self, client_id: UUID) -> None:
        """Anonimiza + desativa os usuários DO tenant (não pode apagar).

        As sessões RETIDAS carregam `created_by` (RESTRICT) apontando para estes
        usuários — apagar violaria a FK. Então: `active=False` (lockout no
        request seguinte, §3.12), nome genérico e e-mail tombstone ÚNICO por
        linha (o e-mail é UNIQUE global — o sufixo com o próprio id garante a
        unicidade sem identificar ninguém). `password_hash` fica: inofensivo com
        a conta inativa, e trocá-lo por lixo criaria um caso novo no login.
        """
        await self._session.execute(
            update(User)
            .where(User.client_id == client_id, User.scope == UserScope.CLIENT.value)
            .values(
                active=False,
                name="Usuário removido",
                email=func.concat("encerrado+", cast(User.id, String), "@anonimizado.invalid"),
            )
        )

    # ------------------------------ WRITE -----------------------------

    async def add_client(self, client: Client) -> None:
        """Insere/atualiza Client com flush + refresh.

        Refresh é necessário para carregar `created_at`/`updated_at` populados
        server-side (evita `MissingGreenlet` na serialização Pydantic).
        """
        self._session.add(client)
        await self._session.flush()
        await self._session.refresh(client)

    async def add_assignment(self, assignment: ClientAssignment) -> None:
        """Persiste um ClientAssignment com refresh do `assigned_at`."""
        self._session.add(assignment)
        await self._session.flush()
        await self._session.refresh(assignment)

    # --------------------- CARTEIRA COMPARTILHADA (86e390kz8) ----------

    async def add_assignment_if_absent(
        self, *, client_id: UUID, user_id: UUID, assigned_by: UUID
    ) -> bool:
        """Concede acesso (colaborador). `False` se a pessoa JÁ tinha.

        `ON CONFLICT DO NOTHING` na UNIQUE `(client_id, user_id)`: dois cliques
        (ou duas abas) não viram erro nem linha duplicada — a garantia é do
        banco. O `RETURNING` só devolve id quando inseriu de fato, e é isso que
        distingue "adicionei" de "já estava".
        """
        stmt = (
            pg_insert(ClientAssignment)
            .values(
                client_id=client_id,
                user_id=user_id,
                assigned_by=assigned_by,
                is_primary=False,
            )
            .on_conflict_do_nothing(constraint=UQ_CLIENT_ASSIGNMENT_CLIENT_USER)
            .returning(ClientAssignment.id)
        )
        inserted_id = (await self._session.execute(stmt)).scalar_one_or_none()
        await self._session.flush()
        return inserted_id is not None

    async def set_primary_assignment(self, *, client_id: UUID, user_id: UUID) -> bool:
        """Torna `user_id` (que JÁ tem acesso) o responsável; ninguém é removido.

        Três statements, na mesma transação e nesta ordem:
            1. `SELECT ... FOR UPDATE` das linhas do cliente — serializa dois
               `/assign` concorrentes (sem isso o segundo estouraria o índice
               parcial `uq_client_assignments_primary` e viraria 500);
            2. rebaixa o responsável atual (`is_primary = false`) — o índice
               parcial é checado por statement, então rebaixar vem ANTES;
            3. promove o alvo com `RETURNING`. Devolve `False` quando nenhuma
               linha foi promovida: o alvo perdeu o acesso entre a leitura e a
               escrita (DELETE concorrente). O service levanta, e o rollback do
               request desfaz o rebaixamento — sem isso o cliente ficaria sem
               responsável com um 200.
        Não mexe em `assigned_by`/`assigned_at` — a linha diz quem concedeu o
        ACESSO e quando; a promoção é outro evento.
        """
        s = self._session
        await s.execute(
            select(ClientAssignment.id)
            .where(ClientAssignment.client_id == client_id)
            .with_for_update()
        )
        await s.execute(
            update(ClientAssignment)
            .where(
                ClientAssignment.client_id == client_id,
                ClientAssignment.is_primary.is_(True),
            )
            .values(is_primary=False)
        )
        promoted = (
            await s.execute(
                update(ClientAssignment)
                .where(
                    ClientAssignment.client_id == client_id,
                    ClientAssignment.user_id == user_id,
                )
                .values(is_primary=True)
                .returning(ClientAssignment.id)
            )
        ).scalar_one_or_none()
        await s.flush()
        return promoted is not None

    async def delete_assignment_if_collaborator(self, *, client_id: UUID, user_id: UUID) -> bool:
        """Remove o acesso de UMA pessoa — só se ela NÃO for a responsável.

        A condição `is_primary = false` vai no próprio `DELETE`, não numa leitura
        anterior: um `/assign` concorrente que promova a pessoa entre a tela e o
        clique é recusado (zero linhas → `False`), não apagado. Core `DELETE` —
        relationships são `lazy="raise"`.
        """
        deleted = (
            await self._session.execute(
                delete(ClientAssignment)
                .where(
                    ClientAssignment.client_id == client_id,
                    ClientAssignment.user_id == user_id,
                    ClientAssignment.is_primary.is_(False),
                )
                .returning(ClientAssignment.id)
            )
        ).scalar_one_or_none()
        await self._session.flush()
        return deleted is not None

    # ------------------------- S7: cache L1 ---------------------------

    async def get_accounts_cache(self, client_id: UUID) -> Sequence[OmieAccountCache]:
        """Retorna todas as linhas do cache L1 do cliente, ordenadas por nome.

        Ordem por `name ASC` mantém a UI do detalhe estável entre requests
        (tela mostra cards das contas — sem ordem fixa, embaralha a cada hit
        no banco). `client_id` é indexado, então o ORDER BY não dói.
        """
        stmt = (
            select(OmieAccountCache)
            .where(OmieAccountCache.client_id == client_id)
            .order_by(OmieAccountCache.name.asc())
        )
        result = await self._session.execute(stmt)
        return result.scalars().all()

    async def replace_accounts_cache(
        self,
        client: Client,
        items: Sequence[OmieAccountCache],
        *,
        connection: ClientConnection | None = None,
    ) -> datetime:
        """Substitui o cache L1 do cliente por `items` em uma única transação.

        Estratégia escolhida (Doc §5.2):
            DELETE de todas as linhas do cliente + INSERT em massa, dentro da
            mesma transação do request. Vantagens vs. UPSERT por linha:
                - clean-slate: contas removidas no Omie somem do nosso cache.
                - idempotência trivial: rodar 2x deixa o estado idêntico.
                - sem dependência de `INSERT ... ON CONFLICT` (Postgres-only,
                  difícil de testar com SQLite).
            UNIQUE(client_id, omie_conta_id) protege contra race entre 2 syncs
            concorrentes — o segundo a chegar levanta IntegrityError, que o
            handler global converte em 409, e a UI tenta de novo.

        O `synced_at` final é gravado em `connection.accounts_synced_at` (S9) e,
        enquanto as colunas antigas existirem, TAMBÉM em
        `clients.omie_accounts_synced_at` — a coluna do cliente deixou de ser
        LIDA (`accounts_cache.get_or_sync` decide o TTL pela conexão), mas
        continua escrita como salvaguarda de rollback até o `contract`.

        Nunca derivar de `MAX(omie_accounts_cache.synced_at)`: quando o provedor
        devolve lista vazia o MAX volta `None`, o TTL não dispara e toda request
        bate na rede (bug descoberto em 29/04/2026 com Quial).

        Retorna o `synced_at` aplicado (mesmo timestamp para todas as linhas e
        para os dois carimbos).
        """
        await self._session.execute(
            delete(OmieAccountCache).where(OmieAccountCache.client_id == client.id)
        )
        synced_at = datetime.now(UTC)
        for item in items:
            item.synced_at = synced_at
            self._session.add(item)
        client.omie_accounts_synced_at = synced_at
        if connection is not None:
            # O carimbo que MANDA desde a S9. Por conexão: sincronizar uma não
            # pode marcar a outra do mesmo cliente como fresca.
            connection.accounts_synced_at = synced_at
            self._session.add(connection)
        # `add` em objeto já tracked é no-op, mas garante a presença na identity
        # map caso o caller tenha passado um Client detached por algum motivo.
        self._session.add(client)
        await self._session.flush()
        return synced_at

    # ------------------------- S7: histórico de conciliações ----------

    async def list_reconciliations_paginated(
        self,
        client_id: UUID,
        *,
        page: int,
        page_size: int,
        omie_conta_id: int | None = None,
        month_start: date | None = None,
        month_end: date | None = None,
        statuses: Sequence[str] | None = None,
    ) -> tuple[Sequence[tuple[ReconciliationSession, int]], int]:
        """Lista paginada das conciliações de UM cliente (S7 BACK 4.2 + BACK 04.3).

        Filtros opcionais, **combináveis com E**:
            - `omie_conta_id`: igual.
            - `month_start`/`month_end`: range half-open `[start, end)` para
              filtrar `reference_month` em um mês específico. Caller calcula
              `[YYYY-MM-01, mês+1-01)` para evitar erro de timezone/granularidade.
            - `statuses`: lista de status do BANCO já traduzida pelo service a
              partir do vocabulário do produto ("Processada" = reviewing OU
              done). O repository não conhece esse mapeamento.

        Devolve `(sessão, nº de arquivos)` por item. A contagem vem de uma
        **subquery correlata na mesma query** — contar arquivo por item num
        loop seria N+1 numa tela que pagina de 20 em 20 (guardrail do PRD: a
        lista não pode ficar mais lenta com a paginação).

        Os totalizadores por item saem das COLUNAS da sessão, materializadas
        pela fonte única (`totals.refresh_session_counters`) — a lista não
        recalcula nada, e por isso não diverge do detalhe.

        Ordem: `created_at DESC, id DESC` — desempate determinístico quando 2
        sessões caem no mesmo segundo (pode acontecer em testes).
        """
        files_count = (
            select(func.count(ReconciliationFile.id))
            .where(ReconciliationFile.session_id == ReconciliationSession.id)
            .correlate(ReconciliationSession)
            .scalar_subquery()
            .label("total_files")
        )

        # Esconde sessões descartadas (soft-delete). Sessões em error
        # descartadas pela UI não aparecem mais no histórico do cliente,
        # mas continuam no banco pra auditoria.
        base = (
            select(ReconciliationSession, files_count)
            # 86e2n39f1 — o card mostra QUEM criou; relationship é
            # `lazy="raise"`, então o autor entra por selectinload (1 query
            # extra pra página inteira, nunca N+1).
            .options(selectinload(ReconciliationSession.user))
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationSession.deleted_at.is_(None),
            )
        )
        count_base = (
            select(func.count(ReconciliationSession.id))
            .select_from(ReconciliationSession)
            .where(
                ReconciliationSession.client_id == client_id,
                ReconciliationSession.deleted_at.is_(None),
            )
        )

        if omie_conta_id is not None:
            base = base.where(ReconciliationSession.omie_conta_id == omie_conta_id)
            count_base = count_base.where(ReconciliationSession.omie_conta_id == omie_conta_id)

        if month_start is not None and month_end is not None:
            base = base.where(
                ReconciliationSession.reference_month >= month_start,
                ReconciliationSession.reference_month < month_end,
            )
            count_base = count_base.where(
                ReconciliationSession.reference_month >= month_start,
                ReconciliationSession.reference_month < month_end,
            )

        if statuses:
            base = base.where(ReconciliationSession.status.in_(statuses))
            count_base = count_base.where(ReconciliationSession.status.in_(statuses))

        base = base.order_by(
            ReconciliationSession.created_at.desc(),
            ReconciliationSession.id.desc(),
        )
        offset = (page - 1) * page_size
        base = base.offset(offset).limit(page_size)

        # `total` = COUNT com os MESMOS filtros (senão o rodapé "x-y de N"
        # mente quando há filtro ativo). Paginar DEPOIS de filtrar.
        total = (await self._session.execute(count_base)).scalar_one()
        result = await self._session.execute(base)
        rows = [(row[0], int(row[1])) for row in result.all()]
        return rows, int(total)
