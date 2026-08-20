"""Categorias Omie do cliente, com cache por tenant (Sprint 7 / BACK 07.3).

O `cCodCateg` é **obrigatório** no `IncluirLancCC` e **não existe na fatura** —
sem uma lista rápida de categorias, o passo de classificação vira o gargalo do
fluxo (é a suposição S-3 do PRD: "a categoria pode ser classificada pelo
operador em tempo aceitável"). O que esta camada pode fazer a respeito é não
piorar: entregar a lista **completa e cacheada**, para que a busca do combobox
seja instantânea e local, sem uma chamada Omie por tecla digitada.

O erro do fornecedor **não** vira lista vazia. A Omie responde HTTP 200 com
`faultstring` em erro (§6.3): tratar isso como "cliente sem categorias" faria a
tela mostrar um combobox vazio e o operador concluir que o cadastro dele está
vazio — quando o que houve foi credencial inválida ou instabilidade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from app.core.exceptions import (
    OmieAuthError,
    OmieFaultError,
    OmieServerError,
    OmieTimeoutError,
)
from app.core.logging import get_logger
from app.modules.omie_data.schemas import OmieCategoriaItem

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.integrations.omie.categorias_cache import OmieCategoriasCache
    from app.integrations.omie.client import OmieClient
    from app.integrations.omie.schemas import CategoriaOmie

log = get_logger(__name__)


class OmieCategoriasService:
    """Lista as categorias de um cliente, servindo do cache quando fresco."""

    def __init__(self, cache: OmieCategoriasCache) -> None:
        self._cache = cache

    async def list_categorias(
        self,
        *,
        client_id: UUID,
        omie_client_factory: Callable[[], Awaitable[OmieClient]],
        refresh: bool = False,
    ) -> list[OmieCategoriaItem]:
        """Categorias ATIVAS do cliente, ordenadas por descrição.

        Args:
            client_id: tenant já autorizado pelo caller — é a chave do cache.
            omie_client_factory: constrói o `OmieClient` só no caminho de
                miss. É `async` de propósito: construir o cliente exige
                desembrulhar a DEK do tenant, que em staging/prod é uma ida ao
                Cloud KMS. Em cache hit **nada** disso acontece.
            refresh: ignora o cache e rebusca. É o caminho explícito para o
                operador que acabou de criar uma categoria no Omie — melhor do
                que um TTL curto que faria toda tela pagar a latência.
        """
        if refresh:
            self._cache.invalidate(client_id)
        else:
            cached = self._cache.get(client_id)
            if cached is not None:
                return _to_items(cached)

        categorias = await self._fetch(client_id=client_id, factory=omie_client_factory)
        self._cache.set(client_id, categorias)
        return _to_items(categorias)

    async def _fetch(
        self,
        *,
        client_id: UUID,
        factory: Callable[[], Awaitable[OmieClient]],
    ) -> list[CategoriaOmie]:
        """Chama a Omie, convertendo cada falha em erro tratado — nunca em [].

        `except Exception` seria largo demais aqui, mas o inverso (deixar o
        `OmieFaultError` subir cru) devolveria 502 com a mensagem técnica do
        fornecedor. Cada ramo abaixo escolhe a mensagem que o operador precisa
        ler — e nenhum deles inclui credencial.
        """
        omie_client = await factory()
        try:
            return await omie_client.listar_categorias()
        except OmieAuthError as exc:
            raise OmieAuthError(
                f"Auth falhou ao listar categorias do cliente {client_id}: {exc.message}",
                user_message=(
                    "As credenciais Omie cadastradas estão inválidas. "
                    "Atualize-as para carregar as categorias."
                ),
            ) from exc
        except OmieTimeoutError as exc:
            raise OmieTimeoutError(
                f"Timeout ao listar categorias do cliente {client_id}: {exc.message}",
                user_message=(
                    "O Omie não respondeu no tempo esperado ao carregar as categorias. "
                    "Tente novamente em instantes."
                ),
            ) from exc
        except OmieServerError as exc:
            raise OmieServerError(
                f"5xx do Omie ao listar categorias do cliente {client_id}: {exc.message}",
                user_message=(
                    "O Omie está com instabilidade no momento. Tente novamente em instantes."
                ),
            ) from exc
        except OmieFaultError:
            # `faultstring` com HTTP 200 — o modo de falha que mais engana.
            # A mensagem do fornecedor já vem no `user_message` do erro; o log
            # registra só o tenant (o texto é livre e não entra em log, §3.3).
            log.warning("omie_categorias_fault", client_id=str(client_id))
            raise
        finally:
            await omie_client.aclose()


def _to_items(categorias: list[CategoriaOmie]) -> list[OmieCategoriaItem]:
    """Filtra inativas e ordena por descrição. Nunca loga o conteúdo."""
    return sorted(
        (
            OmieCategoriaItem(codigo=c.codigo, descricao=c.descricao)
            for c in categorias
            if c.is_active
        ),
        key=lambda item: (item.descricao.casefold(), item.codigo),
    )
