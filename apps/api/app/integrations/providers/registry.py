"""Registry de provedores de origem — tipo → adaptador (Sprint 9, BACK 09.2).

Um `dict` literal, e nada além disso. **Sem plugin loader dinâmico e sem tabela
de provedores no banco**: provedor é código, e código novo é deploy, não
configuração em runtime. Um loader por entry point tornaria "quais origens
existem" uma pergunta cuja resposta muda sem revisão de código — exatamente o
que não se quer num caminho que carrega credencial de cliente.

Tipo desconhecido é **422** (`ValidationAppError`), não 500: quem manda um tipo
que não existe está mandando entrada inválida.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import ValidationAppError
from app.db.models.client_connection import ProviderType
from app.integrations.providers.base import Capability, OriginProvider
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
}

_CAPABILITIES_BY_TYPE: dict[str, frozenset[Capability]] = {
    ProviderType.OMIE.value: OMIE_CAPABILITIES,
}


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


def supported_provider_types() -> tuple[str, ...]:
    """Os tipos que o registry resolve hoje — para schema, doc e teste."""
    return tuple(_PROVIDERS)
