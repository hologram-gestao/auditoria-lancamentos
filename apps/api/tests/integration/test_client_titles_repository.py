"""A camada de dados da CARTEIRA, contra Postgres de verdade (BACK 11.1).

Estas provas não cabem num teste unitário: o que está sob teste é o
comportamento do `ON CONFLICT`, do `UPDATE … NOT IN`, da subtração de datas e da
FK com `ondelete` — tudo do BANCO, não do Python. Um mock de sessão confirmaria
o SQL que escrevemos, não o que o Postgres faz com ele.

Os critérios de aceite que moram aqui, na ordem:

  - reconciliação de **dois ciclos**: atualiza o que mudou, insere o novo, marca
    quem saiu, e **nunca apaga** linha;
  - **reemissão** (identificador novo para o mesmo título do mundo real): o
    antigo vira `ausente_na_origem`, o novo entra, e os DOIS ficam na tabela;
  - `_base_query` prende o tenant: título de outro cliente não aparece em
    leitura nenhuma;
  - FK com `ondelete=CASCADE`: excluir o cliente leva a carteira junto;
  - os dois relógios de `clients`: a falha **nunca** toca o carimbo de sucesso;
  - filtros e baldes de aging aplicados no SERVIDOR, com um título plantado em
    cada balde — inclusive o de **90+ dias**, que é a diferença que justifica a
    sprint.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.models import (
    Client,
    ClientTitle,
    TitleStatus,
    TitleType,
    User,
    UserRole,
    UserScope,
)
from app.modules.client_titles.aging import AgingBucket
from app.modules.client_titles.repository import ClientTitlesRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

#: "Hoje" fixo: o aging é sobre a data do servidor, e um teste que dependesse do
#: calendário do dia em que roda mudaria de resultado sozinho.
HOJE = date(2026, 9, 24)


async def _make_client(db_session: AsyncSession, *, name: str) -> Client:
    """Um cliente com criador próprio — `clients.created_by` é RESTRICT."""
    creator = User(
        email=f"criador-{uuid4().hex[:8]}@hologram.test",
        password_hash=hash_password("Senha!Forte#123"),
        name="Criador",
        role=UserRole.ADMIN.value,
        scope=UserScope.SYSTEM.value,
        active=True,
    )
    db_session.add(creator)
    await db_session.flush()

    client = Client(name=name, created_by=creator.id)
    db_session.add(client)
    await db_session.flush()
    return client


def _row(
    external_id: str,
    *,
    due_date: date,
    amount: str = "100.00",
    title_type: TitleType = TitleType.A_RECEBER,
    status: TitleStatus = TitleStatus.EM_ABERTO,
    category_code: str | None = "2.04.94",
    supplier_code: int | None = 2624256082,
    omie_conta_id: int | None = 2617722760,
    document_number: str | None = "00123/A",
) -> dict[str, Any]:
    """Uma linha na forma que o serviço da 11.2 entrega ao repositório.

    Os códigos numéricos são os da captura REAL — os dois acima do teto de
    `INTEGER`, o que faz deste teste também a prova de que `BigInteger` era
    obrigatório.
    """
    return {
        "external_id": external_id,
        "title_type": title_type.value,
        "due_date": due_date,
        "amount": Decimal(amount),
        "status": status.value,
        "category_code": category_code,
        "supplier_code": supplier_code,
        "omie_conta_id": omie_conta_id,
        "document_number": document_number,
    }


async def _titles(db_session: AsyncSession, client_id: Any) -> list[ClientTitle]:
    """A carteira do cliente, LIDA DO BANCO — nunca do identity map.

    ⚠️ O `expire_all()` é obrigatório e não é zelo. `reconcile_cycle` escreve em
    **Core** (`INSERT ... ON CONFLICT DO UPDATE`), que não repovoa instância ORM já
    carregada: um teste que leia a mesma linha antes e depois de um ciclo recebe de
    volta o objeto ANTIGO, com o `status` da leitura anterior. Foi exatamente essa
    a armadilha da reprovação de 24/09/2026 — o banco estava certo (`em_aberto`) e o
    teste afirmava `ausente_na_origem`, o pior defeito possível num teste, porque a
    próxima pessoa "conserta" o repositório.

    Fica no helper, e não no teste, porque o helper é usado por todo o arquivo: assim
    qualquer teste futuro que leia duas vezes o mesmo título já nasce imune.

    ⚠️ E é `populate_existing`, NÃO `expire_all()`. Expirar a sessão inteira expira
    também o `Client` criado pelo teste; o próximo `client.id` dispara um refresh
    lazy, que é I/O fora do greenlet do driver async e estoura `MissingGreenlet`
    (validação humana de 24/09/2026: dois testes deste arquivo caíram assim, com o
    banco certo). `populate_existing` sobrescreve, a partir das linhas desta query,
    só as instâncias que ela devolve — exatamente o que se quer, e nada além.
    """
    rows = await db_session.execute(
        select(ClientTitle)
        .where(ClientTitle.client_id == client_id)
        .order_by(ClientTitle.external_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


class TestCicloDeSincronizacao:
    async def test_dois_ciclos_atualizam_inserem_e_marcam_sem_apagar(
        self, db_session: AsyncSession
    ) -> None:
        """O critério central da 11.1, num teste só — porque são um comportamento só.

        Ciclo 1 planta três títulos. Ciclo 2 devolve um com valor NOVO, um
        inalterado e um título NOVO; o terceiro do ciclo 1 não volta. Ao fim:
        quatro linhas, nenhuma apagada, e quem sumiu marcado.
        """
        client = await _make_client(db_session, name="Carteira Dois Ciclos")
        repo = ClientTitlesRepository(db_session)
        t0 = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)
        t1 = t0 + timedelta(days=1)

        primeiro = await repo.reconcile_cycle(
            client.id,
            [
                _row("1001", due_date=date(2026, 8, 1), amount="500.00"),
                _row("1002", due_date=date(2026, 9, 1), amount="250.00"),
                _row("1003", due_date=date(2026, 9, 15), amount="125.00"),
            ],
            synced_at=t0,
        )
        assert (primeiro.upserted, primeiro.closed) == (3, 0)

        segundo = await repo.reconcile_cycle(
            client.id,
            [
                # mudou de valor e de vencimento — tem de ser ATUALIZADO
                _row("1001", due_date=date(2026, 8, 10), amount="480.00"),
                # inalterado
                _row("1002", due_date=date(2026, 9, 1), amount="250.00"),
                # novo
                _row("1004", due_date=date(2026, 10, 5), amount="90.00"),
            ],
            synced_at=t1,
        )
        assert segundo.upserted == 3
        # só o 1003 saiu do conjunto em aberto
        assert segundo.closed == 1

        linhas = {t.external_id: t for t in await _titles(db_session, client.id)}
        # NENHUMA linha foi apagada: 3 do ciclo 1 + 1 novo do ciclo 2
        assert set(linhas) == {"1001", "1002", "1003", "1004"}

        assert linhas["1001"].amount == Decimal("480.00")
        assert linhas["1001"].due_date == date(2026, 8, 10)
        assert linhas["1001"].status == TitleStatus.EM_ABERTO.value

        assert linhas["1003"].status == TitleStatus.AUSENTE_NA_ORIGEM.value
        # quem saiu NÃO é recarimbado: `last_synced_at` diz quando a origem o
        # viu pela última vez, e ela não viu.
        assert linhas["1003"].last_synced_at == t0
        assert linhas["1004"].status == TitleStatus.EM_ABERTO.value

    async def test_ciclo_identico_e_idempotente(self, db_session: AsyncSession) -> None:
        """Duas execuções seguidas com o mesmo payload = mesmo estado.

        É o que sustenta o comando em lote da 11.2 ser retomável: rodar de novo
        depois de uma interrupção não pode duplicar nem fechar nada.
        """
        client = await _make_client(db_session, name="Carteira Idempotente")
        repo = ClientTitlesRepository(db_session)
        payload = [
            _row("2001", due_date=date(2026, 7, 1)),
            _row("2002", due_date=date(2026, 8, 1)),
        ]

        await repo.reconcile_cycle(client.id, payload, synced_at=datetime(2026, 9, 1, tzinfo=UTC))
        segundo = await repo.reconcile_cycle(
            client.id, payload, synced_at=datetime(2026, 9, 2, tzinfo=UTC)
        )

        assert segundo.closed == 0
        linhas = await _titles(db_session, client.id)
        assert [t.external_id for t in linhas] == ["2001", "2002"]
        assert {t.status for t in linhas} == {TitleStatus.EM_ABERTO.value}

    async def test_titulo_reemitido_com_outro_identificador(self, db_session: AsyncSession) -> None:
        """Reemissão: o antigo vira ausente, o novo entra, e os DOIS ficam.

        O contexto da Sprint 15 continua pendurado na linha antiga — visível
        como órfão, nunca apagado em silêncio. É a diferença entre "a plataforma
        perdeu o acordo" e "o acordo está ali, apontando para um título que a
        origem reemitiu".
        """
        client = await _make_client(db_session, name="Carteira Reemissao")
        repo = ClientTitlesRepository(db_session)

        await repo.reconcile_cycle(
            client.id,
            [_row("3001", due_date=date(2026, 5, 1), amount="1000.00")],
            synced_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        await repo.reconcile_cycle(
            client.id,
            [_row("3002", due_date=date(2026, 11, 1), amount="1000.00")],
            synced_at=datetime(2026, 9, 2, tzinfo=UTC),
        )

        linhas = {t.external_id: t for t in await _titles(db_session, client.id)}
        assert set(linhas) == {"3001", "3002"}
        assert linhas["3001"].status == TitleStatus.AUSENTE_NA_ORIGEM.value
        assert linhas["3002"].status == TitleStatus.EM_ABERTO.value

    async def test_titulo_que_reaparece_volta_a_valer(self, db_session: AsyncSession) -> None:
        """O inverso exato: quem voltou sai de `ausente_na_origem`.

        Sem isso, um título que some por uma passada (instabilidade da origem,
        filtro que oscilou) ficaria morto para sempre e a cobertura cairia sem
        que nada tivesse mudado no cadastro do cliente.
        """
        client = await _make_client(db_session, name="Carteira Reaparece")
        repo = ClientTitlesRepository(db_session)
        payload = [_row("4001", due_date=date(2026, 6, 1))]

        await repo.reconcile_cycle(client.id, payload, synced_at=datetime(2026, 9, 1, tzinfo=UTC))
        await repo.reconcile_cycle(client.id, [], synced_at=datetime(2026, 9, 2, tzinfo=UTC))
        sumido = (await _titles(db_session, client.id))[0]
        assert sumido.status == TitleStatus.AUSENTE_NA_ORIGEM.value

        await repo.reconcile_cycle(client.id, payload, synced_at=datetime(2026, 9, 3, tzinfo=UTC))
        voltou = (await _titles(db_session, client.id))[0]
        assert voltou.status == TitleStatus.EM_ABERTO.value

    async def test_liquidado_so_entra_quando_a_origem_declara(
        self, db_session: AsyncSession
    ) -> None:
        """Quem some vira `ausente_na_origem`, não `liquidado`.

        A ingestão lê só o conjunto em aberto: um título que sai dali pode ter
        sido pago, cancelado ou reemitido. Chamá-lo de liquidado seria a
        plataforma afirmando um fato que não verificou. `liquidado` só chega
        pelo upsert, quando a ORIGEM o declara.
        """
        client = await _make_client(db_session, name="Carteira Liquidado")
        repo = ClientTitlesRepository(db_session)

        await repo.reconcile_cycle(
            client.id,
            [_row("5001", due_date=date(2026, 6, 1)), _row("5002", due_date=date(2026, 6, 2))],
            synced_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        await repo.reconcile_cycle(
            client.id,
            [_row("5002", due_date=date(2026, 6, 2), status=TitleStatus.LIQUIDADO)],
            synced_at=datetime(2026, 9, 2, tzinfo=UTC),
        )

        linhas = {t.external_id: t for t in await _titles(db_session, client.id)}
        assert linhas["5001"].status == TitleStatus.AUSENTE_NA_ORIGEM.value
        assert linhas["5002"].status == TitleStatus.LIQUIDADO.value


class TestIsolamentoDeTenant:
    async def test_base_query_nunca_alcanca_titulo_de_outro_cliente(
        self, db_session: AsyncSession
    ) -> None:
        """A terceira trava do isolamento (§3.15), na camada de dados.

        Dois clientes com títulos plantados; toda leitura do repositório vê só
        os do tenant pedido. Se alguém escrever uma query nova sem passar pelo
        `_base_query`, é aqui que aparece.
        """
        alvo = await _make_client(db_session, name="Cliente Alvo")
        outro = await _make_client(db_session, name="Cliente Vizinho")
        repo = ClientTitlesRepository(db_session)
        agora = datetime(2026, 9, 1, tzinfo=UTC)

        await repo.reconcile_cycle(
            alvo.id, [_row("A1", due_date=date(2026, 8, 1))], synced_at=agora
        )
        await repo.reconcile_cycle(
            outro.id,
            [_row("B1", due_date=date(2026, 8, 1)), _row("B2", due_date=date(2026, 8, 2))],
            synced_at=agora,
        )

        linhas, total = await repo.list_for_client(alvo.id, today=HOJE, limit=50, offset=0)
        assert total == 1
        assert [t.external_id for t in linhas] == ["A1"]
        assert await repo.existing_external_ids(alvo.id) == {"A1"}
        assert await repo.count_for_client(alvo.id) == 1
        assert await repo.count_for_client(outro.id) == 2

    async def test_fechar_ciclo_de_um_cliente_nao_encosta_no_outro(
        self, db_session: AsyncSession
    ) -> None:
        """O `UPDATE … NOT IN` do fim do ciclo também é preso ao tenant."""
        alvo = await _make_client(db_session, name="Alvo Fecha")
        outro = await _make_client(db_session, name="Vizinho Fecha")
        repo = ClientTitlesRepository(db_session)
        agora = datetime(2026, 9, 1, tzinfo=UTC)

        await repo.reconcile_cycle(
            alvo.id, [_row("X1", due_date=date(2026, 8, 1))], synced_at=agora
        )
        await repo.reconcile_cycle(
            outro.id, [_row("X1", due_date=date(2026, 8, 1))], synced_at=agora
        )

        # o alvo perde o X1; o vizinho tem um título com o MESMO identificador
        await repo.reconcile_cycle(alvo.id, [], synced_at=agora)

        do_alvo = (await _titles(db_session, alvo.id))[0]
        do_vizinho = (await _titles(db_session, outro.id))[0]
        assert do_alvo.status == TitleStatus.AUSENTE_NA_ORIGEM.value
        assert do_vizinho.status == TitleStatus.EM_ABERTO.value


class TestFkEPurge:
    async def test_excluir_cliente_leva_a_carteira_junto(self, db_session: AsyncSession) -> None:
        """`ondelete=CASCADE` DECLARADO — a exclusão definitiva não deixa órfão.

        Vale como prova do critério "FK com `ondelete` declarado e teste de
        exclusão de cliente": sem o CASCADE, este `DELETE` levantaria violação
        de FK em vez de limpar.
        """
        client = await _make_client(db_session, name="Cliente Excluido")
        repo = ClientTitlesRepository(db_session)
        await repo.reconcile_cycle(
            client.id,
            [_row("9001", due_date=date(2026, 8, 1))],
            synced_at=datetime(2026, 9, 1, tzinfo=UTC),
        )
        assert await repo.count_for_client(client.id) == 1

        await db_session.execute(delete(Client).where(Client.id == client.id))
        await db_session.flush()

        assert await repo.count_for_client(client.id) == 0

    async def test_delete_for_client_e_o_unico_delete_da_carteira(
        self, db_session: AsyncSession
    ) -> None:
        """O caminho do ENCERRAMENTO (§4.12): a carteira sai, o cliente fica."""
        client = await _make_client(db_session, name="Cliente Encerrado")
        repo = ClientTitlesRepository(db_session)
        await repo.reconcile_cycle(
            client.id,
            [_row("9101", due_date=date(2026, 8, 1)), _row("9102", due_date=date(2026, 8, 2))],
            synced_at=datetime(2026, 9, 1, tzinfo=UTC),
        )

        await repo.delete_for_client(client.id)
        await db_session.flush()

        assert await repo.count_for_client(client.id) == 0
        ainda_existe = await db_session.execute(select(Client.id).where(Client.id == client.id))
        assert ainda_existe.scalar_one() == client.id


class TestOsDoisRelogios:
    async def test_carteira_nunca_sincronizada_tem_os_dois_nulos(
        self, db_session: AsyncSession
    ) -> None:
        """O baseline 0% do Outcome, no schema: nasce sem carimbo nenhum."""
        client = await _make_client(db_session, name="Nunca Sincronizou")
        repo = ClientTitlesRepository(db_session)
        assert await repo.get_sync_state(client.id) == (None, None)

    async def test_falha_nunca_toca_o_carimbo_de_sucesso(self, db_session: AsyncSession) -> None:
        """R1 no schema: "falha preserva a última carteira íntegra".

        Um relógio só escolheria entre esquecer a falha e mentir sobre o
        sucesso; com dois, a tela diz "falhou agora, e a última íntegra foi tal
        dia" — que é o que o R3 exige.
        """
        client = await _make_client(db_session, name="Sucesso E Falha")
        repo = ClientTitlesRepository(db_session)
        bom = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
        ruim = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)

        await repo.mark_sync_succeeded(client.id, at=bom)
        await repo.mark_sync_failed(client.id, at=ruim)
        assert await repo.get_sync_state(client.id) == (bom, ruim)

    async def test_sucesso_posterior_limpa_o_aviso_de_falha(self, db_session: AsyncSession) -> None:
        """ "Falhou" é sobre a ÚLTIMA tentativa — senão o aviso fica pendurado."""
        client = await _make_client(db_session, name="Falha E Sucesso")
        repo = ClientTitlesRepository(db_session)
        await repo.mark_sync_failed(client.id, at=datetime(2026, 9, 1, tzinfo=UTC))

        bom = datetime(2026, 9, 3, tzinfo=UTC)
        await repo.mark_sync_succeeded(client.id, at=bom)
        assert await repo.get_sync_state(client.id) == (bom, None)


class TestLeituraPaginadaEFiltros:
    """Títulos plantados em CADA balde — inclusive o de 90+ dias.

    Com `HOJE = 2026-09-24`, os vencimentos abaixo dão exatamente:
    10 · 45 · 75 · 120 dias de atraso, e um a vencer.
    """

    async def _carteira_com_todos_os_baldes(self, db_session: AsyncSession) -> Client:
        client = await _make_client(db_session, name="Carteira Baldes")
        repo = ClientTitlesRepository(db_session)
        await repo.reconcile_cycle(
            client.id,
            [
                _row("B0", due_date=date(2026, 10, 24), amount="10.00"),  # a vencer
                _row("B1", due_date=date(2026, 9, 14), amount="20.00"),  # 10 dias
                _row("B2", due_date=date(2026, 8, 10), amount="30.00"),  # 45 dias
                _row("B3", due_date=date(2026, 7, 11), amount="40.00"),  # 75 dias
                _row(
                    "B4",
                    due_date=date(2026, 5, 27),
                    amount="50.00",
                    title_type=TitleType.A_PAGAR,
                ),  # 120 dias
            ],
            synced_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        return client

    async def test_cada_balde_pega_exatamente_o_seu_titulo(self, db_session: AsyncSession) -> None:
        client = await self._carteira_com_todos_os_baldes(db_session)
        repo = ClientTitlesRepository(db_session)

        esperado = {
            AgingBucket.A_VENCER: ["B0"],
            AgingBucket.D1_30: ["B1"],
            AgingBucket.D31_60: ["B2"],
            AgingBucket.D61_90: ["B3"],
            AgingBucket.D90_MAIS: ["B4"],
        }
        for bucket, ids in esperado.items():
            linhas, total = await repo.list_for_client(
                client.id, today=HOJE, bucket=bucket, limit=50, offset=0
            )
            assert [t.external_id for t in linhas] == ids, bucket
            assert total == len(ids), bucket

    async def test_o_titulo_de_90_mais_e_o_que_a_conciliacao_do_mes_nao_traria(
        self, db_session: AsyncSession
    ) -> None:
        """A diferença que justifica a sprint, afirmada como fato do banco.

        `B4` vence em 27/05/2026. A leitura da conciliação recorta
        `reference_month` até o último dia do mês corrente — com o mês corrente
        em setembro, a janela é 01/09 a 30/09 e o título fica fora dela. A
        carteira o enxerga porque **não tem recorte de competência**.
        """
        client = await self._carteira_com_todos_os_baldes(db_session)
        repo = ClientTitlesRepository(db_session)

        linhas, _ = await repo.list_for_client(
            client.id, today=HOJE, bucket=AgingBucket.D90_MAIS, limit=50, offset=0
        )
        assert [t.external_id for t in linhas] == ["B4"]

        janela_da_conciliacao = (date(2026, 9, 1), date(2026, 9, 30))
        assert not (janela_da_conciliacao[0] <= linhas[0].due_date <= janela_da_conciliacao[1])

    async def test_filtro_de_situacao_separa_vencido_de_em_aberto(
        self, db_session: AsyncSession
    ) -> None:
        client = await self._carteira_com_todos_os_baldes(db_session)
        repo = ClientTitlesRepository(db_session)

        _, abertos = await repo.list_for_client(
            client.id, today=HOJE, situation="em_aberto", limit=50, offset=0
        )
        _, vencidos = await repo.list_for_client(
            client.id, today=HOJE, situation="vencido", limit=50, offset=0
        )
        assert abertos == 5
        # os quatro baldes de vencidos somam exatamente o total vencido
        assert vencidos == 4

    async def test_filtro_de_tipo_e_do_servidor(self, db_session: AsyncSession) -> None:
        client = await self._carteira_com_todos_os_baldes(db_session)
        repo = ClientTitlesRepository(db_session)

        a_pagar, total_pagar = await repo.list_for_client(
            client.id, today=HOJE, title_type=TitleType.A_PAGAR, limit=50, offset=0
        )
        assert total_pagar == 1
        assert [t.external_id for t in a_pagar] == ["B4"]

    async def test_ordenacao_e_paginacao_sao_do_servidor(self, db_session: AsyncSession) -> None:
        """O total acompanha os filtros, e a página é recortada no banco."""
        client = await self._carteira_com_todos_os_baldes(db_session)
        repo = ClientTitlesRepository(db_session)

        pagina, total = await repo.list_for_client(
            client.id, today=HOJE, sort_by="amount", descending=True, limit=2, offset=0
        )
        assert total == 5
        assert [t.external_id for t in pagina] == ["B4", "B3"]

        segunda, _ = await repo.list_for_client(
            client.id, today=HOJE, sort_by="amount", descending=True, limit=2, offset=2
        )
        assert [t.external_id for t in segunda] == ["B2", "B1"]

    async def test_valores_voltam_como_decimal_nunca_float(self, db_session: AsyncSession) -> None:
        """§3.4 ponta a ponta: `Numeric(14,2)` volta `Decimal`, não `float`."""
        client = await self._carteira_com_todos_os_baldes(db_session)
        repo = ClientTitlesRepository(db_session)
        linhas, _ = await repo.list_for_client(client.id, today=HOJE, limit=50, offset=0)

        for titulo in linhas:
            assert isinstance(titulo.amount, Decimal), titulo.external_id
            assert not isinstance(titulo.amount, float), titulo.external_id

    async def test_codigos_da_captura_real_cabem_na_coluna(self, db_session: AsyncSession) -> None:
        """`2624256082` e `2617722760` — acima do teto de INTEGER.

        Com `Integer` no lugar de `BigInteger`, este teste estoura no `INSERT`.
        """
        client = await self._carteira_com_todos_os_baldes(db_session)
        linhas = await _titles(db_session, client.id)
        assert {t.supplier_code for t in linhas} == {2624256082}
        assert {t.omie_conta_id for t in linhas} == {2617722760}
