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

import re
import unicodedata
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
from app.integrations.omie.supplier_names import resolve_supplier_names
from app.modules.omie_data.schemas import OmieDepartamentoItem, OmieFornecedorItem
from app.modules.reconciliations.processing.name_affinity import supplier_affinity

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from app.integrations.omie.client import OmieClient
    from app.integrations.omie.clientes_cache import OmieClientesCache
    from app.integrations.omie.departamentos_cache import OmieDepartamentosCache
    from app.integrations.omie.fornecedores_directory_cache import (
        OmieFornecedoresDirectoryCache,
    )
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
        return _item(chosen)

    async def find_by_code(
        self,
        *,
        client_id: UUID,
        codigo: int,
        cache: OmieClientesCache,
        omie_client_factory: Callable[[], Awaitable[OmieClient]],
    ) -> OmieFornecedorItem:
        """O nome do cadastro deste código (86e3n70q0, bloco C).

        Serve a tela de edição do cliente, que guarda o fornecedor padrão como
        CÓDIGO e precisa mostrar QUEM ele é. **Não é uma segunda resolução**: é a
        mesma `resolve_supplier_names` sobre o MESMO `OmieClientesCache` que a aba
        de Divergências e a carteira usam (`ConsultarCliente`, TTL 6 h, negativo
        de 15 min). Por isso a resposta tem UM nome, o de exibição (razão social,
        ou o nome fantasia quando ela falta), em `razao_social`; `nome_fantasia`
        vem nulo.

        O resolvedor é fail-soft e devolve "sem nome" nos dois modos de falha;
        aqui eles se separam pelo cache negativo, que só a recusa da Omie marca:

        Raises:
            SupplierNotFoundError: a Omie disse que o código não existe (ou o
                cadastro não tem nome) — agora ou há menos de 15 min.
            OmieServerError: a Omie não respondeu; nada foi marcado e a próxima
                busca tenta de novo.
        """
        name = cache.get_name(client_id=client_id, codigo=codigo)
        if name is None and not cache.known_unresolved(client_id=client_id, codigo=codigo):
            # Só no miss: o hit não paga o unwrap da DEK nem a ida à Omie.
            omie_client = await omie_client_factory()
            try:
                resolved = await resolve_supplier_names(
                    cache=cache,
                    client_id=client_id,
                    codes={codigo},
                    omie_client=omie_client,
                    failure_log_event="omie_default_supplier_resolve_failed",
                )
            finally:
                await omie_client.aclose()
            name = resolved.get(codigo)
        if name is not None:
            return OmieFornecedorItem(codigo=codigo, razao_social=name, nome_fantasia=None)
        if cache.known_unresolved(client_id=client_id, codigo=codigo):
            raise SupplierNotFoundError(
                f"Código de fornecedor sem cadastro no Omie do cliente {client_id}",
                user_message=(
                    "Este fornecedor não existe mais no Omie do cliente. Escolha outro "
                    "fornecedor padrão."
                ),
            )
        raise OmieServerError(
            f"Falha de transporte ao resolver fornecedor por código do cliente {client_id}",
            user_message=(
                "O Omie não respondeu ao buscar o fornecedor. Tente novamente em instantes."
            ),
        )


def pick_supplier(cadastros: list[ClienteOmie]) -> ClienteOmie | None:
    """O cadastro ATIVO, se houver; senão o primeiro. Um critério só, usado pela
    busca da tela e pelo passo "fornecedor" do lançamento — a pessoa vê na tela o
    mesmo fornecedor que vai receber o título."""
    if not cadastros:
        return None
    return next((c for c in cadastros if c.is_active), cadastros[0])


# ----------------------------------------------------------------------
# Busca pelo NOME e sugestão pela descrição da compra (86e3qhhrd)
# ----------------------------------------------------------------------

#: Mínimo de caracteres da busca pelo nome, o mesmo que a tela espera antes de
#: perguntar: com menos, "contém" casa metade do cadastro.
SUPPLIER_SEARCH_MIN_CHARS = 3

#: Teto do texto da busca: a razão social do `IncluirCliente` é `string60`.
SUPPLIER_SEARCH_MAX_CHARS = 60

#: Quantos cadastros a busca devolve; o resto é contado em `total`.
SUPPLIER_SEARCH_LIMIT = 20

#: Quantas sugestões por linha da fatura.
SUPPLIER_SUGGESTION_LIMIT = 3

#: Teto de linhas por pedido de sugestão: o lote do lançamento também não passa
#: disso na tela (uma fatura grande cabe; um pedido abusivo não).
SUPPLIER_SUGGESTION_MAX_ENTRIES = 200

_NON_ALNUM = re.compile(r"[^0-9A-Z]+")


def normalize_search_text(text: str) -> str:
    """Maiúsculas, sem acento, sem pontuação, espaços colapsados.

    A mesma normalização da afinidade de nome do cruzamento
    (`name_affinity._normalize`): "Papelária-Demo" e "PAPELARIA DEMO" são o mesmo
    texto para a busca.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(_NON_ALNUM.sub(" ", without_accents.upper()).split())


def _item(cadastro: ClienteOmie) -> OmieFornecedorItem:
    return OmieFornecedorItem(
        codigo=cadastro.codigo_cliente_omie,
        razao_social=cadastro.razao_social,
        nome_fantasia=cadastro.nome_fantasia,
        cnpj_cpf=cadastro.cnpj_cpf or None,
    )


def _sort_name(cadastro: ClienteOmie) -> str:
    return (cadastro.display_name or "").casefold()


def search_directory(
    cadastros: list[ClienteOmie], query: str, *, limit: int = SUPPLIER_SEARCH_LIMIT
) -> tuple[list[OmieFornecedorItem], int]:
    """Os cadastros ATIVOS que contêm TODOS os pedaços do texto, e quantos são.

    "Contém" por pedaço, sem ordem: "demo papel" acha "PAPELARIA DEMO LTDA". O
    texto é procurado na razão social, no nome fantasia e nos dígitos do CNPJ/CPF
    (um pedaço do CNPJ também acha). Cadastro inativo fica fora: oferecer seria
    mostrar uma escolha que a pessoa não deveria fazer.

    Ordem: quem COMEÇA pelo texto vem antes de quem só o contém; depois o nome, e
    o código desempata (resultado estável entre duas buscas iguais).
    """
    needle = normalize_search_text(query)
    pieces = needle.split()
    if not pieces:
        return [], 0
    ranked: list[tuple[int, str, int, ClienteOmie]] = []
    for cadastro in cadastros:
        if not cadastro.is_active:
            continue
        names = [
            normalize_search_text(name)
            for name in (cadastro.razao_social, cadastro.nome_fantasia)
            if name
        ]
        digits = "".join(ch for ch in (cadastro.cnpj_cpf or "") if ch.isdigit())
        haystack = " ".join([*names, digits])
        if not all(piece in haystack for piece in pieces):
            continue
        starts = 0 if any(name.startswith(needle) for name in names) else 1
        ranked.append((starts, _sort_name(cadastro), cadastro.codigo_cliente_omie, cadastro))
    ranked.sort(key=lambda row: row[:3])
    return [_item(row[3]) for row in ranked[:limit]], len(ranked)


def suggest_for_description(
    cadastros: list[ClienteOmie],
    description: str | None,
    *,
    limit: int = SUPPLIER_SUGGESTION_LIMIT,
) -> list[OmieFornecedorItem]:
    """Os cadastros ATIVOS com MAIS tokens em comum com a descrição da compra.

    É a MESMA `supplier_affinity` que desempata o cruzamento (§5.5): contagem de
    tokens significativos, sem limiar arbitrário e sem IA. Só voltam os que
    empatam na MAIOR contagem: "SOFTWARE DEMO*ASSINATURA" sugere "SOFTWARE DEMO
    GMBH" (2 tokens) e não "PAPELARIA DEMO" (1, só o "DEMO"), que seria ruído na
    tela. Zero tokens em comum não sugere nada ("não sei", nunca "não é"). A
    descrição chega decifrada em memória e morre aqui: nada é logado nem
    persistido (§4.5).
    """
    if not description:
        return []
    scored: list[tuple[int, str, int, ClienteOmie]] = []
    for cadastro in cadastros:
        if not cadastro.is_active:
            continue
        score = max(
            supplier_affinity(cadastro.razao_social, description),
            supplier_affinity(cadastro.nome_fantasia, description),
        )
        if score > 0:
            scored.append((-score, _sort_name(cadastro), cadastro.codigo_cliente_omie, cadastro))
    if not scored:
        return []
    best = min(row[0] for row in scored)
    top = sorted((row for row in scored if row[0] == best), key=lambda row: row[:3])
    return [_item(row[3]) for row in top[:limit]]


class OmieFornecedorDirectoryService:
    """O diretório de cadastros do cliente, lido da Omie uma vez por TTL."""

    def __init__(self, cache: OmieFornecedoresDirectoryCache) -> None:
        self._cache = cache

    async def directory(
        self,
        *,
        client_id: UUID,
        omie_client_factory: Callable[[], Awaitable[OmieClient]],
        refresh: bool = False,
    ) -> list[ClienteOmie]:
        """Os cadastros do cliente; no miss, UMA leitura da Omie por vez.

        A trava por cliente faz as buscas que chegam juntas no primeiro acesso
        esperarem a primeira leitura em vez de cada uma ler o diretório inteiro.
        """
        if refresh:
            self._cache.invalidate(client_id)
        else:
            cached = self._cache.get(client_id)
            if cached is not None:
                return cached
        async with self._cache.lock_for(client_id):
            cached = self._cache.get(client_id)
            if cached is not None:
                return cached

            async def _call(omie: OmieClient) -> list[ClienteOmie]:
                return await omie.listar_clientes()

            cadastros = await _ask_omie(
                factory=omie_client_factory,
                call=_call,
                client_id=client_id,
                what="carregar os fornecedores",
            )
            self._cache.set(client_id, cadastros)
            return cadastros
