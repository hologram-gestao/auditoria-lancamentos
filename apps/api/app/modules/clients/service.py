"""Lógica de negócio do CRUD de clientes BPO (S6 — BACK 3.1 a 3.5).

Responsabilidades:
    - Criptografia das credenciais Omie via AES-256-GCM (IV novo por operação).
    - Auto-assignment do criador na criação (Doc §9.2).
    - "Test connection" sem persistir nada (Doc §9.2 estados do botão).
    - Validações específicas: ambos os campos de credencial juntos no PATCH,
      gerente-alvo (adicionar / definir responsável) deve ser ativo e role=manager.
    - Carteira compartilhada (86e390kz8): N gerentes com ACESSO, UM responsável.
      Trocar o responsável não remove ninguém; remover o responsável sem
      substituto é recusado (cliente nunca fica órfão).

CLAUDE.md §3 (segurança crítica):
    - Credenciais NUNCA são logadas, retornadas em response, nem persistidas
      em claro. Sempre criptografar em memória, persistir, descartar.
    - Cada credencial gera SEU IV (12 bytes aleatórios) — NUNCA reutilizar.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import httpx
from pydantic import SecretStr

from app.core.authz import (
    CurrentUser,
    resolve_organization_filter,
    resolve_organization_for_creation,
)
from app.core.exceptions import (
    CannotRemoveResponsibleManagerError,
    ClientClosedError,
    ClientHasProcessingSessionError,
    IncompleteCredentialsError,
    InvalidClientCategoryError,
    InvalidManagerError,
    ManagerAlreadyAssignedError,
    ManagerNotAssignedError,
    NotFoundError,
    OmieAuthError,
    OmieFaultError,
    OmieServerError,
    OmieTimeoutError,
)
from app.db.models import Client, ClientAssignment, OmieAccountCache, User
from app.db.models.client_connection import ClientConnection, ProviderType
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.integrations.providers.base import Capability
from app.integrations.providers.omie_adapter import omie_credentials_payload
from app.modules.client_connections.capability import (
    connection_supports,
    select_capable_connection,
)
from app.modules.client_connections.schemas import ClientConnectionResponse
from app.modules.client_connections.service import ClientConnectionService
from app.modules.clients.accounts_cache import OmieAccountsCacheService
from app.modules.clients.repository import ClientRepository, ClientRow
from app.modules.clients.schemas import (
    UI_STATUS_TO_DB,
    BankAccountResponse,
    ClientCategorySummary,
    ClientDetailResponse,
    ClientManagerResponse,
    ClientResponse,
    ManagerSummary,
    OrganizationSummary,
    ReconciliationSessionSummary,
    TestConnectionResponse,
    derive_origin_status,
)
from app.modules.reconciliations.service import author_for_viewer
from app.modules.usage_events.service import UsageEventService
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.db.models import ReconciliationSession


def _row_to_response(row: ClientRow) -> ClientResponse:
    """Converte um `ClientRow` (cliente + manager + count) em response público.

    NUNCA inclui credenciais — `ClientResponse` não tem campo para isso.
    """
    manager = (
        ManagerSummary(id=row.manager.id, name=row.manager.name, email=row.manager.email)
        if row.manager is not None
        else None
    )
    return ClientResponse(
        id=row.client.id,
        name=row.client.name,
        active=row.client.active,
        organization=OrganizationSummary(id=row.organization.id, name=row.organization.name),
        created_at=row.client.created_at,
        updated_at=row.client.updated_at,
        responsible_manager=manager,
        reconciliation_count=row.reconciliation_count,
        is_favorite=row.is_favorite,
        manager_count=row.manager_count,
        # 86e36pm1z — encerrado: o front esconde ações e mostra o selo.
        closed_at=row.client.closed_at,
        category=(
            ClientCategorySummary(
                id=row.category.id, name=row.category.name, tone=row.category.tone
            )
            if row.category is not None
            else None
        ),
        # S9 (BACK 09.4): derivado das contagens que vieram na MESMA query.
        origin_status=derive_origin_status(
            total=row.connections_total, active=row.connections_active
        ),
    )


class ClientService:
    """CRUD + regras de negócio para `clients`."""

    def __init__(
        self,
        repository: ClientRepository,
        settings: Settings,
        *,
        accounts_cache: OmieAccountsCacheService | None = None,
        usage_events: UsageEventService | None = None,
        connections: ClientConnectionService | None = None,
    ) -> None:
        self._repo = repository
        self._settings = settings
        # Sink de métrica da exclusão (86e34jd1d). Opcional: sem ele, o fato
        # não é medido, mas a exclusão acontece — instrumentação nunca bloqueia.
        self._usage_events = usage_events
        # Cache L1 — instanciado on-demand quando não passado pelo caller.
        # Em testes é injetado com `OmieClient` mockado via respx.
        self._accounts_cache = accounts_cache or OmieAccountsCacheService(repository, settings)
        # S9 (BACK 09.4): o MESMO serviço da 09.3, para o `POST /clients` com
        # credencial criar a conexão pelo caminho único — que valida contra o
        # provedor antes de persistir. Opcional só na assinatura: o ramo "com
        # credencial" exige (a rota sempre passa). Diferente do `usage_events`,
        # aqui a ausência NÃO degrada em silêncio.
        self._connections = connections

    # ------------------------------ READ ------------------------------

    async def list_clients(
        self,
        *,
        user: CurrentUser,
        page: int,
        page_size: int,
        search: str | None,
        category_id: UUID | None = None,
        requested_organization_id: UUID | None = None,
    ) -> tuple[list[ClientResponse], PaginationMeta]:
        """Lista clientes dentro do ALCANCE do usuário (`authz.reach_filter`).

        O repositório põe a decisão única no SELECT; aqui só se repassa a LINHA
        do usuário — favoritos e alcance derivam dela, nunca da rota. O
        `?organizationId=` passa por `resolve_organization_filter`: vale para a
        plataforma; para o staff, ou é a própria (no-op) ou é 403 (86e36ecqz).
        """
        organization_id = resolve_organization_filter(user, requested_organization_id)
        rows, total = await self._repo.list_paginated(
            user=user,
            page=page,
            page_size=page_size,
            search=search,
            category_id=category_id,
            organization_id=organization_id,
        )
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        responses = [_row_to_response(r) for r in rows]
        pagination = PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )
        return responses, pagination

    async def get_client_detail(
        self, client_id: UUID, *, viewer_user_id: UUID | None = None
    ) -> ClientResponse:
        """Retorna ClientResponse já preenchido com manager + count.

        `viewer_user_id` resolve `is_favorite` (86e34jd5a): toda response que o
        front guarda em cache precisa refletir o favorito de quem pede, senão
        um PATCH ou um sync devolveria o coração apagado.

        404 se cliente não existe — caller tipicamente já passou por
        `require_client_access` (que retorna 403/404 antes), mas mantemos o
        guard aqui para reuso fora desse contexto.
        """
        row = await self._repo.get_detail(client_id, viewer_user_id=viewer_user_id)
        if row is None:
            raise NotFoundError("Cliente não encontrado.")
        return _row_to_response(row)

    # ------------------------------ CREATE ----------------------------

    async def create_client(
        self,
        *,
        name: str,
        omie_app_key: str | None,
        omie_app_secret: str | None,
        actor: CurrentUser,
        requested_organization_id: UUID | None,
        category_id: UUID | None = None,
    ) -> ClientResponse:
        """Cria o cliente. A credencial é OPCIONAL desde a Sprint 9 (BACK 09.4).

        **Dois ramos, uma transação:**

        - **Sem credencial** — o cliente nasce pleno e SEM origem: as 4 colunas
          antigas ficam `NULL` e **nenhuma DEK é provisionada**. Quem provisiona
          é a primeira conexão (09.3); gerar DEK aqui criaria chave para um
          cliente que talvez nunca cifre nada.
        - **Com credencial** — o cliente nasce e, na MESMA transação,
          `ClientConnectionService` cria a conexão `omie` com o rótulo padrão.
          A validação contra o provedor acontece ANTES do commit: recusada, o
          service levanta e **nem `clients` nem `client_connections` ganham
          linha**. As colunas antigas continuam `NULL` — a credencial mora na
          conexão, que é o caminho novo.

        A organização é decidida por `resolve_organization_for_creation` (§3.15):
        staff cria na PRÓPRIA org (a da LINHA; `organization_id` divergente no
        payload é 403, nunca ignorado); a plataforma escolhe, e a escolha é
        obrigatória e validada (org existe e está ativa).

        Emite `cliente_criado` nos DOIS ramos — é a métrica da sprint, e sem o
        ramo "com origem" a leitura não teria denominador.
        """
        if (omie_app_key is None) != (omie_app_secret is None):
            raise IncompleteCredentialsError(
                "POST /clients: app_key e app_secret precisam vir juntos.",
            )

        organization_id = await resolve_organization_for_creation(
            actor,
            requested_organization_id,
            get_organization=self._repo.get_organization,
            subject="o cliente",
        )
        await self._assert_category_exists(category_id, organization_id=organization_id)
        current_user_id = UUID(actor.id)

        client = Client(
            id=uuid4(),
            name=name,
            active=True,
            created_by=current_user_id,
            category_id=category_id,
            organization_id=organization_id,
        )
        await self._repo.add_client(client)

        # Carteira (86e390kz8): só GERENTE entra. Quem cria sendo manager vira
        # o RESPONSÁVEL. Admin e plataforma já alcançam tudo pela matriz e não
        # entram na carteira — nem como responsável provisório: o cliente nasce
        # sem responsável e o primeiro gerente adicionado assume
        # (`add_client_manager`), ou o admin define pelo `/assign`. A carteira é
        # intra-org (86e36ecjp): o criador só entra se for gerente DA org do cliente.
        if await self._repo.is_active_manager(current_user_id, organization_id=organization_id):
            assignment = ClientAssignment(
                client_id=client.id,
                user_id=current_user_id,
                assigned_by=current_user_id,
                is_primary=True,
            )
            await self._repo.add_assignment(assignment)

        tipo_conexao: str | None = None
        if omie_app_key is not None and omie_app_secret is not None:
            # O MESMO serviço da 09.3 — nenhum segundo caminho de escrita de
            # credencial. Ele verifica no provedor antes de persistir; se
            # recusar, a exceção sobe e a transação do request inteira é
            # desfeita (o cliente recém-criado some junto).
            if self._connections is None:  # pragma: no cover - guarda de montagem
                raise RuntimeError("ClientService sem ClientConnectionService para criar conexão.")
            await self._connections.create_connection(
                client=client,
                user=actor,
                provider_type=ProviderType.OMIE.value,
                label=None,
                credentials=omie_credentials_payload(omie_app_key, omie_app_secret),
            )
            tipo_conexao = ProviderType.OMIE.value

        if self._usage_events is not None:
            await self._usage_events.emit_cliente_criado(
                client_id=client.id,
                organization_id=organization_id,
                tem_conexao=tipo_conexao is not None,
                tipo_conexao=tipo_conexao,
            )

        return await self.get_client_detail(client.id, viewer_user_id=current_user_id)

    # ------------------------------ UPDATE ----------------------------

    async def update_client(
        self,
        client: Client,
        *,
        name: str | None,
        active: bool | None,
        viewer_user_id: UUID | None = None,
        category_id: UUID | None = None,
        category_set: bool = False,
    ) -> ClientResponse:
        """Atualiza campos parciais do cliente (PATCH).

        `category_set` distingue "omitido" (mantém) de `null` explícito (limpa) —
        86e34jd8m; UUID troca depois de validar que existe no catálogo.

        ⚠️ **Credencial saiu daqui na Sprint 9 (BACK 09.3).** A origem virou
        entidade própria (`client_connections`) e a escrita passa por
        `ClientConnectionService`, que verifica contra o provedor ANTES de
        gravar. Este caminho não verificava nada — manter os dois seria manter
        duas verdades sobre a mesma credencial, sendo que uma delas grava
        qualquer coisa. O 422 que orienta quem ainda manda os campos antigos
        está no `UpdateClientRequest`, não aqui: é validação de entrada.
        """
        if name is not None:
            client.name = name
        if active is not None:
            client.active = active
        if category_set:
            await self._assert_category_exists(category_id, organization_id=client.organization_id)
            client.category_id = category_id

        await self._repo.add_client(client)
        return await self.get_client_detail(client.id, viewer_user_id=viewer_user_id)

    async def _assert_category_exists(
        self, category_id: UUID | None, *, organization_id: UUID
    ) -> None:
        """400 se o `category_id` não está no catálogo DA ORGANIZAÇÃO do cliente
        (86e34jd8m + 86e36ecqz). `None` passa. Categoria de outra organização
        recebe o MESMO 400 de inexistente — sem oráculo entre BPOs."""
        if category_id is None:
            return
        if (
            await self._repo.get_category_by_id(category_id, organization_id=organization_id)
            is None
        ):
            raise InvalidClientCategoryError(
                f"Categoria inexistente na organização {organization_id}: {category_id}"
            )

    # --------------------- CARTEIRA COMPARTILHADA (86e390kz8) ----------
    #
    # Três ações, nenhuma delas "reatribuir": ADICIONAR acesso, REMOVER acesso
    # e DEFINIR o responsável. A que existia (sobrescrever o `user_id` da linha
    # única) tirou o acesso da Bruna sem aviso em 14/09/2026 — não existe mais.
    # Quem decide o acesso ao tenant é a rota (`AccessibleClientDep`/
    # `OpenClientDep`); aqui só a regra.

    async def list_client_managers(self, client_id: UUID) -> list[ClientManagerResponse]:
        """Todos com acesso ao cliente — responsável primeiro, depois por nome."""
        rows = await self._repo.list_assignments_with_users(client_id)
        return [_assignment_to_response(assignment, user) for assignment, user in rows]

    async def add_client_manager(
        self, client: Client, *, user_id: UUID, current_admin_id: UUID
    ) -> list[ClientManagerResponse]:
        """Concede ACESSO a um gerente (colaborador). Admin-only.

        400 se o alvo não é manager ativo; 409 se já tem acesso — a dedup é do
        banco (`ON CONFLICT DO NOTHING` na UNIQUE do par), então duas requisições
        simultâneas produzem uma linha e um 409, nunca duas linhas. Cliente SEM
        responsável (criado por admin): o primeiro gerente que entra assume.
        """
        await self._assert_active_manager(user_id, organization_id=client.organization_id)
        inserted = await self._repo.add_assignment_if_absent(
            client_id=client.id, user_id=user_id, assigned_by=current_admin_id
        )
        if not inserted:
            raise ManagerAlreadyAssignedError(
                f"User {user_id} já tem acesso ao cliente {client.id}."
            )
        if not await self._repo.has_responsible(client.id):
            await self._repo.set_primary_assignment(client_id=client.id, user_id=user_id)
        return await self.list_client_managers(client.id)

    async def remove_client_manager(
        self, client: Client, *, user_id: UUID
    ) -> list[ClientManagerResponse]:
        """Remove o ACESSO de um gerente. Admin-only.

        404 se a pessoa não tem acesso; 409 se é o RESPONSÁVEL — cliente nunca
        fica órfão: define-se outro responsável antes (`set_responsible_manager`,
        que não remove ninguém) e só então o antigo pode sair. A condição "não
        é o responsável" está no próprio DELETE (`delete_assignment_if_collaborator`):
        o 409/404 é decidido pelo estado ATUAL, depois da escrita condicional.
        """
        removed = await self._repo.delete_assignment_if_collaborator(
            client_id=client.id, user_id=user_id
        )
        if not removed:
            assignment = await self._repo.get_assignment_for_user(client.id, user_id)
            if assignment is None:
                raise ManagerNotAssignedError(
                    f"User {user_id} não tem acesso ao cliente {client.id}."
                )
            raise CannotRemoveResponsibleManagerError(
                f"User {user_id} é o responsável pelo cliente {client.id}; "
                "defina outro responsável antes de remover."
            )
        return await self.list_client_managers(client.id)

    async def set_responsible_manager(
        self, client: Client, *, user_id: UUID, current_admin_id: UUID
    ) -> ClientResponse:
        """Define quem RESPONDE pelo cliente. Admin-only. Não remove ninguém.

        Se o alvo ainda não tinha acesso, passa a ter (como colaborador) e é
        promovido na sequência; se já era o responsável, é no-op. O responsável
        anterior continua na carteira como colaborador — o oposto do antigo
        "reatribuir", que o apagava. Idempotente de ponta a ponta, sem lookup:
        quem já tem acesso não é inserido de novo (ON CONFLICT DO NOTHING) e
        promover quem já é o responsável rebaixa e promove a mesma linha.
        """
        await self._assert_active_manager(user_id, organization_id=client.organization_id)
        await self._repo.add_assignment_if_absent(
            client_id=client.id, user_id=user_id, assigned_by=current_admin_id
        )
        if not await self._repo.set_primary_assignment(client_id=client.id, user_id=user_id):
            # O alvo perdeu o acesso entre a inserção e a promoção (DELETE
            # concorrente). Levantar desfaz o rebaixamento no rollback do request.
            raise ManagerNotAssignedError(
                f"User {user_id} perdeu o acesso ao cliente {client.id} durante a operação."
            )
        return await self.get_client_detail(client.id, viewer_user_id=current_admin_id)

    async def _assert_active_manager(self, user_id: UUID, *, organization_id: UUID) -> None:
        """400 se o alvo não existe, está inativo, não é `manager` OU é de outra
        organização (S6 §3.5 + 86e36ecjp: a carteira é intra-org).

        Um só código para os quatro casos, de propósito: distinguir "gerente de
        outra organização" diria a um admin que aquele id existe e é gerente em
        algum lugar (anti-enumeração, §3.15). Admin e plataforma não entram na
        carteira — nem por aqui, nem na criação do cliente: já alcançam os
        clientes pela matriz, e contá-los em `manager_count` mentiria sobre quem
        opera a carteira.
        """
        if not await self._repo.is_active_manager(user_id, organization_id=organization_id):
            raise InvalidManagerError(
                f"User {user_id} não é manager ativo da organização {organization_id} (recusado)."
            )

    # ------------------------------ TEST CONNECTION -------------------

    async def test_connection(
        self,
        *,
        omie_app_key: str,
        omie_app_secret: str,
    ) -> TestConnectionResponse:
        """Valida credenciais sem persistir nada (S6 §3.3).

        Cria um httpx client temporário com `OMIE_TEST_CONNECTION_TIMEOUT_SECONDS`
        (mais agressivo que o default), faz `listar_clientes_minimal()` e mapeia
        os 3 modos de falha (auth / timeout / fault genérico) para `ok=False`
        com mensagem em PT-BR — UI não distingue.

        NUNCA loga as credenciais (o redactor já cobre, mas aqui também não há
        log da operação para reduzir blast radius).
        """
        creds = OmieCredentials(
            app_key=SecretStr(omie_app_key),
            app_secret=SecretStr(omie_app_secret),
        )
        timeout = float(self._settings.OMIE_TEST_CONNECTION_TIMEOUT_SECONDS)
        async with httpx.AsyncClient(timeout=timeout) as http:
            omie = OmieClient(creds, self._settings, http_client=http)
            try:
                await omie.listar_clientes_minimal()
            except OmieAuthError:
                return TestConnectionResponse(
                    ok=False,
                    message="Credenciais Omie inválidas",
                )
            except OmieTimeoutError:
                return TestConnectionResponse(
                    ok=False,
                    message="O Omie não respondeu no tempo esperado",
                )
            except OmieServerError:
                return TestConnectionResponse(
                    ok=False,
                    message="O Omie está com instabilidade no momento",
                )
            except OmieFaultError as exc:
                return TestConnectionResponse(ok=False, message=exc.user_message)
        return TestConnectionResponse(ok=True, message="Conexão estabelecida com sucesso")

    # ------------------------------ S7: detalhe + cache L1 ------------

    async def get_client_detail_with_accounts(
        self, client: Client, *, viewer_user_id: UUID | None = None
    ) -> ClientDetailResponse:
        """Detalhe completo: Client + manager + count + contas do cache (Endpoint A).

        TTL de 24 h decidido dentro do `OmieAccountsCacheService`. Se o cache
        miss falhar (Omie indisponível), `AccountsSyncError` propaga e o
        handler global retorna 502 — alinhado ao padrão do test-connection.
        """
        connections = await self._load_connections(client)
        if client.closed_at is not None:
            # 86e36pm1z — encerrado NÃO fala com o Omie: as credenciais foram
            # destruídas e o cache purgado. O detalhe volta sem contas, só com
            # o histórico retido (sem isso, o miss do cache tentaria decifrar
            # credencial vazia e viraria 500).
            return await self._build_detail_response(
                client.id, [], None, connections, viewer_user_id=viewer_user_id
            )
        capable = self._first_capable(connections, Capability.LISTAR_CONTAS)
        if capable is None:
            # S9 (BACK 09.4): cliente SEM origem capaz de listar contas não
            # bate no provedor. Este endpoint responde 200 sempre — 409 aqui
            # impediria o parceiro de abrir a tela do cliente que acabou de
            # cadastrar, e um `get_or_sync` sem credencial viraria 500.
            return await self._build_detail_response(
                client.id, [], None, connections, viewer_user_id=viewer_user_id
            )
        rows, synced_at = await self._accounts_cache.get_or_sync(client, capable)
        return await self._build_detail_response(
            client.id, rows, synced_at, connections, viewer_user_id=viewer_user_id
        )

    async def _load_connections(self, client: Client) -> list[ClientConnection]:
        """As origens EFETIVAS do cliente — com o fallback da 09.5 aplicado.

        `resolve_origins` e não `list_rows`: o cliente ainda não convertido
        precisa operar, e é a conexão sintetizada que o mantém de pé.
        """
        if self._connections is None:  # pragma: no cover - montagem sem conexões
            return []
        return await self._connections.resolve_origins(client)

    @staticmethod
    def _first_capable(
        connections: Sequence[ClientConnection], capability: Capability
    ) -> ClientConnection | None:
        """A primeira conexão capaz, ou `None`. Sem levantar.

        Existe porque o DETALHE do cliente responde **200 sempre** (ADR-044-BE):
        ele precisa saber se dá para sincronizar, não de uma exceção. Quem
        levanta o 409 certo é `select_capable_connection`, usado no sync manual.

        O predicado é o ÚNICO da 09.2 (`connection_supports`) — nunca um
        `status == 'ativa'` escrito aqui.

        ⚠️ A lista já vem de `resolve_origin_connections` (09.5), que inclui a
        conexão SINTETIZADA do cliente ainda não convertido. Por isso não há
        mais um "ou tem credencial nas colunas antigas": o fallback já resolveu.
        """
        return next((c for c in connections if connection_supports(c, capability)), None)

    async def force_sync_accounts(
        self, client: Client, *, viewer_user_id: UUID | None = None
    ) -> ClientDetailResponse:
        """Endpoint B: força sync ignorando TTL e retorna o detalhe completo.

        Ao contrário do detalhe, aqui o sync é o PEDIDO do usuário: sem origem
        capaz, `select_capable_connection` (09.2) decide o 409 acionável — não
        se devolve "sincronizado, zero contas" para quem clicou em sincronizar.
        """
        connections = await self._load_connections(client)
        # Levanta o 409 CERTO dos três (sem_conexao / origem_com_erro /
        # capacidade_ausente) a partir do estado real das conexões.
        capable = select_capable_connection(connections, Capability.LISTAR_CONTAS)
        rows, synced_at = await self._accounts_cache.force_sync(client, capable)
        return await self._build_detail_response(
            client.id, rows, synced_at, connections, viewer_user_id=viewer_user_id
        )

    # ------------------------------ EXCLUSÃO (86e34jd1d) --------------

    async def delete_client(self, client: Client) -> None:
        """Exclusão DEFINITIVA do cliente e de tudo que pende dele.

        Decisão do Galhardo (04/09/2026): quando o cliente sai da carteira, some
        da plataforma; se voltar, integra de novo. O backup do que foi tratado
        fica no Drive do cliente — fora da ADL.

        Guarda: 409 se houver conciliação EM PROCESSAMENTO — o job roda fora do
        request e morreria no meio. Quem decide o acesso é a rota (admin pela
        matriz + tenant pelo `AccessibleClientDep`); aqui só a regra.
        """
        if await self._repo.count_sessions(client.id, processing_only=True) > 0:
            raise ClientHasProcessingSessionError(
                f"Cliente {client.id} tem conciliação em processamento; exclusão recusada."
            )
        n_sessions = await self._repo.count_sessions(client.id)
        n_users = await self._repo.count_tenant_users(client.id)
        client_id = client.id
        await self._repo.delete_client_cascade(client)
        if self._usage_events is not None:
            await self._usage_events.emit_cliente_excluido(
                client_id=client_id, n_conciliacoes=n_sessions, n_usuarios=n_users
            )

    # --------------------- ENCERRAMENTO (86e36pm1z) --------------------

    async def close_client(self, client: Client) -> None:
        """Encerramento com RETENÇÃO — o irmão da exclusão (decisão 09/09/2026).

        Apaga quem o cliente É e mantém o que ACONTECEU:
            - nome → rótulo anônimo; credenciais Omie → vazias;
            - `dek_wrapped` → NULL: crypto-shredding (§4.1) — todo o conteúdo
              cifrado do tenant (descrições, notas, contexto de anomalias) vira
              irrecuperável de uma vez, sem varrer tabela por tabela;
            - usuários do tenant anonimizados + desativados (FK impede apagar);
            - glossário/cache/notificações/favoritos removidos;
            - conciliações, valores, datas, categoria, carteira, `usage_events`
              e `access_audit` FICAM — a retenção é o propósito.

        Terminal: cliente que voltar é cadastro NOVO. Mesmo guard de 409 da
        exclusão para conciliação em processamento; encerrar duas vezes é 409.
        A exclusão total continua disponível para cliente encerrado (LGPD).
        """
        if client.closed_at is not None:
            raise ClientClosedError(f"Cliente {client.id} já está encerrado.")
        if await self._repo.count_sessions(client.id, processing_only=True) > 0:
            raise ClientHasProcessingSessionError(
                f"Cliente {client.id} tem conciliação em processamento; encerramento recusado."
            )
        n_sessions = await self._repo.count_sessions(client.id)
        n_users = await self._repo.count_tenant_users(client.id)

        # Scrub da identidade + crypto-shredding. A instância está anexada à
        # sessão do request: o UPDATE sai no commit, junto com o resto — atômico.
        client.name = f"Cliente encerrado #{client.id.hex[:8]}"
        client.omie_app_key_encrypted = ""
        client.omie_app_key_iv = ""
        client.omie_app_secret_encrypted = ""
        client.omie_app_secret_iv = ""
        client.dek_wrapped = None
        client.active = False
        client.closed_at = datetime.now(UTC)

        await self._repo.anonymize_tenant_users(client.id)
        await self._repo.close_client_purge(client.id)
        if self._usage_events is not None:
            await self._usage_events.emit_cliente_encerrado(
                client_id=client.id, n_conciliacoes=n_sessions, n_usuarios=n_users
            )

    # ------------------------------ FAVORITOS (86e34jd5a) -------------

    async def set_favorite(
        self, client: Client, *, user_id: UUID, favorite: bool
    ) -> ClientResponse:
        """Marca/desmarca o cliente como favorito DE `user_id` e devolve o cliente.

        Preferência por usuário: não altera o cliente nem passa pela matriz de
        edição — quem enxerga o cliente (`resolve_client_access`, já aplicado
        pela rota) pode favoritá-lo. Idempotente nos dois sentidos.
        """
        if favorite:
            await self._repo.add_favorite(user_id=user_id, client_id=client.id)
        else:
            await self._repo.remove_favorite(user_id=user_id, client_id=client.id)
        return await self.get_client_detail(client.id, viewer_user_id=user_id)

    async def list_reconciliations(
        self,
        client_id: UUID,
        *,
        page: int,
        page_size: int,
        omie_conta_id: int | None,
        month: str | None,
        status: str | None = None,
        viewer: CurrentUser,
    ) -> tuple[list[ReconciliationSessionSummary], PaginationMeta]:
        """Lista paginada das conciliações do cliente (S7 BACK 4.2 + BACK 04.3).

        `month` chega como `'YYYY-MM'`. Convertemos pra range half-open
        `[YYYY-MM-01, próximo-mês-01)` no service — repository fica agnóstico.
        Mês inválido (formato errado, valores fora de range) → 400.

        `status` chega no vocabulário do PRODUTO ("Em processamento" /
        "Processada" / "Erro") e é traduzido AQUI para os status do banco —
        `processed` cobre `reviewing` E `done`. A tradução mora no service
        porque é regra de produto, não de persistência; o repository recebe a
        lista já resolvida.
        """
        month_start, month_end = _parse_month_range(month)
        statuses = UI_STATUS_TO_DB.get(status) if status else None
        rows, total = await self._repo.list_reconciliations_paginated(
            client_id,
            page=page,
            page_size=page_size,
            omie_conta_id=omie_conta_id,
            month_start=month_start,
            month_end=month_end,
            statuses=statuses,
        )
        responses = [
            _session_to_summary(session, total_files, viewer=viewer)
            for session, total_files in rows
        ]
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        pagination = PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )
        return responses, pagination

    # ------------------------------ INTERNALS -------------------------

    async def _build_detail_response(
        self,
        client_id: UUID,
        rows: Sequence[OmieAccountCache],
        synced_at: datetime | None,
        connections: Sequence[ClientConnection] = (),
        *,
        viewer_user_id: UUID | None = None,
    ) -> ClientDetailResponse:
        """Compõe `ClientDetailResponse` a partir de Client + manager + cache + origens."""
        base = await self.get_client_detail(client_id, viewer_user_id=viewer_user_id)
        accounts = [BankAccountResponse.model_validate(r) for r in rows]
        return ClientDetailResponse(
            **base.model_dump(),
            accounts=accounts,
            accounts_synced_at=synced_at,
            connections=[ClientConnectionResponse.from_connection(c) for c in connections],
        )


# ----------------------------------------------------------------------
# Helpers de módulo (puros — não dependem do service)
# ----------------------------------------------------------------------


def _assignment_to_response(assignment: ClientAssignment, user: User) -> ClientManagerResponse:
    """Mapeia (vínculo, usuário) → DTO de gestão: `ManagerSummary` + `active` + carteira.

    Nunca a linha de `users` (§3.2): o `id` entra porque é o alvo do
    `DELETE .../managers/{user_id}`, e `active` porque um responsável desativado
    precisa ser visível para o admin passar o bastão.
    """
    return ClientManagerResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        active=user.active,
        is_responsible=assignment.is_primary,
        assigned_at=assignment.assigned_at,
    )


def _session_to_summary(
    session: ReconciliationSession,
    total_files: int,
    *,
    viewer: CurrentUser,
) -> ReconciliationSessionSummary:
    """Mapeia ORM `ReconciliationSession` → DTO `ReconciliationSessionSummary`.

    `total_files` vem da subquery da listagem (não de um acesso a relationship —
    todos são `lazy="raise"`, e seria N+1 mesmo que não fossem). O AUTOR
    (86e2n39f1) vem do `selectinload` da própria listagem, já mascarado por
    escopo — o `model_validate` NUNCA pode serializar o relationship `user`
    inteiro (§3.2), por isso o campo entra pelo `model_copy`, nunca por
    atributo homônimo.
    """
    summary = ReconciliationSessionSummary.model_validate(session, from_attributes=True)
    return summary.model_copy(
        update={
            "total_files": total_files,
            "created_by": author_for_viewer(session.user, viewer),
        }
    )


def _parse_month_range(month: str | None) -> tuple[date | None, date | None]:
    """Converte `'YYYY-MM'` em range `[start, end)`. None → `(None, None)`.

    Raises:
        ValidationAppError: formato inválido. Levanta ValueError aqui — caller
        já valida pelo Pydantic Query (regex), então este caminho é defensivo.
    """
    if month is None:
        return None, None
    try:
        year_str, mon_str = month.split("-", 1)
        year = int(year_str)
        mon = int(mon_str)
        start = date(year, mon, 1)
    except (ValueError, IndexError) as exc:
        raise ValueError(f"Mês inválido: {month!r} (esperado YYYY-MM).") from exc
    # Próximo mês — sem timedelta porque "+1 month" não é constante em dias
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start, end
