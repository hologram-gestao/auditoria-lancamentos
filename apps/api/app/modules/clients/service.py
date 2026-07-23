"""Lógica de negócio do CRUD de clientes BPO (S6 — BACK 3.1 a 3.5).

Responsabilidades:
    - Criptografia das credenciais Omie via AES-256-GCM (IV novo por operação).
    - Auto-assignment do criador na criação (Doc §9.2).
    - "Test connection" sem persistir nada (Doc §9.2 estados do botão).
    - Validações específicas: ambos os campos de credencial juntos no PATCH,
      manager-alvo do /assign deve ser ativo e role=manager.

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

from app.core.crypto_service import (
    AAD_CLIENT_APP_KEY,
    AAD_CLIENT_APP_SECRET,
    field_locator,
    new_client_dek,
    provision_client_cipher,
)
from app.core.exceptions import (
    IncompleteCredentialsError,
    InvalidManagerError,
    NotFoundError,
    OmieAuthError,
    OmieFaultError,
    OmieServerError,
    OmieTimeoutError,
)
from app.db.models import Client, ClientAssignment, OmieAccountCache, UserRole
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.modules.clients.accounts_cache import OmieAccountsCacheService
from app.modules.clients.repository import ClientRepository, ClientRow
from app.modules.clients.schemas import (
    BankAccountResponse,
    ClientDetailResponse,
    ClientResponse,
    ManagerSummary,
    ReconciliationSessionSummary,
    TestConnectionResponse,
)
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
        created_at=row.client.created_at,
        updated_at=row.client.updated_at,
        responsible_manager=manager,
        reconciliation_count=row.reconciliation_count,
    )


class ClientService:
    """CRUD + regras de negócio para `clients`."""

    def __init__(
        self,
        repository: ClientRepository,
        settings: Settings,
        *,
        accounts_cache: OmieAccountsCacheService | None = None,
    ) -> None:
        self._repo = repository
        self._settings = settings
        # Cache L1 — instanciado on-demand quando não passado pelo caller.
        # Em testes é injetado com `OmieClient` mockado via respx.
        self._accounts_cache = accounts_cache or OmieAccountsCacheService(repository, settings)

    # ------------------------------ READ ------------------------------

    async def list_clients(
        self,
        *,
        page: int,
        page_size: int,
        search: str | None,
        manager_id_filter: UUID | None,
    ) -> tuple[list[ClientResponse], PaginationMeta]:
        """Lista clientes com filtro RBAC.

        `manager_id_filter` é controlado pela route conforme o role do caller:
        admin → `None` (vê tudo); manager → `UUID(current_user.id)`.
        """
        rows, total = await self._repo.list_paginated(
            page=page, page_size=page_size, search=search, manager_id=manager_id_filter
        )
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        responses = [_row_to_response(r) for r in rows]
        pagination = PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )
        return responses, pagination

    async def get_client_detail(self, client_id: UUID) -> ClientResponse:
        """Retorna ClientResponse já preenchido com manager + count.

        404 se cliente não existe — caller tipicamente já passou por
        `require_client_access` (que retorna 403/404 antes), mas mantemos o
        guard aqui para reuso fora desse contexto.
        """
        row = await self._repo.get_detail(client_id)
        if row is None:
            raise NotFoundError("Cliente não encontrado.")
        return _row_to_response(row)

    # ------------------------------ CREATE ----------------------------

    async def create_client(
        self,
        *,
        name: str,
        omie_app_key: str,
        omie_app_secret: str,
        current_user_id: UUID,
    ) -> ClientResponse:
        """Cria cliente com credenciais criptografadas + auto-assign do criador.

        Sprint 3: cada cliente nasce com uma DEK própria (gerada e embrulhada
        pela KEK do KMS). As credenciais são cifradas no envelope versionado
        `v<n>:<key_id>:` + AAD (client_id‖tabela‖coluna‖pk). O `client.id` é
        gerado ANTES para compor o AAD (o default `uuid4` só valeria no flush).
        Cada credencial usa IV próprio; o texto plano só vive em memória local.
        """
        client_id = uuid4()
        cipher, dek_wrapped = await new_client_dek(client_id, settings=self._settings)
        ct_key, iv_key = cipher.encrypt(omie_app_key, field_locator(AAD_CLIENT_APP_KEY, client_id))
        ct_secret, iv_secret = cipher.encrypt(
            omie_app_secret, field_locator(AAD_CLIENT_APP_SECRET, client_id)
        )

        client = Client(
            id=client_id,
            name=name,
            dek_wrapped=dek_wrapped,
            omie_app_key_encrypted=ct_key,
            omie_app_key_iv=iv_key,
            omie_app_secret_encrypted=ct_secret,
            omie_app_secret_iv=iv_secret,
            active=True,
            created_by=current_user_id,
        )
        await self._repo.add_client(client)

        assignment = ClientAssignment(
            client_id=client.id,
            user_id=current_user_id,
            assigned_by=current_user_id,
        )
        await self._repo.add_assignment(assignment)

        return await self.get_client_detail(client.id)

    # ------------------------------ UPDATE ----------------------------

    async def update_client(
        self,
        client: Client,
        *,
        name: str | None,
        active: bool | None,
        omie_app_key: str | None,
        omie_app_secret: str | None,
    ) -> ClientResponse:
        """Atualiza campos parciais do cliente (PATCH).

        Para credenciais: precisa enviar AMBOS os campos juntos. Caso só um
        venha preenchido, retorna 400 `IncompleteCredentialsError`. Quando
        ambos vêm, recriptografa com IVs novos.
        """
        if name is not None:
            client.name = name
        if active is not None:
            client.active = active

        # Pares possíveis: ambos None (ignora), ambos preenchidos (recriptografa),
        # apenas um → 400 (evita silenciosamente manter credenciais inconsistentes).
        if omie_app_key is not None and omie_app_secret is not None:
            # Provisiona a DEK se o cliente for legado (dek_wrapped None) e
            # recifra no envelope corrente com AAD amarrado à linha.
            cipher = await provision_client_cipher(client, settings=self._settings)
            ct_key, iv_key = cipher.encrypt(
                omie_app_key, field_locator(AAD_CLIENT_APP_KEY, client.id)
            )
            ct_secret, iv_secret = cipher.encrypt(
                omie_app_secret, field_locator(AAD_CLIENT_APP_SECRET, client.id)
            )
            client.omie_app_key_encrypted = ct_key
            client.omie_app_key_iv = iv_key
            client.omie_app_secret_encrypted = ct_secret
            client.omie_app_secret_iv = iv_secret
        elif omie_app_key is not None or omie_app_secret is not None:
            raise IncompleteCredentialsError(
                "PATCH /clients/{id}: app_key e app_secret precisam vir juntos.",
            )

        await self._repo.add_client(client)
        return await self.get_client_detail(client.id)

    # ------------------------------ ASSIGN ----------------------------

    async def assign_client(
        self,
        client_id: UUID,
        *,
        new_user_id: UUID,
        current_admin_id: UUID,
    ) -> ClientResponse:
        """Reatribui o cliente para outro gerente. Admin-only.

        Validações:
            - Cliente existe (404 se não).
            - Novo user existe + role=manager + active=true (400 caso não).
        """
        client = await self._repo.get_by_id(client_id)
        if client is None:
            raise NotFoundError("Cliente não encontrado.")

        new_manager = await self._repo.get_user_by_id(new_user_id)
        if (
            new_manager is None
            or not new_manager.active
            or new_manager.role != UserRole.MANAGER.value
        ):
            raise InvalidManagerError(
                f"User {new_user_id} não é manager ativo (assign rejeitado).",
            )

        assignment = await self._repo.get_assignment(client_id)
        if assignment is None:
            # Cliente órfão (não deveria ocorrer — auto-assign na criação) →
            # cria o vínculo. Mantém a operação idempotente do ponto de vista
            # do admin: o efeito final é "este cliente pertence ao novo manager".
            assignment = ClientAssignment(
                client_id=client_id,
                user_id=new_user_id,
                assigned_by=current_admin_id,
            )
            await self._repo.add_assignment(assignment)
        else:
            assignment.user_id = new_user_id
            assignment.assigned_by = current_admin_id
            assignment.assigned_at = datetime.now(UTC)
            await self._repo.add_assignment(assignment)

        return await self.get_client_detail(client_id)

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

    async def get_client_detail_with_accounts(self, client: Client) -> ClientDetailResponse:
        """Detalhe completo: Client + manager + count + contas do cache (Endpoint A).

        TTL de 24 h decidido dentro do `OmieAccountsCacheService`. Se o cache
        miss falhar (Omie indisponível), `AccountsSyncError` propaga e o
        handler global retorna 502 — alinhado ao padrão do test-connection.
        """
        rows, synced_at = await self._accounts_cache.get_or_sync(client)
        return await self._build_detail_response(client.id, rows, synced_at)

    async def force_sync_accounts(self, client: Client) -> ClientDetailResponse:
        """Endpoint B: força sync ignorando TTL e retorna o detalhe completo."""
        rows, synced_at = await self._accounts_cache.force_sync(client)
        return await self._build_detail_response(client.id, rows, synced_at)

    async def list_reconciliations(
        self,
        client_id: UUID,
        *,
        page: int,
        page_size: int,
        omie_conta_id: int | None,
        month: str | None,
    ) -> tuple[list[ReconciliationSessionSummary], PaginationMeta]:
        """Endpoint C: histórico paginado das sessões de conciliação (S7 BACK 4.2).

        `month` chega como `'YYYY-MM'`. Convertemos pra range half-open
        `[YYYY-MM-01, próximo-mês-01)` no service — repository fica agnóstico.
        Mês inválido (formato errado, valores fora de range) → 400.
        """
        month_start, month_end = _parse_month_range(month)
        rows, total = await self._repo.list_reconciliations_paginated(
            client_id,
            page=page,
            page_size=page_size,
            omie_conta_id=omie_conta_id,
            month_start=month_start,
            month_end=month_end,
        )
        responses = [_session_to_summary(r) for r in rows]
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
    ) -> ClientDetailResponse:
        """Compõe `ClientDetailResponse` a partir de Client + manager + cache."""
        base = await self.get_client_detail(client_id)
        accounts = [BankAccountResponse.model_validate(r) for r in rows]
        return ClientDetailResponse(
            **base.model_dump(),
            accounts=accounts,
            accounts_synced_at=synced_at,
        )


# ----------------------------------------------------------------------
# Helpers de módulo (puros — não dependem do service)
# ----------------------------------------------------------------------


def _session_to_summary(session: ReconciliationSession) -> ReconciliationSessionSummary:
    """Mapeia ORM `ReconciliationSession` → DTO `ReconciliationSessionSummary`."""
    return ReconciliationSessionSummary.model_validate(session, from_attributes=True)


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
