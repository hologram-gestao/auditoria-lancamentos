"""O lock por CLIENTE das chamadas à origem — fonte ÚNICA (Sprint 11, BACK 11.2).

**Por que existe.** A Omie processa **uma requisição por método por credencial**:
duas chamadas do mesmo método com a mesma `app_key` ao mesmo tempo voltam como
`8020`/`1880`. Isso não é teoria — foi incidente real no produto, e é o motivo de
`OmieLancamentoCache` ter nascido com um `dict[UUID, asyncio.Lock]` interno para
serializar `populate_from_extrato` por cliente.

**Por que MUDOU de casa.** A ingestão da carteira (S11) fala com a MESMA
credencial do mesmo cliente, por outro caminho. Um lock por instância de cache
serializaria o cache consigo mesmo e deixaria a ingestão colidir com ele — dois
mecanismos, um buraco. Então o lock saiu de dentro do cache para cá, e o cache
passou a consumir **este** registro. É "um mecanismo", como o PRD exige, e não um
segundo.

**Escopo: processo.** `asyncio.Lock` não atravessa instância do Cloud Run, e este
módulo não finge que atravessa. Ele resolve a colisão que o produto de fato tem
(dois caminhos do MESMO processo disparando contra o mesmo cliente) e não a que
exigiria lock distribuído. A defesa contra a segunda é a serialização do lote no
comando em batch, que roda um cliente de cada vez.

**Regra que não pode ser quebrada: NUNCA aninhar.** Um trecho que já segura o
lock de um cliente não pode chamar outro trecho que o peça — `asyncio.Lock` não é
reentrante e o resultado é deadlock silencioso, sem traceback. Hoje há dois
tomadores (`populate_from_extrato` e a ingestão de títulos) e nenhum chama o
outro; tomador novo precisa manter isso.
"""

from __future__ import annotations

import asyncio
from uuid import UUID


class OriginClientLocks:
    """Um `asyncio.Lock` por cliente, criado na primeira vez que é pedido.

    `dict` simples basta: asyncio é single-threaded, então o `setdefault` é
    atômico em relação a outras corrotinas, e ~100 clientes x 1 Lock é custo
    desprezível. **Não há expurgo**: o lock de um cliente que parou de aparecer
    é um objeto vazio de alguns bytes, e um expurgo por TTL introduziria a
    janela em que duas corrotinas pegam locks diferentes para o mesmo cliente —
    exatamente a colisão que isto existe para evitar.
    """

    def __init__(self) -> None:
        self._locks: dict[UUID, asyncio.Lock] = {}

    def for_client(self, client_id: UUID) -> asyncio.Lock:
        """O lock DAQUELE cliente. Mesma chamada, mesmo objeto, sempre.

        Use como `async with locks.for_client(client_id):` — o `Lock` do asyncio
        já é um gerenciador de contexto, e embrulhá-lo num segundo só esconderia
        de quem lê que há um lock ali.
        """
        return self._locks.setdefault(client_id, asyncio.Lock())

    def tracked_clients(self) -> int:
        """Quantos clientes já pediram lock. Só para teste e log — nunca lógica."""
        return len(self._locks)


#: O registro do PROCESSO. Importado por quem fala com a origem — nunca
#: instanciado de novo: um segundo `OriginClientLocks()` seria um segundo
#: mecanismo, e os dois não se conhecem.
origin_client_locks = OriginClientLocks()
