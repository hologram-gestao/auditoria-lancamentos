"""O lock por cliente é UM mecanismo, não dois (Sprint 11, BACK 11.2).

O PRD é explícito: "serializar as chamadas à origem por cliente com o **mesmo**
lock por cliente que `lancamento_cache.py` já usa, em vez de um segundo
mecanismo". Este módulo é o que torna isso verificável em vez de declarado.

**Por que importa.** A Omie processa uma requisição por método por credencial.
Antes desta sprint o lock morava DENTRO de `OmieLancamentoCache`; se a ingestão da
carteira tivesse criado o seu, os dois seriam invisíveis um para o outro e as duas
rotas colidiriam contra a mesma `app_key` — que é exatamente o incidente que o
lock existe para evitar, reintroduzido por um refactor que "parecia" correto.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from app.integrations.omie.client_locks import OriginClientLocks, origin_client_locks
from app.integrations.omie.lancamento_cache import OmieLancamentoCache
from app.modules.client_titles.service import ClientTitlesSyncService


def _service(locks: OriginClientLocks | None = None) -> ClientTitlesSyncService:
    """O serviço só para inspecionar qual registro de lock ele adotou.

    As dependências vão como `None` de propósito: nada aqui chama banco, origem
    nem repositório — o que está sob teste é a escolha feita no `__init__`.
    """
    return ClientTitlesSyncService(
        db=None,  # type: ignore[arg-type]
        repository=None,  # type: ignore[arg-type]
        clients=None,  # type: ignore[arg-type]
        settings=None,  # type: ignore[arg-type]
        locks=locks,
    )


class TestFonteUnica:
    def test_o_mesmo_cliente_recebe_sempre_o_mesmo_lock(self) -> None:
        locks = OriginClientLocks()
        client_id = uuid4()
        assert locks.for_client(client_id) is locks.for_client(client_id)

    def test_clientes_diferentes_recebem_locks_diferentes(self) -> None:
        """Serializar por cliente, não globalmente: dois clientes têm credenciais
        diferentes e não colidem entre si."""
        locks = OriginClientLocks()
        assert locks.for_client(uuid4()) is not locks.for_client(uuid4())

    def test_o_cache_de_lancamentos_consome_o_registro_compartilhado(self) -> None:
        """O lock saiu de DENTRO do cache — é isto que prova que saiu de verdade.

        Se alguém reintroduzir um `dict` interno no cache, este teste vermelho é
        o aviso de que a ingestão da carteira voltou a poder colidir com a Tela
        de Revisão.
        """
        cache = OmieLancamentoCache()
        assert cache._locks is origin_client_locks
        assert not hasattr(cache, "_populate_locks")

    def test_o_servico_da_carteira_consome_o_mesmo_registro(self) -> None:
        """Construído sem `locks`, o serviço cai no singleton do processo."""
        assert _service()._locks is origin_client_locks

    def test_cache_e_carteira_compartilham_o_lock_do_mesmo_cliente(self) -> None:
        """O ponto todo: dois caminhos, uma credencial, um lock."""
        locks = OriginClientLocks()
        cache = OmieLancamentoCache(locks=locks)
        service = _service(locks)
        client_id = uuid4()
        assert cache._locks.for_client(client_id) is service._locks.for_client(client_id)


class TestExclusaoMutua:
    async def test_dois_tomadores_do_mesmo_cliente_nao_se_sobrepoem(self) -> None:
        """A exclusão mútua é o comportamento, não o objeto.

        Três corrotinas entram; o pico de ocupação simultânea tem de ser 1 — é a
        mesma asserção que o teste do adaptador faz, aqui no nível do lock.
        """
        locks = OriginClientLocks()
        client_id = uuid4()
        em_voo = 0
        pico = 0

        async def tomador() -> None:
            nonlocal em_voo, pico
            async with locks.for_client(client_id):
                em_voo += 1
                pico = max(pico, em_voo)
                await asyncio.sleep(0)
                em_voo -= 1

        await asyncio.gather(tomador(), tomador(), tomador())
        assert pico == 1

    async def test_clientes_diferentes_nao_se_bloqueiam(self) -> None:
        """Serializar tudo seria correto e inútil: a carteira de 100 clientes
        viraria uma fila única. O limite da origem é por credencial."""
        locks = OriginClientLocks()
        a, b = uuid4(), uuid4()
        ordem: list[str] = []

        async def tomador(nome: str, client_id: UUID) -> None:
            async with locks.for_client(client_id):
                ordem.append(f"{nome}-entrou")
                await asyncio.sleep(0)
                ordem.append(f"{nome}-saiu")

        await asyncio.gather(tomador("a", a), tomador("b", b))
        # Se se bloqueassem, "b-entrou" só apareceria depois de "a-saiu".
        assert ordem.index("b-entrou") < ordem.index("a-saiu")

    async def test_excecao_dentro_do_lock_libera_o_lock(self) -> None:
        """Falha da origem não pode deixar o cliente travado para sempre."""
        locks = OriginClientLocks()
        client_id = uuid4()

        try:
            async with locks.for_client(client_id):
                raise RuntimeError("origem caiu")
        except RuntimeError:
            pass

        assert not locks.for_client(client_id).locked()
