"""Resolução de NOME de devedor/fornecedor em runtime — um lugar só (S11, BACK 11.5).

**Por que este módulo existe.** A §4.5 mantém razão social fora do disco em claro:
o banco guarda `supplier_code` e o nome é resolvido a cada leitura, por
`ConsultarCliente` sobre o `OmieClientesCache` (TTL 6 h + cache negativo de 15 min).
Esse caminho já servia a aba de Divergências, **dentro** de
`reconciliations/review/service.py`. A carteira (S11) precisa do MESMO nome para os
MESMOS códigos, e copiar o método seria criar uma segunda leitura da origem sobre o
mesmo cache — duas implementações de fail-soft, duas chances de uma delas logar o
nome ou esquecer o cache negativo.

Então o método saiu de lá e virou **acessor** do caminho existente. A aba de
Divergências passou a chamar daqui; não há segunda leitura.

**Fail-soft nos dois modos de falha, com tratamentos DIFERENTES** — e é essa
distinção que justifica a função ter corpo em vez de ser um `get`:

- `OmieFaultError` (a origem respondeu e recusou: código excluído ou inexistente)
  → **cache negativo** de 15 min. Sem ele, cada render consultaria de novo um
  código que a origem já disse não conhecer;
- falha de transporte (timeout, 5xx) → **nada** é marcado, e o próximo render tenta
  outra vez. Marcar aqui silenciaria o retry natural de uma indisponibilidade
  passageira.

Nos dois casos o consumidor recebe o código sem nome e decide o que mostrar — nunca
um 502 que esconde a lista inteira.

⚠️ **O nome NUNCA é logado** (é PII, §3.3) e **nunca é persistido** (§4.5). O log de
falha carrega o código e o TIPO da exceção, nada mais.

⚠️ **Sequencial, sem `asyncio.gather`.** A Omie processa uma requisição por método
por credencial; paralelizar `ConsultarCliente` devolve `8020`/`1880`. Consultar por
CÓDIGO (e não paginar o cadastro inteiro) é o que torna N consultas cacheadas mais
baratas que uma varredura por render.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import OmieFaultError
from app.core.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Collection
    from uuid import UUID

    from app.integrations.omie.client import OmieClient
    from app.integrations.omie.clientes_cache import OmieClientesCache

log = get_logger(__name__)

#: Nome de evento de log padrão do caminho de falha. A aba de Divergências passa o
#: dela (`omie_entries_supplier_resolve_failed`) para não invalidar consulta de
#: observabilidade que já exista apontando para a chave antiga.
DEFAULT_FAILURE_LOG_EVENT = "omie_supplier_resolve_failed"


async def resolve_supplier_names(
    *,
    cache: OmieClientesCache | None,
    client_id: UUID,
    codes: Collection[int],
    omie_client: OmieClient | None,
    failure_log_event: str = DEFAULT_FAILURE_LOG_EVENT,
) -> dict[int, str]:
    """`supplier_code` → nome de exibição. Códigos irresolúveis ficam FORA do mapa.

    Ausência no mapa é o contrato: quem chama mostra o código marcado como "não
    resolvido", nunca um campo vazio nem o código repetido como se fosse nome.

    **Resolução em LOTE**: recebe o conjunto de códigos da página inteira, consulta
    de uma vez só o que falta no cache e devolve um mapa. É o que evita o N+1 — um
    `ConsultarCliente` por linha renderizada.

    `cache=None` ou `omie_client=None` devolve o que já estiver em cache (ou nada):
    é o caminho de quem não tem origem alcançável no momento, e ele não pode
    derrubar a leitura.
    """
    if not codes or cache is None:
        return {}

    resolved: dict[int, str] = {}
    to_consult: list[int] = []
    for code in codes:
        name = cache.get_name(client_id=client_id, codigo=code)
        if name is not None:
            resolved[code] = name
        elif not cache.known_unresolved(client_id=client_id, codigo=code):
            to_consult.append(code)

    if omie_client is None:
        return resolved

    for code in to_consult:
        try:
            cliente = await omie_client.consultar_cliente(codigo_cliente_omie=code)
        except OmieFaultError:
            # A origem RESPONDEU e recusou: o código não existe mais no cadastro.
            # Cache negativo, senão cada render paga a mesma consulta inútil.
            cache.mark_unresolved(client_id=client_id, codigo=code)
            continue
        except Exception as exc:
            # Transporte: timeout, 5xx. NÃO marca — indisponibilidade passageira
            # não prova nada sobre o cadastro, e marcar silenciaria o retry.
            log.warning(
                failure_log_event,
                client_id=str(client_id),
                codigo=code,
                error=type(exc).__name__,
            )
            continue

        name = cliente.display_name
        if name:
            cache.set_name(client_id=client_id, codigo=code, name=name)
            resolved[code] = name
        else:
            # Cadastro existe mas sem razão social nem nome fantasia —
            # irresolúvel de fato, então cache negativo também.
            cache.mark_unresolved(client_id=client_id, codigo=code)
    return resolved
