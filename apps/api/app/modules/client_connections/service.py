"""Serviço ÚNICO de conexão de origem (Sprint 9, BACK 09.3 — R5).

Regra de negócio pura: sem HTTP, sem `HTTPException`. Quem decide tenant e
permissão é a rota (`AccessibleClientDep`/`OpenClientDep` + a matriz); aqui mora
o que é caro de duplicar.

**A ordem não é estética — é a regra.** Criar ou trocar credencial:

    1. resolve o tipo no registry (desconhecido → 422, antes de qualquer coisa);
    2. **verifica contra o provedor ANTES de persistir** — credencial recusada
       não vira linha, nem linha com `status='erro'`: o cliente ficaria com uma
       origem que nunca funcionou ocupando `(tipo, rótulo)`;
    3. só então provisiona a DEK e cifra.

O passo 3 vem depois do guard de cliente ABERTO da rota (`OpenClientDep`), e
isso importa: `provision_client_cipher` faz read-modify-write de `dek_wrapped`,
e num cliente encerrado ele ressuscitaria a capacidade de cifrar num tenant cujo
conteúdo já morreu por crypto-shredding (§4.12).

**A credencial vira UM JSON cifrado**, com a DEK do cliente e o AAD da linha
(`AAD_CONNECTION_CREDENTIALS` + pk). A pk entra no AAD, então a linha precisa
existir ANTES da cifra — daí o `flush` no meio da criação.

**A sessão vem do chamador** de propósito: a BACK 09.4 cria cliente e conexão na
MESMA transação, e um serviço que abrisse sessão própria tornaria isso
impossível.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from app.core.audit import AccessAction, record_access
from app.core.crypto_service import (
    AAD_CONNECTION_CREDENTIALS,
    field_locator,
    load_client_cipher,
    provision_client_cipher,
)
from app.core.exceptions import (
    ConnectionLabelAlreadyExistsError,
    NotFoundError,
    ProviderAuthError,
    ValidationAppError,
)
from app.db.models.client_connection import ClientConnection, ConnectionStatus
from app.integrations.providers.registry import capabilities_for, get_provider
from app.modules.client_connections.legacy_fallback import resolve_origin_connections
from app.modules.client_connections.repository import ClientConnectionRepository
from app.modules.client_connections.schemas import ClientConnectionResponse

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.core.config import Settings
    from app.db.models import Client
    from app.integrations.providers.base import ProviderCredentials

#: Rótulo sugerido quando o cliente ganha a PRIMEIRA conexão de um tipo.
_DEFAULT_LABELS: dict[str, str] = {"omie": "Omie"}


def default_label_for(provider_type: str) -> str:
    """Rótulo padrão do tipo. Tipo sem entrada usa o próprio nome capitalizado."""
    return _DEFAULT_LABELS.get(provider_type, provider_type.capitalize())


def _is_auth_error(exc: BaseException) -> bool:
    """`ProviderAuthError`, inclusive embrulhado num `ExceptionGroup` (anyio)."""
    if isinstance(exc, ProviderAuthError):
        return True
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_auth_error(sub) for sub in exc.exceptions)
    return False


class ClientConnectionService:
    """Orquestra provedor + cripto + persistência + trilha."""

    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._repo = ClientConnectionRepository(db)

    # ------------------------------------------------------------------ leitura

    async def list_connections(self, client: Client) -> list[ClientConnectionResponse]:
        """As origens do cliente. Sem credencial, nem mascarada."""
        return [
            ClientConnectionResponse.from_connection(row) for row in await self.list_rows(client.id)
        ]

    async def list_rows(self, client_id: UUID) -> list[ClientConnection]:
        """As linhas ORM GRAVADAS, em ordem determinística.

        Existe para quem precisa DECIDIR sobre elas (`select_capable_connection`)
        e não só exibi-las — o DTO de resposta não carrega o que a decisão usa.

        ⚠️ Não inclui a conexão SINTETIZADA da janela de conversão. Quem vai
        OPERAR a origem usa `resolve_origins`; esta aqui é para o CRUD, onde
        mostrar (ou tentar remover) uma conexão que não existe no banco seria
        pior que não mostrá-la.
        """
        return await self._repo.list_for_client(client_id)

    async def resolve_origins(self, client: Client) -> list[ClientConnection]:
        """As origens EFETIVAS do cliente — com o fallback da 09.5 aplicado.

        É esta que os consumidores de origem usam: cliente ainda não convertido
        recebe a conexão sintetizada a partir das colunas antigas, em vez de um
        409 `SEM_CONEXAO` no minuto do deploy.
        """
        return await resolve_origin_connections(self._db, client, settings=self._settings)

    async def _get_or_404(self, client: Client, connection_id: UUID) -> ClientConnection:
        connection = await self._repo.get_in_client(connection_id, client_id=client.id)
        if connection is None:
            # Conexão de OUTRO cliente cai aqui pelo mesmo caminho da
            # inexistente: 404 uniforme, sem oráculo (§3.15).
            raise NotFoundError("Conexão não encontrada.")
        return connection

    # ------------------------------------------------------------------ escrita

    async def create_connection(
        self,
        *,
        client: Client,
        user: CurrentUser,
        provider_type: str,
        label: str | None,
        credentials: ProviderCredentials,
    ) -> ClientConnectionResponse:
        """Conecta uma origem ao cliente. Valida no provedor ANTES de persistir."""
        capabilities_for(provider_type)  # tipo desconhecido → 422, antes de tudo
        resolved_label = await self._resolve_label(
            client=client, provider_type=provider_type, label=label
        )
        await self._assert_label_free(
            client=client, provider_type=provider_type, label=resolved_label
        )
        await self._verify_against_provider(provider_type, credentials)

        connection = ClientConnection(
            client_id=client.id,
            provider_type=provider_type,
            label=resolved_label,
            status=ConnectionStatus.ATIVA.value,
            last_checked_at=datetime.now(UTC),
        )
        try:
            # A pk entra no AAD → a linha precisa existir antes da cifra.
            await self._repo.add(connection)
        except IntegrityError as exc:
            # A UNIQUE do banco é quem decide: entre o `_assert_label_free` e
            # este INSERT cabe outra request.
            raise await self._label_conflict(
                client=client, provider_type=provider_type, label=resolved_label
            ) from exc

        await self._encrypt_into(client, connection, credentials)
        await self._db.flush()
        await self._audit(user, client, AccessAction.CONN_CREATE)
        return ClientConnectionResponse.from_connection(connection)

    async def test_connection(
        self, *, client: Client, user: CurrentUser, connection_id: UUID
    ) -> ClientConnectionResponse:
        """Reverifica a credencial GRAVADA contra o provedor.

        Sucesso → `ativa` + carimbo. Credencial recusada → `erro` + carimbo, com
        a credencial INTACTA (ADR-037-BE): recusada não é perdida. Timeout e
        instabilidade do provedor propagam como 5xx e **não** mudam estado — são
        transitórios, e marcar `erro` por eles faria o usuário reconectar uma
        conexão que está boa.
        """
        connection = await self._get_or_404(client, connection_id)
        credentials = await self._decrypt_credentials(client, connection)
        try:
            await self._verify_against_provider(connection.provider_type, credentials)
        except Exception as exc:
            # `except Exception` e não `except ProviderAuthError`: sob anyio o
            # erro pode chegar dentro de um `ExceptionGroup` — `_is_auth_error`
            # desembrulha. O que não é auth propaga sem tocar no estado.
            if _is_auth_error(exc):
                await self._repo.mark_connection_error(connection.id)
                await self._db.flush()
                await self._audit(user, client, AccessAction.CONN_TEST)
            raise
        await self._repo.mark_connection_checked(connection.id)
        await self._db.refresh(connection)
        await self._audit(user, client, AccessAction.CONN_TEST)
        return ClientConnectionResponse.from_connection(connection)

    async def update_connection(
        self,
        *,
        client: Client,
        user: CurrentUser,
        connection_id: UUID,
        label: str | None,
        credentials: ProviderCredentials | None,
    ) -> ClientConnectionResponse:
        """Renomeia e/ou troca a credencial. Credencial nova é verificada antes."""
        if label is None and credentials is None:
            raise ValidationAppError(
                "PATCH de conexão sem rótulo nem credenciais.",
                user_message="Informe um novo rótulo ou novas credenciais.",
            )
        connection = await self._get_or_404(client, connection_id)

        if label is not None and label != connection.label:
            await self._assert_label_free(
                client=client, provider_type=connection.provider_type, label=label
            )
            connection.label = label

        if credentials is not None:
            await self._verify_against_provider(connection.provider_type, credentials)
            await self._encrypt_into(client, connection, credentials)
            connection.status = ConnectionStatus.ATIVA.value
            connection.last_checked_at = datetime.now(UTC)

        try:
            await self._db.flush()
        except IntegrityError as exc:
            raise await self._label_conflict(
                client=client,
                provider_type=connection.provider_type,
                label=connection.label,
            ) from exc
        await self._audit(user, client, AccessAction.CONN_UPDATE)
        return ClientConnectionResponse.from_connection(connection)

    async def delete_connection(
        self, *, client: Client, user: CurrentUser, connection_id: UUID
    ) -> None:
        """Remoção DEFINITIVA da linha — reconectar o mesmo (tipo, rótulo) volta a valer."""
        await self._get_or_404(client, connection_id)
        await self._repo.delete_in_client(connection_id, client_id=client.id)
        await self._db.flush()
        await self._audit(user, client, AccessAction.CONN_DELETE)

    # ------------------------------------------------------------------ apoio

    async def _resolve_label(self, *, client: Client, provider_type: str, label: str | None) -> str:
        """Rótulo do payload, ou o padrão do tipo na PRIMEIRA conexão daquele tipo.

        A partir da segunda o rótulo é obrigatório: sem ele, o padrão colidiria
        com a que já existe e o usuário receberia um 409 sem entender por quê.
        """
        if label is not None:
            return label
        if await self._repo.count_of_type(client_id=client.id, provider_type=provider_type) > 0:
            raise ValidationAppError(
                f"client {client.id} already has a {provider_type} connection; label required",
                user_message=(
                    "Este cliente já tem uma origem deste tipo. Dê um rótulo para "
                    "diferenciar a nova."
                ),
            )
        return default_label_for(provider_type)

    async def _assert_label_free(self, *, client: Client, provider_type: str, label: str) -> None:
        existing = await self._repo.find_by_label(
            client_id=client.id, provider_type=provider_type, label=label
        )
        if existing is not None:
            raise ConnectionLabelAlreadyExistsError(
                f"connection ({provider_type}, {label!r}) already exists for client {client.id}",
                details={"existingConnectionId": str(existing.id)},
            )

    async def _label_conflict(
        self, *, client: Client, provider_type: str, label: str
    ) -> ConnectionLabelAlreadyExistsError:
        """Monta o 409 relendo quem ficou com o par — o banco já decidiu."""
        existing = await self._repo.find_by_label(
            client_id=client.id, provider_type=provider_type, label=label
        )
        details = {"existingConnectionId": str(existing.id)} if existing else {}
        return ConnectionLabelAlreadyExistsError(
            f"connection ({provider_type}, {label!r}) already exists for client {client.id}",
            details=details,
        )

    async def _verify_against_provider(
        self, provider_type: str, credentials: ProviderCredentials
    ) -> None:
        """Bate no provedor. Erro do provedor sobe — NADA foi persistido ainda."""
        provider = get_provider(provider_type, credentials, self._settings)
        try:
            await provider.verify_credentials()
        finally:
            await provider.aclose()

    async def _encrypt_into(
        self,
        client: Client,
        connection: ClientConnection,
        credentials: ProviderCredentials,
    ) -> None:
        """Cifra o JSON de credenciais na linha, com a DEK do cliente e o AAD dela.

        `provision_client_cipher` gera a DEK se o cliente ainda não tiver — é o
        caso do cliente criado sem origem (09.4). Chega aqui só depois do guard
        de cliente ABERTO da rota, nunca antes (§4.12).

        `sort_keys=True`: o JSON é o plaintext, e uma ordem instável mudaria o
        texto cifrado sem que nada tivesse mudado de verdade.
        """
        cipher = await provision_client_cipher(client, settings=self._settings)
        plaintext = json.dumps(
            {key: value.get_secret_value() for key, value in credentials.items()},
            ensure_ascii=False,
            sort_keys=True,
        )
        envelope, iv = cipher.encrypt(
            plaintext, field_locator(AAD_CONNECTION_CREDENTIALS, connection.id)
        )
        connection.credentials_encrypted = envelope
        connection.credentials_iv = iv

    async def _decrypt_credentials(
        self, client: Client, connection: ClientConnection
    ) -> ProviderCredentials:
        """Credencial gravada → mapa de `SecretStr`. Nunca vira `str` solto."""
        if connection.credentials_encrypted is None or connection.credentials_iv is None:
            raise ValidationAppError(
                f"connection {connection.id} has no stored credentials",
                user_message="Esta origem não tem credenciais gravadas. Atualize a conexão.",
            )
        cipher = await load_client_cipher(client, settings=self._settings)
        plaintext = cipher.decrypt(
            connection.credentials_encrypted,
            connection.credentials_iv,
            field_locator(AAD_CONNECTION_CREDENTIALS, connection.id),
        )
        raw: dict[str, str] = json.loads(plaintext)
        return {key: SecretStr(value) for key, value in raw.items()}

    async def _audit(self, user: CurrentUser, client: Client, action: AccessAction) -> None:
        """1 linha por ação, SÓ IDs (§4.7). Nenhum rótulo, nenhum nome, nenhuma chave."""
        await record_access(
            self._db,
            user_id=UUID(user.id),
            client_id=client.id,
            action=action,
            user_scope=user.scope,
            actor_client_id=user.client_id,
            actor_organization_id=user.organization_id,
        )
