"""Registry de provedores de origem — tipo → adaptador (Sprint 9, BACK 09.2).

Um `dict` literal, e nada além disso. **Sem plugin loader dinâmico e sem tabela
de provedores no banco**: provedor é código, e código novo é deploy, não
configuração em runtime. Um loader por entry point tornaria "quais origens
existem" uma pergunta cuja resposta muda sem revisão de código — exatamente o
que não se quer num caminho que carrega credencial de cliente.

Tipo desconhecido é **422** (`ValidationAppError`), não 500: quem manda um tipo
que não existe está mandando entrada inválida.

Sprint 14 (BACK 14.1): o segundo provedor entrou — `arquivo` — e com ele a
pergunta "este tipo exige credencial?" (`requires_credentials`), respondida pela
capacidade declarada e num lugar só.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import ValidationAppError
from app.db.models.client_connection import ProviderType
from app.integrations.providers.base import Capability, OriginProvider
from app.integrations.providers.file_adapter import FILE_CAPABILITIES, build_file_provider
from app.integrations.providers.omie_adapter import OMIE_CAPABILITIES, OmieProvider

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from app.core.config import Settings
    from app.integrations.providers.base import ProviderCredentials

#: O mapa. Provedor novo entra aqui E em `_CAPABILITIES_BY_TYPE` — as duas
#: entradas existem porque a segunda responde "o que este tipo faz?" sem
#: precisar de credencial (é o que a API devolve no schema de conexão).
_PROVIDERS: dict[str, Callable[..., OriginProvider]] = {
    ProviderType.OMIE.value: OmieProvider,
    ProviderType.ARQUIVO.value: build_file_provider,
}

_CAPABILITIES_BY_TYPE: dict[str, frozenset[Capability]] = {
    ProviderType.OMIE.value: OMIE_CAPABILITIES,
    ProviderType.ARQUIVO.value: FILE_CAPABILITIES,
}


#: Os tipos cujo adaptador expõe o `OmieClient` cru (`raw_client`) — o que a
#: conciliação, a revisão, a exportação, o plano de contas e os dados do Omie
#: consomem (fluxo verificado contra fixture real, ver `client_connections/origin.py`).
#: Provedor novo com client compatível entra aqui também. `arquivo` NÃO entra:
#: ele só alimenta a base de movimentos (S14, ADR-083-BE).
_ORIGIN_CLIENT_TYPES: frozenset[str] = frozenset({ProviderType.OMIE.value})


def _known(provider_type: str) -> str:
    if provider_type not in _PROVIDERS:
        raise ValidationAppError(
            f"unknown provider type: {provider_type!r}",
            user_message="Tipo de origem não suportado.",
        )
    return provider_type


def get_provider(
    provider_type: str,
    credentials: ProviderCredentials,
    settings: Settings,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> OriginProvider:
    """Adaptador pronto para o tipo pedido. Tipo desconhecido → 422."""
    factory = _PROVIDERS[_known(provider_type)]
    return factory(credentials, settings, http_client=http_client)


def capabilities_for(provider_type: str) -> frozenset[Capability]:
    """O que um tipo de origem sabe fazer, **sem credencial nenhuma**.

    É por aqui que a resposta de conexão (09.3) monta o campo `capabilities`:
    capacidade é propriedade do TIPO, não da instância conectada, então
    perguntar não pode exigir decifrar segredo de cliente.
    """
    return _CAPABILITIES_BY_TYPE[_known(provider_type)]


def requires_credentials(provider_type: str) -> bool:
    """Este tipo de origem guarda um segredo? (Sprint 14, BACK 14.1)

    Derivado da capacidade, não de uma segunda lista: um provedor que sabe
    VERIFICAR credencial tem credencial; um que não declara (`arquivo`) não tem
    o que guardar. É a regra ÚNICA que decide, na criação e na troca de
    conexão, se `credentials` é obrigatório ou proibido — e, na leitura da
    origem, se a ausência de ciphertext é estado normal ou defeito.
    """
    return Capability.VERIFICAR_CREDENCIAL in capabilities_for(provider_type)


def offers_origin_client(provider_type: str) -> bool:
    """O adaptador deste tipo expõe o client cru do ERP? (S14, ADR-083-BE)

    Pergunta de TIPO, sem credencial: é o que deixa a criação da conciliação
    recusar cliente só-arquivo ANTES de gravar a sessão, sem decifrar nada.
    """
    return _known(provider_type) in _ORIGIN_CLIENT_TYPES


def supported_provider_types() -> tuple[str, ...]:
    """Os tipos que o registry resolve hoje — para schema, doc e teste."""
    return tuple(_PROVIDERS)
