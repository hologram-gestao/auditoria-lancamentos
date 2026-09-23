"""Adaptador Omie — o PRIMEIRO `OriginProvider` (Sprint 9, BACK 09.2 — R3).

Embrulha o `OmieClient`, que continua sendo a única coisa que fala com a API da
Omie e continua **intocado**: todo contrato verificado contra fixture real
(`tests/fixtures/omie/`) segue valendo, e o `omie_posting` continua usando o
client cru, sem passar por aqui. Este arquivo só traduz — nomes do Omie para os
DTOs neutros, erros do Omie para os erros neutros.

**A resolução do cliente-demo mudou de casa.** O prefixo `FAKE_DEMO_OMIE_` que
troca `OmieClient` por `MockOmieClient` morava em `modules/clients/omie_factory.py`;
agora mora em `build_omie_raw_client`, aqui. Motivo: o seed demo
(`scripts/seed_demo_client.py`), o ambiente local de validação e o e2e mockado
dependem desse prefixo, e a partir da 09.3 a credencial pode chegar por
`client_connections` em vez das colunas de `clients` — se a heurística ficasse
no factory antigo, o caminho novo entraria em produção sem ela e o demo iria
para a rede de verdade. Uma casa só, servindo os dois caminhos.

**As 4 capacidades.** O Omie verifica credencial, lista contas, lista
lançamentos **e escreve** (`IncluirLancCC`, Sprint 7 — §3.16). Declarar
`ESCREVER` aqui é dizer o que o provedor SABE fazer; se aquele lançamento pode
sair é outra decisão, a do kill-switch `OMIE_POSTING_ENABLED`, e ela continua
onde está.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import SecretStr

from app.core.exceptions import OmieAuthError, ProviderAuthError, ValidationAppError
from app.db.models.client_connection import ProviderType
from app.integrations.omie.client import OmieClient, OmieCredentials
from app.integrations.omie.mock_client import FAKE_DEMO_KEY_PREFIX, MockOmieClient
from app.integrations.providers.base import (
    Capability,
    ProviderAccount,
    ProviderCredentials,
    ProviderEntry,
)

if TYPE_CHECKING:
    from datetime import date

    import httpx

    from app.core.config import Settings

#: Chaves do JSON de credencial do Omie. Fonte ÚNICA — a 09.3 valida o payload
#: de criação de conexão contra elas, e o adaptador as lê daqui.
OMIE_CREDENTIAL_KEYS = ("app_key", "app_secret")

#: O que o Omie sabe fazer. Congelado como `frozenset` para não ser mutado por
#: engano por quem só queria ler.
OMIE_CAPABILITIES = frozenset(
    {
        Capability.VERIFICAR_CREDENCIAL,
        Capability.LISTAR_CONTAS,
        Capability.LISTAR_LANCAMENTOS,
        Capability.ESCREVER,
    }
)


def build_omie_raw_client(
    credentials: OmieCredentials,
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> OmieClient:
    """`OmieClient` (ou `MockOmieClient`) pronto — o ÚNICO lugar que decide qual.

    Heurística de cliente-demo: credencial gerada por `seed_demo_client.py`
    começa com `FAKE_DEMO_OMIE_`. O prefixo é improvável numa key real e o Omie
    de produção nunca o aceitaria — usá-lo como flag implícita evita coluna e
    migration, e mantém o seed como única fonte de ativação. NUNCA usar prefixo
    parecido em credencial real.
    """
    if credentials.app_key.get_secret_value().startswith(FAKE_DEMO_KEY_PREFIX):
        return MockOmieClient(credentials, settings)
    return OmieClient(credentials, settings, http_client=http_client)


def omie_credentials_from(credentials: ProviderCredentials) -> OmieCredentials:
    """Mapa neutro → par tipado do Omie. Chave faltando é 422, não `KeyError`."""
    missing = [key for key in OMIE_CREDENTIAL_KEYS if key not in credentials]
    if missing:
        raise ValidationAppError(
            f"omie credentials missing keys: {', '.join(missing)}",
            user_message="A conexão com o Omie exige App Key e App Secret.",
        )
    return OmieCredentials(
        app_key=credentials["app_key"],
        app_secret=credentials["app_secret"],
    )


class OmieProvider:
    """`OriginProvider` do Omie. Traduz, não reimplementa."""

    def __init__(
        self,
        credentials: ProviderCredentials,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = build_omie_raw_client(
            omie_credentials_from(credentials), settings, http_client=http_client
        )

    @property
    def provider_type(self) -> str:
        return ProviderType.OMIE.value

    @property
    def capabilities(self) -> frozenset[Capability]:
        return OMIE_CAPABILITIES

    @property
    def raw_client(self) -> OmieClient:
        """Escotilha para o que já está verificado contra fixture real.

        `omie_posting` e o resto do fluxo do Omie continuam falando com o client
        cru: reescrevê-los atrás dos DTOs neutros nesta task seria mexer em
        contrato validado por captura, sem necessidade nenhuma.
        """
        return self._client

    async def verify_credentials(self) -> None:
        """`ListarClientes` com página de 1 — a chamada mais barata que autentica."""
        try:
            await self._client.listar_clientes_minimal()
        except OmieAuthError as exc:
            raise ProviderAuthError(str(exc)) from exc

    async def list_accounts(self) -> list[ProviderAccount]:
        try:
            contas = await self._client.listar_contas_correntes()
        except OmieAuthError as exc:
            raise ProviderAuthError(str(exc)) from exc
        return [
            ProviderAccount(
                external_id=str(conta.n_cod_cc),
                name=conta.descricao,
                bank_code=conta.codigo_banco,
                account_type=conta.tipo,
            )
            for conta in contas
        ]

    async def list_entries(
        self, *, account_external_id: str, start: date, end: date
    ) -> list[ProviderEntry]:
        """Extrato do período. `account_external_id` é o `nCodCC`, como texto."""
        try:
            n_cod_cc = int(account_external_id)
        except ValueError as exc:
            raise ValidationAppError(
                f"omie account id is not numeric: {account_external_id!r}",
                user_message="A conta informada não é válida para o Omie.",
            ) from exc
        try:
            lancamentos = await self._client.listar_extrato(
                n_cod_cc=n_cod_cc, data_inicial=start, data_final=end
            )
        except OmieAuthError as exc:
            raise ProviderAuthError(str(exc)) from exc
        return [
            ProviderEntry(
                external_id=str(lanc.n_cod_lancamento),
                entry_date=lanc.d_data_lancamento,
                # Já com sinal: a convenção de natureza (D/C de conta corrente,
                # P/R de cartão) morre aqui, no adaptador.
                amount=lanc.signed_amount,
                description=lanc.description,
                status=lanc.c_situacao,
                # CÓDIGO, nunca nome (§4.5) — descrição de categoria e razão
                # social são resolvidas em runtime, com cache TTL.
                category_code=lanc.c_cod_categoria,
                supplier_code=str(lanc.n_cod_cliente) if lanc.n_cod_cliente else None,
            )
            for lanc in lancamentos
        ]

    async def aclose(self) -> None:
        await self._client.aclose()


def omie_credentials_payload(app_key: str, app_secret: str) -> ProviderCredentials:
    """Atalho tipado para montar o mapa do Omie a partir de texto decifrado."""
    return {"app_key": SecretStr(app_key), "app_secret": SecretStr(app_secret)}
