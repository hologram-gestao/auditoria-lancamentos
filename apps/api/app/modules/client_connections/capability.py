"""O predicado ÚNICO de "esta conexão serve?" (Sprint 9, BACK 09.2 — R3).

Uma função. Toda rota, job e service que precise de uma origem chama **ela** —
comparar `status == "ativa"` na mão em qualquer outro lugar é proibido, pelo
mesmo motivo que `resolve_client_access` é única (§3.15): duas cópias divergem,
e a que esquecer um caso vira um 500 no lugar de um 409 acionável.

**Conexão capaz = `status == ativa` E o adaptador do tipo declara a capacidade.**
`inativa` e `erro` NÃO são capazes: a primeira foi desligada de propósito, a
segunda tem credencial recusada — nas duas, usar a conexão produziria um erro do
provedor, tarde, dentro de um job.

**A taxonomia é fechada em três**, e a ordem do diagnóstico importa porque o que
o usuário precisa fazer é diferente em cada caso:

    sem_conexao        → não há conexão nenhuma            → conectar
    origem_com_erro    → há conexão, nenhuma ativa         → reconectar
    capacidade_ausente → há ativa, nenhuma faz isso        → nada a consertar

Todas **409** — são estados esperados da configuração do cliente, não falhas do
sistema (§7: erro de negócio é 4xx com mensagem acionável; 5xx mentiria "tente
de novo" e ainda poluiria o alerting).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.exceptions import (
    NoOriginConnectionError,
    OriginCapabilityMissingError,
    OriginConnectionInErrorError,
)
from app.db.models.client_connection import ConnectionStatus
from app.integrations.providers.registry import capabilities_for

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.db.models.client_connection import ClientConnection
    from app.integrations.providers.base import Capability


def connection_supports(connection: ClientConnection, capability: Capability) -> bool:
    """A conexão, sozinha, é capaz disso? (ativa **e** o tipo declara)."""
    if connection.status != ConnectionStatus.ATIVA.value:
        return False
    return capability in capabilities_for(connection.provider_type)


def select_capable_connection(
    connections: Sequence[ClientConnection], capability: Capability
) -> ClientConnection:
    """A conexão que atende a `capability`, ou o 409 que diz por que não.

    Empate (duas ativas do mesmo tipo, rótulos diferentes) resolve pela PRIMEIRA
    da sequência — a ordenação é responsabilidade de quem consulta o
    repositório, e escolher "a mais recente" aqui esconderia de quem lê a
    chamada que existe uma escolha sendo feita.
    """
    if not connections:
        raise NoOriginConnectionError("client has no origin connection")

    active = [c for c in connections if c.status == ConnectionStatus.ATIVA.value]
    if not active:
        raise OriginConnectionInErrorError(
            "client has origin connections, none active",
        )

    for connection in active:
        if capability in capabilities_for(connection.provider_type):
            return connection

    raise OriginCapabilityMissingError(
        f"no active connection provides capability {capability.value!r}",
    )
