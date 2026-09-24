"""Os agregados e o aging, calculados em SQL sobre a carteira INTEIRA (BACK 11.4).

Um título plantado em **cada** um dos quatro baldes, mais um a vencer, mais um
liquidado e mais um de outro tenant. Com isso dá para afirmar tudo que o R3 pede
de uma vez:

  - cada balde pega exatamente o seu título, por tipo;
  - os quatro baldes somam **exatamente** o total vencido (asserção, não
    comentário);
  - a vencer + vencido == total em aberto;
  - título que saiu do aberto (`liquidado`/`ausente_na_origem`) **não** entra em
    soma nenhuma;
  - título de outro cliente nunca entra na soma.

O aging é contra a data corrente do servidor; o teste passa um "hoje" fixo para
não depender do calendário do dia em que roda — o parâmetro existe para isso, e
não para a rota aceitar data do cliente.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from app.core.security import hash_password
from app.db.models import Client, TitleStatus, TitleType, User, UserRole, UserScope
from app.modules.client_titles.aging import OVERDUE_BUCKETS, AgingBucket
from app.modules.client_titles.repository import ClientTitlesRepository
from app.modules.client_titles.service import ClientTitlesReadService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

#: "Hoje" fixo. Os vencimentos abaixo dão exatamente 10, 45, 75 e 120 dias de
#: atraso contra ele — um por balde.
HOJE = date(2026, 9, 24)

VENCIMENTOS = {
    AgingBucket.A_VENCER: date(2026, 10, 24),
    AgingBucket.D1_30: date(2026, 9, 14),
    AgingBucket.D31_60: date(2026, 8, 10),
    AgingBucket.D61_90: date(2026, 7, 11),
    AgingBucket.D90_MAIS: date(2026, 5, 27),
}


async def _make_client(db: AsyncSession, *, name: str) -> Client:
    creator = User(
        email=f"aging-{uuid4().hex[:8]}@hologram.test",
        password_hash=hash_password("Senha!Forte#123"),
        name="Criador",
        role=UserRole.ADMIN.value,
        scope=UserScope.SYSTEM.value,
        active=True,
    )
    db.add(creator)
    await db.flush()
    client = Client(name=name, created_by=creator.id)
    db.add(client)
    await db.flush()
    return client


def _row(
    external_id: str,
    *,
    due_date: date,
    amount: str,
    title_type: TitleType,
    status: TitleStatus = TitleStatus.EM_ABERTO,
) -> dict[str, Any]:
    return {
        "external_id": external_id,
        "title_type": title_type.value,
        "due_date": due_date,
        "amount": Decimal(amount),
        "status": status.value,
        "category_code": "2.04.94",
        "supplier_code": 2624256082,
        "omie_conta_id": 2617722760,
        "document_number": None,
    }


async def _seed_carteira(db: AsyncSession, client: Client) -> None:
    """Um título por balde em CADA tipo, com valores distintos por construção.

    Os valores são potências de 10 diferentes por balde e por tipo: se o SQL
    trocar dois baldes de lugar ou misturar os tipos, a soma muda de ordem de
    grandeza em vez de coincidir por acaso.
    """
    rows: list[dict[str, Any]] = []
    valores_receber = {
        AgingBucket.A_VENCER: "1000.00",
        AgingBucket.D1_30: "200.00",
        AgingBucket.D31_60: "30.00",
        AgingBucket.D61_90: "4.00",
        AgingBucket.D90_MAIS: "50000.00",
    }
    valores_pagar = {
        AgingBucket.A_VENCER: "7.00",
        AgingBucket.D1_30: "60.00",
        AgingBucket.D31_60: "500.00",
        AgingBucket.D61_90: "4000.00",
        AgingBucket.D90_MAIS: "300000.00",
    }
    for bucket, vencimento in VENCIMENTOS.items():
        rows.append(
            _row(
                f"R-{bucket.value}",
                due_date=vencimento,
                amount=valores_receber[bucket],
                title_type=TitleType.A_RECEBER,
            )
        )
        rows.append(
            _row(
                f"P-{bucket.value}",
                due_date=vencimento,
                amount=valores_pagar[bucket],
                title_type=TitleType.A_PAGAR,
            )
        )

    # Ruído que NÃO pode entrar em soma nenhuma: um liquidado e um ausente, os
    # dois com valor absurdo para a contaminação ser óbvia se acontecer.
    rows.append(
        _row(
            "R-LIQUIDADO",
            due_date=date(2026, 1, 1),
            amount="999999.99",
            title_type=TitleType.A_RECEBER,
            status=TitleStatus.LIQUIDADO,
        )
    )
    rows.append(
        _row(
            "P-AUSENTE",
            due_date=date(2026, 1, 1),
            amount="888888.88",
            title_type=TitleType.A_PAGAR,
            status=TitleStatus.AUSENTE_NA_ORIGEM,
        )
    )

    await ClientTitlesRepository(db).reconcile_cycle(
        client.id, rows, synced_at=datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    )


class TestBaldesEmSQL:
    async def test_cada_balde_recebe_exatamente_o_seu_titulo(
        self, db_session: AsyncSession
    ) -> None:
        client = await _make_client(db_session, name="Aging completo")
        await _seed_carteira(db_session, client)

        aging = await ClientTitlesRepository(db_session).aging(client.id, today=HOJE)
        receber = aging[TitleType.A_RECEBER]

        assert receber.baldes[AgingBucket.D1_30] == Decimal("200.00")
        assert receber.baldes[AgingBucket.D31_60] == Decimal("30.00")
        assert receber.baldes[AgingBucket.D61_90] == Decimal("4.00")
        assert receber.baldes[AgingBucket.D90_MAIS] == Decimal("50000.00")
        assert all(q == 1 for q in receber.baldes_qtd.values())

    async def test_os_dois_tipos_nao_se_misturam(self, db_session: AsyncSession) -> None:
        """R3: agregados "separadamente para a pagar e a receber"."""
        client = await _make_client(db_session, name="Aging dois tipos")
        await _seed_carteira(db_session, client)

        aging = await ClientTitlesRepository(db_session).aging(client.id, today=HOJE)

        assert aging[TitleType.A_PAGAR].baldes[AgingBucket.D90_MAIS] == Decimal("300000.00")
        assert aging[TitleType.A_RECEBER].baldes[AgingBucket.D90_MAIS] == Decimal("50000.00")

    async def test_os_quatro_baldes_somam_exatamente_o_total_vencido(
        self, db_session: AsyncSession
    ) -> None:
        """A asserção que o critério pede, sobre o resultado real do SQL.

        É aqui que um `>=` virado `>` numa fronteira aparece: a soma dos baldes
        deixa de fechar com o total vencido, mesmo que cada parcela pareça
        plausível sozinha.
        """
        client = await _make_client(db_session, name="Aging soma")
        await _seed_carteira(db_session, client)

        aging = await ClientTitlesRepository(db_session).aging(client.id, today=HOJE)
        for tipo, totals in aging.items():
            soma = sum(totals.baldes.values(), start=Decimal("0.00"))
            assert soma == totals.total_vencido, tipo
            assert sum(totals.baldes_qtd.values()) == totals.qtd_vencido, tipo

    async def test_a_vencer_mais_vencido_da_o_total_em_aberto(
        self, db_session: AsyncSession
    ) -> None:
        client = await _make_client(db_session, name="Aging identidade")
        await _seed_carteira(db_session, client)

        aging = await ClientTitlesRepository(db_session).aging(client.id, today=HOJE)
        for tipo, totals in aging.items():
            assert totals.total_a_vencer + totals.total_vencido == totals.total_em_aberto, tipo
            assert totals.qtd_a_vencer + totals.qtd_vencido == totals.qtd_em_aberto, tipo

    async def test_titulo_a_vencer_fica_fora_dos_baldes(self, db_session: AsyncSession) -> None:
        """Ainda não venceu: conta no total em aberto e em balde nenhum."""
        client = await _make_client(db_session, name="Aging a vencer")
        await _seed_carteira(db_session, client)

        receber = (await ClientTitlesRepository(db_session).aging(client.id, today=HOJE))[
            TitleType.A_RECEBER
        ]
        assert receber.total_a_vencer == Decimal("1000.00")
        assert receber.qtd_a_vencer == 1
        assert Decimal("1000.00") not in receber.baldes.values()

    async def test_titulo_que_saiu_do_aberto_nao_entra_em_soma_nenhuma(
        self, db_session: AsyncSession
    ) -> None:
        """`liquidado` e `ausente_na_origem` ficam na tabela, fora do agregado.

        A linha permanece (a lei "título não se apaga"), mas a carteira é o
        espelho do que está EM ABERTO — somar o que já saiu inflaria o aging com
        cobranças que não existem mais.
        """
        client = await _make_client(db_session, name="Aging ruido")
        await _seed_carteira(db_session, client)
        repo = ClientTitlesRepository(db_session)

        aging = await repo.aging(client.id, today=HOJE)
        for totals in aging.values():
            assert Decimal("999999.99") not in totals.baldes.values()
            assert totals.total_em_aberto < Decimal("888888.88")
        # e as linhas continuam lá
        assert await repo.count_for_client(client.id) == 12

    async def test_fronteiras_de_30_60_e_90_dias(self, db_session: AsyncSession) -> None:
        """Os dias exatos de virada, contra o BANCO.

        `30/31` e `90/91` são onde a subtração de datas do Postgres e os limites
        do `aging.py` podem discordar — e discordariam em silêncio, porque os dois
        lados continuariam devolvendo números plausíveis.
        """
        client = await _make_client(db_session, name="Aging fronteiras")
        rows = [
            _row(
                "d30",
                due_date=HOJE - timedelta(days=30),
                amount="1.00",
                title_type=TitleType.A_RECEBER,
            ),
            _row(
                "d31",
                due_date=HOJE - timedelta(days=31),
                amount="2.00",
                title_type=TitleType.A_RECEBER,
            ),
            _row(
                "d90",
                due_date=HOJE - timedelta(days=90),
                amount="4.00",
                title_type=TitleType.A_RECEBER,
            ),
            _row(
                "d91",
                due_date=HOJE - timedelta(days=91),
                amount="8.00",
                title_type=TitleType.A_RECEBER,
            ),
            _row("d0", due_date=HOJE, amount="16.00", title_type=TitleType.A_RECEBER),
        ]
        await ClientTitlesRepository(db_session).reconcile_cycle(
            client.id, rows, synced_at=datetime(2026, 9, 24, tzinfo=UTC)
        )

        receber = (await ClientTitlesRepository(db_session).aging(client.id, today=HOJE))[
            TitleType.A_RECEBER
        ]
        assert receber.baldes[AgingBucket.D1_30] == Decimal("1.00")
        assert receber.baldes[AgingBucket.D31_60] == Decimal("2.00")
        assert receber.baldes[AgingBucket.D61_90] == Decimal("4.00")
        assert receber.baldes[AgingBucket.D90_MAIS] == Decimal("8.00")
        # vencer HOJE ainda não é atraso
        assert receber.total_a_vencer == Decimal("16.00")


class TestIsolamentoNaSoma:
    async def test_agregado_de_outro_tenant_nunca_entra(self, db_session: AsyncSession) -> None:
        """Dois clientes com dados cruzados — o `WHERE client_id` do agregado.

        O `_base_query` protege as LEITURAS; a agregação é uma query própria, e é
        por isso que ela precisa do seu próprio teste negativo.
        """
        alvo = await _make_client(db_session, name="Alvo soma")
        vizinho = await _make_client(db_session, name="Vizinho soma")
        repo = ClientTitlesRepository(db_session)

        await repo.reconcile_cycle(
            alvo.id,
            [_row("A1", due_date=date(2026, 8, 1), amount="10.00", title_type=TitleType.A_RECEBER)],
            synced_at=datetime(2026, 9, 24, tzinfo=UTC),
        )
        await repo.reconcile_cycle(
            vizinho.id,
            [
                _row(
                    "B1",
                    due_date=date(2026, 8, 1),
                    amount="999999.99",
                    title_type=TitleType.A_RECEBER,
                )
            ],
            synced_at=datetime(2026, 9, 24, tzinfo=UTC),
        )

        aging = await repo.aging(alvo.id, today=HOJE)
        assert aging[TitleType.A_RECEBER].total_em_aberto == Decimal("10.00")
        assert aging[TitleType.A_RECEBER].qtd_em_aberto == 1


class TestTiposDoResultado:
    async def test_todo_valor_volta_como_decimal(self, db_session: AsyncSession) -> None:
        """§3.4: nenhum `float` em ponto nenhum do caminho, nem vindo do SQL."""
        client = await _make_client(db_session, name="Aging tipos")
        await _seed_carteira(db_session, client)

        aging = await ClientTitlesRepository(db_session).aging(client.id, today=HOJE)
        for totals in aging.values():
            for valor in (
                totals.total_em_aberto,
                totals.total_a_vencer,
                totals.total_vencido,
                *totals.baldes.values(),
            ):
                assert isinstance(valor, Decimal)
                assert not isinstance(valor, float)

    async def test_tipo_sem_titulo_volta_zerado_e_completo(self, db_session: AsyncSession) -> None:
        """O `GROUP BY` não devolve linha para um tipo sem título — a tela precisa.

        Sem o preenchimento, `aging[TitleType.A_PAGAR]` levantaria `KeyError` no
        cliente que só tem a receber, que é o caso do escritório parceiro.
        """
        client = await _make_client(db_session, name="So a receber")
        await ClientTitlesRepository(db_session).reconcile_cycle(
            client.id,
            [_row("R1", due_date=date(2026, 8, 1), amount="10.00", title_type=TitleType.A_RECEBER)],
            synced_at=datetime(2026, 9, 24, tzinfo=UTC),
        )

        aging = await ClientTitlesRepository(db_session).aging(client.id, today=HOJE)
        pagar = aging[TitleType.A_PAGAR]
        assert pagar.total_em_aberto == Decimal("0.00")
        assert set(pagar.baldes) == set(OVERDUE_BUCKETS)

    async def test_carteira_sem_nenhuma_linha_volta_os_dois_tipos_zerados(
        self, db_session: AsyncSession
    ) -> None:
        client = await _make_client(db_session, name="Carteira vazia")
        aging = await ClientTitlesRepository(db_session).aging(client.id, today=HOJE)
        assert set(aging) == set(TitleType)
        for totals in aging.values():
            assert totals.total_em_aberto == Decimal("0.00")
            assert totals.qtd_em_aberto == 0


class TestOsTresEstadosNoServico:
    """O R3 ponta a ponta: o serviço junta agregados + os dois relógios."""

    async def test_nunca_sincronizada(self, db_session: AsyncSession) -> None:
        client = await _make_client(db_session, name="Nunca sync")
        summary = await ClientTitlesReadService(ClientTitlesRepository(db_session)).summary(
            client, today=HOJE
        )
        assert summary.nunca_sincronizada is True
        assert summary.synced_at is None
        assert summary.sync_failed_at is None

    async def test_sincronizada_com_dados(self, db_session: AsyncSession) -> None:
        client = await _make_client(db_session, name="Sync ok")
        repo = ClientTitlesRepository(db_session)
        await _seed_carteira(db_session, client)
        carimbo = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
        await repo.mark_sync_succeeded(client.id, at=carimbo)

        summary = await ClientTitlesReadService(repo).summary(client, today=HOJE)
        assert summary.nunca_sincronizada is False
        assert summary.synced_at == carimbo
        assert summary.sync_failed_at is None
        assert summary.referencia == HOJE
        assert summary.a_receber.baldes[AgingBucket.D90_MAIS] == Decimal("50000.00")

    async def test_ultima_falhou_serve_os_agregados_da_integra_com_aviso(
        self, db_session: AsyncSession
    ) -> None:
        """R3: os números continuam sendo os melhores que existem, com a data deles."""
        client = await _make_client(db_session, name="Sync falhou")
        repo = ClientTitlesRepository(db_session)
        await _seed_carteira(db_session, client)
        integra = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
        falha = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
        await repo.mark_sync_succeeded(client.id, at=integra)
        await repo.mark_sync_failed(client.id, at=falha)

        summary = await ClientTitlesReadService(repo).summary(client, today=HOJE)
        assert summary.synced_at == integra
        assert summary.sync_failed_at == falha
        # os agregados da última íntegra continuam lá — não foram zerados
        assert summary.a_receber.total_vencido > Decimal("0.00")

    async def test_a_leitura_nunca_carimba_falha(self, db_session: AsyncSession) -> None:
        """Rota de LEITURA não pode inventar um aviso de sincronização falhada."""
        client = await _make_client(db_session, name="Leitura limpa")
        repo = ClientTitlesRepository(db_session)
        await ClientTitlesReadService(repo).summary(client, today=HOJE)
        assert await repo.get_sync_state(client.id) == (None, None)
