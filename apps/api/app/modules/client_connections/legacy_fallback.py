"""Ponte entre as colunas antigas de credencial e as conexões (Sprint 9, BACK 09.5 — R2).

**Este é o ÚNICO módulo do app autorizado a LER
`clients.omie_app_*_encrypted/_iv`.** O gate
`tests/unit/test_legacy_credential_columns_gate.py` reprova qualquer arquivo
novo que as toque — a lista de exceções é fechada e justificada arquivo a
arquivo.

**O problema que ele resolve.** Entre o deploy da Sprint 9 e a conversão
verificada (`scripts/convert_credentials_to_connections.py`), a base tem os dois
mundos ao mesmo tempo: cliente antigo com a credencial nas colunas e nenhuma
conexão, e cliente novo (09.4) com conexão e colunas nulas. Sem ponte, o
primeiro grupo receberia 409 `SEM_CONEXAO` — todo mundo que já usava o sistema
pararia no minuto do deploy.

**Precedência é lei, e é nesta ordem:**

    1. o cliente TEM conexão → usa as conexões, ponto. Nem olha as colunas.
    2. o cliente NÃO tem conexão **e** o fallback está efetivamente ligado
       **e** as colunas antigas estão preenchidas → sintetiza, EM MEMÓRIA, uma
       conexão `omie` ativa a partir delas.
    3. caso contrário → lista vazia, e quem chamou decide o 409 pela taxonomia
       da 09.2.

O passo 1 antes do 2 não é detalhe: um cliente criado com credencial durante a
janela tem conexão e colunas NULAS (09.4). Se a ordem fosse inversa, ele cairia
no fallback, não encontraria nada nas colunas e pararia de operar.

**A conexão sintetizada NUNCA é gravada.** Ela existe só para o adaptador
receber algo com a forma certa. Persistir aqui seria uma segunda conversão —
fora do script, sem lote, sem relatório e sem verificação.

**Desligar a flag não é promoção.** `effective_fallback_enabled` compara a
intenção (`LEGACY_CREDENTIALS_FALLBACK_ENABLED`) com a REALIDADE (a mesma
contagem do `--verify`): flag `False` com conversão incompleta mantém o
fallback ligado em memória e dispara `AlertCode.LEGACY_FALLBACK`. Estado
efetivo sai no log de startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid5

from sqlalchemy import ColumnElement, Select, and_, func, select

from app.core.crypto_service import (
    AAD_CLIENT_APP_KEY,
    AAD_CLIENT_APP_SECRET,
    field_locator,
    load_client_cipher,
)
from app.core.logging import get_logger
from app.db.models.client import Client
from app.db.models.client_connection import ClientConnection, ConnectionStatus, ProviderType
from app.integrations.providers.base import ProviderCredentials
from app.modules.client_connections.repository import ClientConnectionRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.core.crypto import ClientCipher

log = get_logger(__name__)

#: Namespace do UUID determinístico da conexão SINTETIZADA. Determinístico de
#: propósito: a mesma linha legada produz sempre o mesmo id, então log e
#: telemetria não veem uma "conexão nova" a cada request. Nunca colide com um
#: UUID v4 real (versão 5 ≠ versão 4), e nunca é gravado.
_SYNTHETIC_NAMESPACE = UUID("6f1b2f2a-9a4e-5c1e-8f3d-2b7c4a5d6e70")

#: Rótulo da conexão sintetizada. O mesmo padrão que o script de conversão usa,
#: para que a linha real que vier depois pareça a mesma coisa para o usuário.
SYNTHETIC_LABEL = "Omie"


#: As colunas antigas de credencial, em UM lugar só. O predicado Python
#: (`_has_legacy_credentials`) e os predicados SQL (`_filled`) leem DAQUI — sem
#: isso, "tem credencial legada" seria escrito três vezes e divergiria na
#: primeira mudança. Os nomes só podem aparecer neste arquivo: o gate
#: `tests/unit/test_legacy_credential_columns_gate.py` reprova o resto do app.
_LEGACY_CIPHERTEXT_COLUMNS = (
    Client.omie_app_key_encrypted,
    Client.omie_app_secret_encrypted,
)
_LEGACY_IV_COLUMNS = (Client.omie_app_key_iv, Client.omie_app_secret_iv)
_LEGACY_CREDENTIAL_COLUMNS = _LEGACY_CIPHERTEXT_COLUMNS + _LEGACY_IV_COLUMNS


def _has_legacy_credentials(client: Client) -> bool:
    """As 4 colunas antigas estão preenchidas de verdade?

    `''` (cliente ENCERRADO, crypto-shredding §4.12) conta como AUSENTE: a DEK
    dele foi destruída e não há o que decifrar. `None` é o cliente da Sprint 9,
    que nunca teve credencial ali.
    """
    return all(getattr(client, column.key) for column in _LEGACY_CREDENTIAL_COLUMNS)


def _filled(columns: tuple[Any, ...]) -> ColumnElement[bool]:
    """Versão SQL de "preenchida de verdade": nem `NULL`, nem `''`."""
    return and_(*[and_(column.is_not(None), column != "") for column in columns])


def clients_pending_conversion() -> Select[tuple[int]]:
    """`count(*)` dos clientes ABERTOS com credencial antiga e SEM conexão omie.

    A MESMA consulta do `--verify` e do `lifespan` — uma só, para que "a
    conversão terminou" signifique a mesma coisa nos dois lugares.

    Olha só o CIPHERTEXT, e não o par completo com os IVs (ao contrário de
    `legacy_origin_available`), de propósito: linha com ciphertext e IV nulo é
    corrupção, e ela tem de continuar contando como PENDENTE — assim o
    `--verify` sai FAIL barulhento em vez de promover o fallback por cima de um
    cliente que ninguém consegue decifrar.
    """
    ja_convertido = (
        select(ClientConnection.id)
        .where(
            ClientConnection.client_id == Client.id,
            ClientConnection.provider_type == ProviderType.OMIE.value,
        )
        .correlate(Client)
        .exists()
    )
    return select(func.count(Client.id)).where(
        Client.closed_at.is_(None),
        _filled(_LEGACY_CIPHERTEXT_COLUMNS),
        ~ja_convertido,
    )


def legacy_origin_available() -> ColumnElement[bool]:
    """Espelho SQL do ramo que SINTETIZA em `resolve_origin_connections`.

    Existe porque o ESTADO da origem (`origin_status`, que a lista e o detalhe
    devolvem e a tela usa para liberar "Nova conciliação" e "Sincronizar") é
    derivado por contagem, na MESMA query da listagem — e uma contagem que só
    olha `client_connections` diria `sem_origem` para todo cliente legado entre
    o deploy e o fim da conversão, justamente o cenário que este módulo existe
    para evitar. Derivar o estado chamando `resolve_origin_connections` por
    linha seria um N+1 na carteira inteira.

    As condições são as MESMAS do fallback, na mesma ordem: aberto, par
    completo de colunas preenchido e **nenhuma** conexão gravada (precedência —
    quem tem conexão usa conexão). Quem ligar/desligar é o chamador, com o
    veredito de `effective_fallback_enabled`.
    """
    tem_conexao = (
        select(ClientConnection.id)
        .where(ClientConnection.client_id == Client.id)
        .correlate(Client)
        .exists()
    )
    return and_(Client.closed_at.is_(None), _filled(_LEGACY_CREDENTIAL_COLUMNS), ~tem_conexao)


async def count_pending_conversion(db: AsyncSession) -> int:
    """Quantos clientes ainda dependem do fallback. `0` = conversão completa."""
    return int((await db.execute(clients_pending_conversion())).scalar_one())


async def effective_fallback_enabled(
    db: AsyncSession, settings: Settings, *, alert: bool = True
) -> bool:
    """O fallback está ligado DE FATO? (intenção contra realidade)

    - flag `True` → ligado, sem consultar nada.
    - flag `False` **e** nenhum cliente pendente → desligado. Promovido.
    - flag `False` **e** ainda há pendente → **ligado assim mesmo**, com
      `AlertCode.LEGACY_FALLBACK` no canal de plantão. Desligar a flag antes da
      conversão terminar deixaria clientes existentes sem operar; o certo é
      avisar alto e continuar servindo.

    `alert=False` para quem só está LENDO o estado (a derivação de
    `origin_status` na lista de clientes): `send_alert` não tem throttle, e uma
    listagem paginada não pode virar fonte de alerta de plantão. O veredito é o
    mesmo — o que muda é quem faz barulho: o startup e o caminho de OPERAÇÃO
    (`resolve_origin_connections`), que é onde a configuração errada importa.
    """
    if settings.LEGACY_CREDENTIALS_FALLBACK_ENABLED:
        return True
    pending = await count_pending_conversion(db)
    if pending == 0:
        return False
    if not alert:
        return True

    from app.core.alerting import Alert, AlertCode, send_alert

    log.error("legacy_fallback_forced_on", pending_clients=pending)
    await send_alert(
        Alert(
            code=AlertCode.LEGACY_FALLBACK,
            message=(
                f"LEGACY_CREDENTIALS_FALLBACK_ENABLED=false com {pending} cliente(s) "
                "ainda não convertido(s): o fallback segue LIGADO em memória. Rode "
                "scripts/convert_credentials_to_connections.py --verify."
            ),
        ),
        settings,
    )
    return True


def synthesize_legacy_connection(client: Client) -> ClientConnection:
    """Conexão `omie` ativa em MEMÓRIA, a partir das colunas antigas.

    **Nunca é adicionada à sessão.** O id é determinístico (UUID v5 sobre o id
    do cliente) para não aparecer como conexão nova a cada request; as colunas
    de credencial da conexão ficam **vazias** de propósito — o segredo continua
    onde está, e quem o decifra é `legacy_credentials`, com o locator ANTIGO.
    """
    return ClientConnection(
        id=uuid5(_SYNTHETIC_NAMESPACE, str(client.id)),
        client_id=client.id,
        provider_type=ProviderType.OMIE.value,
        label=SYNTHETIC_LABEL,
        status=ConnectionStatus.ATIVA.value,
        accounts_synced_at=client.omie_accounts_synced_at,
    )


def is_synthetic(connection: ClientConnection) -> bool:
    """Esta conexão veio do fallback (não existe no banco)?"""
    return connection.id == uuid5(_SYNTHETIC_NAMESPACE, str(connection.client_id))


def legacy_credentials_with(client: Client, *, cipher: ClientCipher) -> ProviderCredentials:
    """Decifra a credencial das colunas ANTIGAS, com o locator antigo e um cipher pronto.

    Cobre os dois formatos por construção: `ClientCipher` é multi-chave, então
    linha **bare** legada (chave global, sem AAD) e linha `v1:` (DEK + AAD)
    passam pelo mesmo `decrypt`.

    Variante SÍNCRONA (recebe o cipher) para quem já tem um em mãos e não quer
    uma segunda ida ao KMS. É por aqui — e só por aqui — que as colunas antigas
    são lidas: o gate de CI permite os nomes apenas neste arquivo.

    Levanta `ClientWithoutOmieCredentialsError` se as colunas estiverem vazias:
    seguir com credencial em branco viraria um 502 do provedor dizendo outra
    coisa.
    """
    from pydantic import SecretStr

    from app.core.exceptions import ClientWithoutOmieCredentialsError

    if not _has_legacy_credentials(client):
        raise ClientWithoutOmieCredentialsError(
            f"client {client.id} has no Omie credentials stored"
        )
    assert client.omie_app_key_encrypted is not None
    assert client.omie_app_key_iv is not None
    assert client.omie_app_secret_encrypted is not None
    assert client.omie_app_secret_iv is not None
    app_key = cipher.decrypt(
        client.omie_app_key_encrypted,
        client.omie_app_key_iv,
        field_locator(AAD_CLIENT_APP_KEY, client.id),
    )
    app_secret = cipher.decrypt(
        client.omie_app_secret_encrypted,
        client.omie_app_secret_iv,
        field_locator(AAD_CLIENT_APP_SECRET, client.id),
    )
    return {"app_key": SecretStr(app_key), "app_secret": SecretStr(app_secret)}


async def legacy_credentials(client: Client, *, settings: Settings) -> ProviderCredentials:
    """`legacy_credentials_with`, carregando o cipher do cliente antes."""
    cipher = await load_client_cipher(client, settings=settings)
    return legacy_credentials_with(client, cipher=cipher)


async def resolve_origin_connections(
    db: AsyncSession, client: Client, *, settings: Settings
) -> list[ClientConnection]:
    """As origens do cliente — reais, ou a sintetizada da janela de conversão.

    É por ESTA função que os consumidores (09.6) perguntam "quais origens este
    cliente tem?". Ler `client_connections` direto funcionaria hoje e
    quebraria todo cliente ainda não convertido.
    """
    connections = await ClientConnectionRepository(db).list_for_client(client.id)
    if connections:
        # Precedência: quem tem conexão usa conexão. As colunas antigas podem
        # até estar preenchidas (cliente convertido, salvaguarda de rollback) —
        # e não são olhadas.
        return connections
    if client.closed_at is not None or not _has_legacy_credentials(client):
        return []
    if not await effective_fallback_enabled(db, settings):
        return []

    # Só IDs — a credencial NUNCA entra no log (§3.3).
    log.info("legacy_credentials_fallback_used", client_id=str(client.id))
    return [synthesize_legacy_connection(client)]
