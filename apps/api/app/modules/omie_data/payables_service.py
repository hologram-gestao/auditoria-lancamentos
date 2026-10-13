"""Leituras de apoio do lançamento como Contas a Pagar (86e3n70q0, bloco B).

Duas perguntas que a tela de lançamento (bloco C) faz ao Omie do cliente antes
de mandar uma linha como provisão:

- **quais departamentos existem** — para o rateio (`distribuicao`) do título,
  obrigatório para quem tem `omie_department_required`;
- **quem é o fornecedor deste CNPJ** — para mostrar o cadastro que vai receber o
  título, ou dizer que ele não existe e pedir razão social e e-mail.

**Nenhuma das duas grava nada**, nem no Omie nem aqui: o cadastro de fornecedor
só acontece dentro do lançamento, pela chave idempotente. O nome do departamento
e o do fornecedor vivem só em memória (cache TTL no primeiro caso, nada no
segundo), como a §4.5 manda para nome de cliente final.

O erro do fornecedor NUNCA vira lista vazia nem "não encontrado": só o `5113` da
busca por CNPJ significa "não existe" (`OmieClient.listar_clientes_por_cnpj`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.cnpj import format_cnpj
from app.core.exceptions import (
    OmieAuthError,
    OmieFaultError,
    OmieServerError,
    OmieTimeoutError,
    SupplierNotFoundError,
)
from app.core.logging import get_logger
from app.modules.omie_data.schemas import OmieDepartamentoItem, OmieFornecedorItem

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from app.integrations.omie.client import OmieClient
    from app.integrations.omie.departamentos_cache import OmieDepartamentosCache
    from app.integrations.omie.schemas import ClienteOmie, DepartamentoOmie

log = get_logger(__name__)


async def _ask_omie[T](
    *,
    factory: Callable[[], Awaitable[OmieClient]],
    call: Callable[[OmieClient], Awaitable[T]],
    client_id: UUID,
    what: str,
) -> T:
    """Uma chamada de LEITURA ao Omie com a mensagem certa para cada falha.

    Mesmo desenho do `OmieCategoriasService._fetch`: credencial, timeout e 5xx
    viram a exceção da mesma família com uma frase que diz o que fazer;
    `faultstring` sobe com a mensagem do fornecedor, que não entra em log.
    """
    omie_client = await factory()
    try:
        return await call(omie_client)
    except OmieAuthError as exc:
        raise OmieAuthError(
            f"Auth falhou ao {what} do cliente {client_id}: {exc.message}",
            user_message=(
                f"As credenciais Omie cadastradas estão inválidas. Atualize-as para {what}."
            ),
        ) from exc
    except OmieTimeoutError as exc:
        raise OmieTimeoutError(
            f"Timeout ao {what} do cliente {client_id}: {exc.message}",
            user_message=(
                f"O Omie não respondeu no tempo esperado ao {what}. Tente novamente em instantes."
            ),
        ) from exc
    except OmieServerError as exc:
        raise type(exc)(
            f"5xx do Omie ao {what} do cliente {client_id}: {exc.message}",
            user_message=exc.user_message,
        ) from exc
    except OmieFaultError:
        log.warning("omie_payables_read_fault", client_id=str(client_id), what=what)
        raise
    finally:
        await omie_client.aclose()


class OmieDepartamentosService:
    """Departamentos ATIVOS do cliente, servidos do cache quando frescos."""

    def __init__(self, cache: OmieDepartamentosCache) -> None:
        self._cache = cache

    async def list_departamentos(
        self,
        *,
        client_id: UUID,
        omie_client_factory: Callable[[], Awaitable[OmieClient]],
        refresh: bool = False,
    ) -> list[OmieDepartamentoItem]:
        """Ativos, na ordem da árvore do Omie (`estrutura`), depois pelo nome.

        Inativos ficam FORA: a Omie não aceita rateio para eles e oferecer seria
        mostrar uma escolha que o lançamento vai recusar. Sintéticos ficam DENTRO:
        a resposta real não diz quem é sintético (sem `nivel_totalizador`), só a
        convenção de nome do cliente — a pessoa escolhe.
        """
        if refresh:
            self._cache.invalidate(client_id)
        cached = None if refresh else self._cache.get(client_id)
        if cached is None:

            async def _call(omie: OmieClient) -> list[DepartamentoOmie]:
                return await omie.listar_departamentos()

            cached = await _ask_omie(
                factory=omie_client_factory,
                call=_call,
                client_id=client_id,
                what="carregar os departamentos",
            )
            self._cache.set(client_id, cached)
        return sorted(
            (
                OmieDepartamentoItem(codigo=d.codigo, descricao=d.descricao, estrutura=d.estrutura)
                for d in cached
                if d.is_active
            ),
            key=lambda item: (item.estrutura or "", item.descricao.casefold(), item.codigo),
        )


class OmieFornecedorLookupService:
    """Busca UM fornecedor pelo CNPJ. Nunca grava, nunca cacheia nome."""

    async def find_by_cnpj(
        self,
        *,
        client_id: UUID,
        cnpj_digits: str,
        omie_client_factory: Callable[[], Awaitable[OmieClient]],
    ) -> OmieFornecedorItem:
        """O cadastro deste CNPJ, preferindo o ATIVO.

        Raises:
            SupplierNotFoundError: a Omie respondeu `5113` (não existe).
        """

        async def _call(omie: OmieClient) -> list[ClienteOmie]:
            return await omie.listar_clientes_por_cnpj(cnpj_cpf=format_cnpj(cnpj_digits))

        cadastros = await _ask_omie(
            factory=omie_client_factory,
            call=_call,
            client_id=client_id,
            what="buscar o fornecedor",
        )
        chosen = pick_supplier(cadastros)
        if chosen is None:
            raise SupplierNotFoundError(f"CNPJ sem cadastro no Omie do cliente {client_id}")
        return OmieFornecedorItem(
            codigo=chosen.codigo_cliente_omie,
            razao_social=chosen.razao_social,
            nome_fantasia=chosen.nome_fantasia,
        )


def pick_supplier(cadastros: list[ClienteOmie]) -> ClienteOmie | None:
    """O cadastro ATIVO, se houver; senão o primeiro. Um critério só, usado pela
    busca da tela e pelo passo "fornecedor" do lançamento — a pessoa vê na tela o
    mesmo fornecedor que vai receber o título."""
    if not cadastros:
        return None
    return next((c for c in cadastros if c.is_active), cadastros[0])
