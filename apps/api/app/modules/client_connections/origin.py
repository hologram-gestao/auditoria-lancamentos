"""A porta ÚNICA por onde um consumidor pede a origem de um cliente (S9, BACK 09.6 — R6).

Antes da Sprint 9, "falar com a origem" era duas linhas repetidas em 8 arquivos:

    cipher = await load_client_cipher(client, settings=settings)
    omie   = build_omie_client(client, settings, cipher)

Isso embute três suposições que deixaram de valer: que todo cliente tem origem,
que a origem é o Omie, e que a credencial mora nas colunas de `clients`. Com o
cliente sem origem (09.4), a primeira linha estoura `CryptoError` e vira **500**
— quando a resposta certa é um **409 acionável**.

Este módulo junta as peças que já existem, e não acrescenta regra nenhuma:

    `resolve_origin_connections` (09.5)  → quais origens o cliente tem
        (inclui a sintetizada da janela de conversão)
    `select_capable_connection`  (09.2)  → qual delas serve, ou qual dos TRÊS
        409 explica por que nenhuma serve
    `get_provider` / adaptador   (09.2)  → o client do provedor

**Nenhum consumidor decide nada disso na mão.** Comparar `status == 'ativa'`,
ler as colunas antigas ou construir o `OmieClient` direto passou a ser proibido
— o gate `tests/unit/test_legacy_credential_columns_gate.py` cobre o segundo
caso e `tests/unit/test_origin_consumers_inventory.py` cobre o terceiro.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import SecretStr

from app.core.crypto_service import (
    AAD_CONNECTION_CREDENTIALS,
    field_locator,
    load_client_cipher,
)
from app.core.exceptions import ValidationAppError
from app.integrations.providers.registry import get_provider
from app.modules.client_connections.capability import select_capable_connection
from app.modules.client_connections.legacy_fallback import (
    is_synthetic,
    legacy_credentials,
    resolve_origin_connections,
)

if TYPE_CHECKING:
    import httpx
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.db.models import Client
    from app.db.models.client_connection import ClientConnection
    from app.integrations.omie.client import OmieClient
    from app.integrations.providers.base import Capability, OriginProvider, ProviderCredentials


async def resolve_capable_connection(
    db: AsyncSession, client: Client, capability: Capability, *, settings: Settings
) -> ClientConnection:
    """A conexão do cliente que atende `capability`, ou o 409 que diz por quê.

    Os três desfechos negativos (`sem_conexao`, `origem_com_erro`,
    `capacidade_ausente`) saem do predicado único da 09.2 — este módulo não
    escolhe qual.
    """
    connections = await resolve_origin_connections(db, client, settings=settings)
    return select_capable_connection(connections, capability)


async def credentials_for(
    client: Client, connection: ClientConnection, *, settings: Settings
) -> ProviderCredentials:
    """A credencial DAQUELA conexão, decifrada.

    Dois caminhos, e a diferença é só onde o segredo está:

    - conexão **sintetizada** (janela de conversão, 09.5) → o segredo ainda
      está nas colunas antigas, e quem sabe lê-las é o `legacy_fallback`;
    - conexão **real** → JSON cifrado na própria linha, com o AAD dela.
    """
    if is_synthetic(connection):
        return await legacy_credentials(client, settings=settings)
    if connection.credentials_encrypted is None or connection.credentials_iv is None:
        raise ValidationAppError(
            f"connection {connection.id} has no stored credentials",
            user_message="Esta origem não tem credenciais gravadas. Atualize a conexão.",
        )
    cipher = await load_client_cipher(client, settings=settings)
    plaintext = cipher.decrypt(
        connection.credentials_encrypted,
        connection.credentials_iv,
        field_locator(AAD_CONNECTION_CREDENTIALS, connection.id),
    )
    raw: dict[str, str] = json.loads(plaintext)
    return {key: SecretStr(value) for key, value in raw.items()}


async def build_origin_provider(
    client: Client,
    connection: ClientConnection,
    *,
    settings: Settings,
    http_client: httpx.AsyncClient | None = None,
) -> OriginProvider:
    """Adaptador do provedor da conexão, com a credencial dela já decifrada."""
    credentials = await credentials_for(client, connection, settings=settings)
    return get_provider(connection.provider_type, credentials, settings, http_client=http_client)


async def build_origin_client(
    client: Client,
    connection: ClientConnection,
    *,
    settings: Settings,
    http_client: httpx.AsyncClient | None = None,
) -> OmieClient:
    """O `OmieClient` cru daquela conexão.

    Existe porque o fluxo do Omie (extrato, títulos, categorias, `IncluirLancCC`)
    foi verificado contra fixture REAL e não vai ser reescrito atrás dos DTOs
    neutros sem necessidade. O adaptador continua sendo quem constrói — o que
    muda é que a credencial vem da CONEXÃO, não das colunas de `clients`.
    """
    provider = await build_origin_provider(
        client, connection, settings=settings, http_client=http_client
    )
    raw: OmieClient | None = getattr(provider, "raw_client", None)
    if raw is None:  # pragma: no cover - só um provedor hoje
        raise ValidationAppError(
            f"provider {connection.provider_type!r} has no Omie-compatible client",
            user_message="Esta origem não oferece esta operação.",
        )
    return raw


def client_from_credentials(
    provider_type: str,
    credentials: ProviderCredentials,
    *,
    settings: Settings,
    http_client: httpx.AsyncClient | None = None,
) -> OmieClient:
    """O client do provedor a partir de credenciais JÁ decifradas.

    Para quem não pode segurar a conexão viva: o job de conciliação roda fora do
    request, a `AsyncSession` que carregou a linha já fechou e os atributos
    dela estão expirados. Decifrar dentro da sessão e carregar só o
    `ProviderCredentials` (que é `SecretStr`, não texto) evita tanto o
    `DetachedInstanceError` quanto uma segunda ida ao KMS.
    """
    provider = get_provider(provider_type, credentials, settings, http_client=http_client)
    raw: OmieClient | None = getattr(provider, "raw_client", None)
    if raw is None:  # pragma: no cover - só um provedor hoje
        raise ValidationAppError(
            f"provider {provider_type!r} has no Omie-compatible client",
            user_message="Esta origem não oferece esta operação.",
        )
    return raw


async def build_capable_client(
    db: AsyncSession,
    client: Client,
    capability: Capability,
    *,
    settings: Settings,
    http_client: httpx.AsyncClient | None = None,
) -> OmieClient:
    """Atalho: resolve a conexão capaz E constrói o client. O caminho comum.

    Quem precisa da CONEXÃO em si (para carimbar `accounts_synced_at`, marcar
    erro ou gravar `connection_id`) chama as duas partes separadamente.
    """
    connection = await resolve_capable_connection(db, client, capability, settings=settings)
    return await build_origin_client(client, connection, settings=settings, http_client=http_client)


__all__ = [
    "build_capable_client",
    "build_origin_client",
    "build_origin_provider",
    "client_from_credentials",
    "credentials_for",
    "resolve_capable_connection",
]
