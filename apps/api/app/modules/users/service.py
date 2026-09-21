"""Lógica de negócio do CRUD de usuários (admin-only).

Regras (Doc §8 + CLAUDE.md):
    - E-mail único por usuário (case-insensitive).
    - Admin não pode desativar nem rebaixar a si mesmo (Doc §8.2-8.5).
    - Senha hasheada com bcrypt cost ≥12 (`app.core.security.hash_password`).
    - Email é normalizado para lower-case ao persistir.
    - Update é PATCH (parcial): só campos enviados são alterados.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authz import (
    CurrentUser,
    resolve_organization_filter,
    resolve_organization_for_creation,
)
from app.core.exceptions import (
    CannotDeactivateSelfError,
    EmailAlreadyExistsError,
    ForbiddenError,
    NotFoundError,
    OrganizationNotFoundError,
    UserAlreadyInOrganizationError,
    UserIsPrimaryManagerError,
)
from app.core.security import hash_password
from app.db.models import ClientUserRole, User, UserRole, UserScope
from app.modules.usage_events.service import UsageEventService
from app.modules.users.repository import StaffRow, UserRepository
from app.modules.users.schemas import PaginationMeta


def _primary_manager_message(n: int) -> str:
    plural = "s" if n != 1 else ""
    return (
        f"Este usuário é o gerente responsável de {n} cliente{plural} aberto{plural}. "
        "Defina outro responsável antes de transferir."
    )


class UserService:
    """CRUD + regras de negócio para `users`."""

    def __init__(
        self,
        repository: UserRepository,
        usage_events: UsageEventService | None = None,
    ) -> None:
        self._repo = repository
        # Opcional para não obrigar todo caller a montar o serviço de eventos;
        # a rota injeta (como o módulo de organizações).
        self._usage_events = usage_events

    # ------------------------------ READ ------------------------------

    async def list_users(
        self,
        *,
        viewer: CurrentUser,
        page: int,
        page_size: int,
        search: str | None = None,
        requested_organization_id: UUID | None = None,
        role: UserRole | None = None,
    ) -> tuple[list[StaffRow], PaginationMeta]:
        """Staff da organização do observador (plataforma: de todas) — nunca
        usuários de cliente nem a própria plataforma (86e36ecar).

        `requested_organization_id` (`?organizationId=`) passa por
        `resolve_organization_filter`: vale para a plataforma; para o admin, ou
        é a própria (no-op) ou é 403 — nunca ignorado em silêncio (86e36ecqz).
        """
        organization_id = resolve_organization_filter(viewer, requested_organization_id)
        rows, total = await self._repo.list_staff_paginated(
            viewer=viewer,
            page=page,
            page_size=page_size,
            search=search,
            organization_id=organization_id,
            role=role.value if role is not None else None,
        )
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return rows, PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )

    async def get_user(self, user_id: UUID, *, viewer: CurrentUser) -> StaffRow:
        """Staff alvo dentro do alcance do observador — 404 fora dele (anti-IDOR)."""
        row = await self._repo.get_staff_by_id(user_id, viewer=viewer)
        if row is None:
            raise NotFoundError("Usuário não encontrado.")
        return row

    # ------------------------------ CREATE ----------------------------

    async def create_user(
        self,
        *,
        viewer: CurrentUser,
        name: str,
        email: str,
        password: str,
        role: UserRole,
        requested_organization_id: UUID | None = None,
    ) -> StaffRow:
        """Cria staff ativo com senha hasheada, na organização decidida pela LINHA.

        Email único — 409 se duplicado. A organização vem de
        `resolve_organization_for_creation` (§3.15): o admin cria na própria
        (um `organization_id` diferente no payload é 403, nunca ignorado); a
        plataforma escolhe, e a escolha é obrigatória e validada (existe e está
        ativa). `role` já chega restrito a `admin`/`manager` pelo schema —
        `platform_admin` não entra em whitelist de API nenhuma (nasce só por
        `scripts/promote_platform_admin.py`).
        """
        organization_id = await resolve_organization_for_creation(
            viewer,
            requested_organization_id,
            get_organization=self._repo.get_organization,
            subject="o usuário",
        )
        normalized_email = email.lower()
        existing = await self._repo.get_by_email(normalized_email)
        if existing is not None:
            raise EmailAlreadyExistsError(
                f"E-mail já existe: {normalized_email}",
            )

        user = User(
            name=name,
            email=normalized_email,
            password_hash=hash_password(password),
            role=role.value,
            active=True,
            organization_id=organization_id,
        )
        await self._repo.add(user)
        # Nome da org sem query nova: o admin cria na própria (já no `CurrentUser`);
        # a plataforma acabou de validar a organização escolhida.
        if viewer.is_platform:
            organization = await self._repo.get_organization(organization_id)
            organization_name = organization.name if organization is not None else None
        else:
            organization_name = viewer.organization_name
        return StaffRow(user=user, organization_name=organization_name)

    # ------------------------------ UPDATE ----------------------------

    async def update_user(
        self,
        user_id: UUID,
        *,
        viewer: CurrentUser,
        name: str | None = None,
        email: str | None = None,
        role: UserRole | None = None,
    ) -> StaffRow:
        """Atualiza campos parcialmente. Bloqueios:
        - Admin NÃO pode rebaixar a si mesmo para manager (Doc §8.4).
        - E-mail só pode mudar se não conflitar com outro usuário.
        - Alvo fora do alcance (outra org, plataforma, usuário de cliente) → 404.
        """
        current_user_id = UUID(viewer.id)
        row = await self.get_user(user_id, viewer=viewer)
        user = row.user

        if email is not None:
            normalized_email = email.lower()
            if normalized_email != user.email:
                conflict = await self._repo.get_by_email(normalized_email)
                if conflict is not None:
                    raise EmailAlreadyExistsError(
                        f"E-mail já existe: {normalized_email}",
                    )
                user.email = normalized_email

        if name is not None:
            user.name = name

        if role is not None:
            # Admin não pode rebaixar a si mesmo (Doc §8.4)
            if (
                user.id == current_user_id
                and user.role == UserRole.ADMIN.value
                and role != UserRole.ADMIN
            ):
                raise ForbiddenError(
                    "Admin não pode rebaixar o próprio perfil.",
                    user_message="Você não pode rebaixar seu próprio perfil de administrador.",
                )
            user.role = role.value

        # SQLAlchemy detecta mudanças automaticamente — flush para persistir
        # antes de retornar (atualiza updated_at via onupdate).
        await self._repo.add(user)
        return StaffRow(user=user, organization_name=row.organization_name)

    # ------------------------------ TRANSFER --------------------------

    async def transfer_user(
        self,
        user_id: UUID,
        *,
        viewer: CurrentUser,
        organization_id: UUID,
    ) -> StaffRow:
        """Move um staff para OUTRA organização — só plataforma (86e3bvbfx).

        Não é edição de campo: `client_assignments` e `user_client_favorites`
        são pares usuárioxcliente, e trocar só a organização deixaria lixo
        cross-org (o usuário apareceria como responsável de clientes da
        organização antiga). Por isso:

        - alvo fora do alcance de staff (usuário de cliente, plataforma) → 404
          pelo `get_staff_by_id`, como todo alvo por PK;
        - mesma organização → 409; destino inexistente → 404; suspensa → 409
          (as MESMAS respostas da criação de cliente pela plataforma);
        - RESPONSÁVEL de cliente aberto → 409 e nada muda: a plataforma define
          outro responsável antes (cliente nunca fica órfão, §4.13);
        - colaborador em cliente aberto e favoritos fora da organização nova
          são removidos NA MESMA transação; linhas em cliente encerrado ficam
          (retenção, §4.12); histórico (`created_by`, `assigned_by`, trilha)
          fica; o papel não muda;
        - efeito imediato: a autoridade é a linha lida a cada request (§3.15),
          então o JWT antigo já vale para a organização nova.

        O guard da rota é `ManagePlatformDep`; a decisão de alcance por PK
        continua sendo a do repositório, não uma segunda cópia aqui.
        """
        row = await self.get_user(user_id, viewer=viewer)
        user = row.user
        if user.organization_id == organization_id:
            raise UserAlreadyInOrganizationError(
                f"Usuário {user.id} já está na organização {organization_id}."
            )
        # A validação do destino é a MESMA decisão da criação (§3.15): existe e
        # está ativa, ou 404/409. Uma terceira cópia aqui divergiria em silêncio.
        await resolve_organization_for_creation(
            viewer,
            organization_id,
            get_organization=self._repo.get_organization,
            subject="o usuário",
        )
        organization = await self._repo.get_organization(organization_id)
        if organization is None:  # o resolver acabou de validar; é só para o tipo
            raise OrganizationNotFoundError(f"Organização inexistente: {organization_id}")

        n_primary = await self._repo.count_primary_assignments_on_open_clients(user.id)
        if n_primary:
            raise UserIsPrimaryManagerError(
                f"Usuário {user.id} é responsável de {n_primary} cliente(s) aberto(s).",
                user_message=_primary_manager_message(n_primary),
            )

        from_organization_id = user.organization_id
        # O DELETE nunca leva linha de responsável (`is_primary = false` no
        # próprio SQL, como o remove da carteira). Se uma promoção entrou entre
        # a contagem acima e este ponto, a linha sobra — e a re-contagem abaixo
        # transforma isso em 409, desfazendo a transação inteira.
        n_assignments = await self._repo.delete_assignments_on_open_clients(user.id)
        if await self._repo.count_primary_assignments_on_open_clients(user.id):
            raise UserIsPrimaryManagerError(
                f"Usuário {user.id} virou responsável durante a transferência.",
                user_message=_primary_manager_message(1),
            )
        n_favorites = await self._repo.delete_favorites_outside_organization(
            user.id, organization_id
        )
        n_notifications = await self._repo.delete_notifications_outside_organization(
            user.id, organization_id
        )
        user.organization_id = organization_id
        await self._repo.add(user)

        if self._usage_events is not None and from_organization_id is not None:
            await self._usage_events.emit_usuario_transferido_de_organizacao(
                user_id=user.id,
                from_organization_id=from_organization_id,
                to_organization_id=organization_id,
                n_carteira_removida=n_assignments,
                n_favoritos_removidos=n_favorites,
                n_notificacoes_removidas=n_notifications,
            )
        return StaffRow(user=user, organization_name=organization.name)

    # ------------------------------ ACTIVATE / DEACTIVATE -------------

    async def set_user_active(
        self,
        user_id: UUID,
        *,
        active: bool,
        viewer: CurrentUser,
    ) -> StaffRow:
        """Soft activation/deactivation. Bloqueios:
        - Admin NÃO pode desativar a si mesmo (Doc §8.2 + §8.5).
        - Alvo fora do alcance (outra org, plataforma, usuário de cliente) → 404.
        """
        if not active and user_id == UUID(viewer.id):
            raise CannotDeactivateSelfError(
                f"User {user_id} tentou desativar a si mesmo.",
            )

        row = await self.get_user(user_id, viewer=viewer)
        row.user.active = active
        await self._repo.add(row.user)
        return row

    # ------------------------------------------------------------------
    # Usuários DO CLIENTE (tenant) — Sprint 5 / R5
    # ------------------------------------------------------------------
    #
    # Todo método recebe `client_id` (o tenant, já validado pela rota via
    # `require_client_access`) e resolve o alvo por `get_by_id_in_tenant` — o
    # `AND client_id` mora no SELECT. É a defesa anti-IDOR: `user_id` forjado de
    # outro tenant, ou de um admin do sistema (`client_id IS NULL`), não retorna
    # linha e vira 404 antes de qualquer escrita.

    async def list_client_users(
        self,
        *,
        client_id: UUID,
        page: int,
        page_size: int,
        search: str | None = None,
    ) -> tuple[list[User], PaginationMeta]:
        """Usuários do tenant, paginados. Nunca inclui usuário de outro tenant."""
        rows, total = await self._repo.list_paginated(
            page=page, page_size=page_size, search=search, client_id=client_id
        )
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return list(rows), PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )

    async def get_client_user(self, *, client_id: UUID, user_id: UUID) -> User:
        user = await self._repo.get_by_id_in_tenant(user_id, client_id=client_id)
        if user is None:
            raise NotFoundError("Usuário não encontrado.")
        return user

    async def create_client_user(
        self,
        *,
        client_id: UUID,
        name: str,
        email: str,
        password: str,
        role: ClientUserRole,
    ) -> User:
        """Cria usuário DO cliente com senha inicial definida por quem cria.

        `client_id` e `scope` são fixados pelo SERVIDOR a partir do tenant da
        rota — nunca vêm do body. `role` já chega restrito a
        `client_manager`/`client_operator` pelo schema (422 do Pydantic), então
        não há caminho para forjar `admin`/`manager`/`scope='system'`.

        E-mail é único GLOBALMENTE (constraint da tabela): colisão com usuário
        de outro tenant, ou da equipe Hologram, devolve 409 — e a mensagem ao
        usuário não diz de quem é o e-mail (não vira oráculo de enumeração
        cross-tenant).
        """
        normalized_email = email.lower()
        if await self._repo.get_by_email(normalized_email) is not None:
            raise EmailAlreadyExistsError(
                f"E-mail já existe: {normalized_email}",
                user_message="Este e-mail já está em uso.",
            )

        user = User(
            name=name,
            email=normalized_email,
            password_hash=hash_password(password),
            role=role.value,
            active=True,
            scope=UserScope.CLIENT.value,
            client_id=client_id,
        )
        await self._repo.add(user)
        return user

    async def update_client_user(
        self,
        *,
        client_id: UUID,
        user_id: UUID,
        name: str | None = None,
        email: str | None = None,
        role: ClientUserRole | None = None,
    ) -> User:
        """PATCH parcial de um usuário do tenant. Papel segue restrito ao enum."""
        user = await self.get_client_user(client_id=client_id, user_id=user_id)

        if email is not None:
            normalized_email = email.lower()
            if normalized_email != user.email:
                if await self._repo.get_by_email(normalized_email) is not None:
                    raise EmailAlreadyExistsError(
                        f"E-mail já existe: {normalized_email}",
                        user_message="Este e-mail já está em uso.",
                    )
                user.email = normalized_email

        if name is not None:
            user.name = name
        if role is not None:
            user.role = role.value

        await self._repo.add(user)
        return user

    async def set_client_user_active(
        self,
        *,
        client_id: UUID,
        user_id: UUID,
        active: bool,
        current_user_id: UUID,
    ) -> User:
        """Ativa/desativa. Desativar revoga no PRÓXIMO request (checagem de `active`).

        Ninguém se desativa: o gerente do cliente ficaria de fora do próprio
        tenant sem ter quem o reative (não há autoatendimento no MVP).
        """
        if not active and user_id == current_user_id:
            raise CannotDeactivateSelfError(
                f"User {user_id} tentou desativar a si mesmo.",
            )
        user = await self.get_client_user(client_id=client_id, user_id=user_id)
        user.active = active
        await self._repo.add(user)
        return user
