"""Camada de dados da CARTEIRA de títulos (Sprint 11, BACK 11.1).

SQL puro, uma função por query. **Toda leitura nasce de `_base_query`**, que já
carrega `AND client_id = <tenant>` — nenhuma query alcança uma linha só pela PK:
ela pertence a um tenant, e o tenant vem do `Client` já validado pela rota
(§3.15). É a terceira trava do isolamento, depois da matriz e do guard de rota.

O serviço de ingestão (11.2) e as rotas (11.5) consomem estas funções; nada aqui
decide regra de negócio nem fala com a origem.

**A lei desta camada: nunca `DELETE` de título.** Um título que sai do conjunto
em aberto vira `liquidado` ou `ausente_na_origem` — a linha FICA, porque pode
haver contexto da Sprint 15 apontando para ela. O único `DELETE` daqui é o
`delete_for_client`, que serve ao encerramento e à exclusão do cliente inteiro.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from sqlalchemy import CursorResult, Integer, Select, delete, func, literal, select, update
from sqlalchemy import Date as SQLDate
from sqlalchemy import cast as sa_cast
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.client import Client
from app.db.models.client_title import (
    UQ_CLIENT_TITLE_CLIENT_EXTERNAL_ID,
    ClientTitle,
    TitleStatus,
    TitleType,
)
from app.modules.client_titles.aging import (
    AGING_BUCKET_BOUNDS,
    OVERDUE_BUCKETS,
    AgingBucket,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

#: Linhas por comando no upsert. 12 placeholders por linha contra o teto de
#: 65.535 do protocolo do Postgres dá ~5.400; 1.000 é folga de 5x — e a carteira
#: real que motivou a sprint tem 148 títulos. Mesmo número do plano de contas
#: (S10), de propósito: dois tetos diferentes para o mesmo problema seriam duas
#: coisas para lembrar.
UPSERT_CHUNK_SIZE = 1000

#: Ordenações aceitas pela lista (11.5). Vocabulário fechado aqui para que a
#: rota não monte `ORDER BY` a partir de string do cliente.
type TitleSortField = Literal["due_date", "amount"]

#: Situação pedida pelo filtro da lista — derivada do VENCIMENTO, não do
#: `status`. "Vencido" é um título `em_aberto` cuja data já passou; não é um
#: quinto valor de `TitleStatus`, porque o mesmo título muda de vencido para
#: não-vencido só pela passagem do tempo, sem ninguém escrever nada.
type TitleSituation = Literal["em_aberto", "vencido"]


@dataclass(frozen=True, slots=True)
class TitleSyncOutcome:
    """O que um ciclo de sincronização fez — contagens, nunca linhas.

    É daqui que saem os números do evento `carteira_sincronizada` (11.3): o
    emissor não recontar nada é o que garante que a métrica e o banco não
    divirjam.
    """

    #: Quantos títulos a origem devolveu e foram gravados (inseridos OU
    #: atualizados) neste ciclo.
    upserted: int
    #: Quantos saíram do conjunto em aberto e foram marcados nesta passada.
    closed: int


@dataclass(frozen=True, slots=True)
class AgingTotals:
    """Os agregados de UM tipo de título (a pagar **ou** a receber).

    Calculados no BANCO, sobre a carteira INTEIRA. Contar em Python sobre a
    página devolveria números que mudam conforme a paginação — e a pergunta que a
    reunião faz ("quanto está vencido há mais de 90 dias?") é sobre o cliente
    todo, não sobre 20 linhas.

    **A identidade que a tela promete, e que o teste afirma:**

        total_a_vencer + total_vencido == total_em_aberto
        soma dos quatro baldes      == total_vencido

    Ela vale porque as duas metades saem da MESMA base (`status = em_aberto`) e
    porque os baldes particionam exatamente `dias >= 1` (ver `aging.py`). Contar
    "vencido" sobre uma base diferente de "em aberto" faria as parcelas não
    fecharem com a soma exibida ao lado — o mesmo defeito que a S10 evitou na
    cobertura do plano de contas.
    """

    #: Dinheiro sempre `Decimal` (§3.4). `Decimal("0.00")` na carteira vazia —
    #: nunca `None`, que forçaria todo consumidor a tratar o caso.
    total_em_aberto: Decimal
    total_a_vencer: Decimal
    total_vencido: Decimal
    #: Valor por balde de atraso. As quatro chaves de `OVERDUE_BUCKETS` estão
    #: SEMPRE presentes, com `Decimal("0.00")` onde não há título: chave ausente
    #: viraria `KeyError` na tela ou, pior, um balde silenciosamente omitido.
    baldes: Mapping[AgingBucket, Decimal]

    #: Contagens, para a tela poder dizer "12 títulos" ao lado do valor.
    qtd_em_aberto: int
    qtd_a_vencer: int
    qtd_vencido: int
    baldes_qtd: Mapping[AgingBucket, int]


@dataclass(frozen=True, slots=True)
class TitlesSummary:
    """O bloco de agregados da carteira — os dois tipos + o estado do sync.

    **Três estados, e nenhum deles é "zeros".** É o coração do R3:

    - `nunca_sincronizada=True` → a plataforma **nunca** consultou a origem deste
      cliente. A tela oferece "sincronizar"; não mostra zeros, que pareceriam
      resultado ("este cliente não deve nada").
    - `sync_failed_at` preenchido → os agregados abaixo são da última
      sincronização ÍNTEGRA (`synced_at` diz quando), e a tela avisa da falha. Os
      números continuam sendo os melhores que existem — descartá-los deixaria a
      tela vazia justamente quando o usuário precisa dela.
    - os dois nulos e `nunca_sincronizada=False` → carteira íntegra e atual.

    `nunca_sincronizada` é campo EXPLÍCITO, e não `synced_at is None` derivado no
    consumidor, exatamente para que o teste possa afirmar **o campo** em vez do
    valor `0` — e para que cada tela não reimplemente a mesma decisão.
    """

    a_pagar: AgingTotals
    a_receber: AgingTotals
    #: Data da última sincronização ÍNTEGRA. `None` = nunca houve uma.
    synced_at: datetime | None
    #: Data da última FALHA. Preenchido junto com `synced_at` significa "falhou
    #: agora, e estes números são de tal dia".
    sync_failed_at: datetime | None
    #: A data do servidor usada como referência do aging (R3). Vai na resposta
    #: para que a tela mostre os baldes sem recalcular nada com o "hoje" do
    #: navegador — dois usuários em fusos diferentes veriam baldes diferentes.
    referencia: date

    @property
    def nunca_sincronizada(self) -> bool:
        """Nunca houve sincronização íntegra deste cliente.

        Derivado de `synced_at`, num lugar só: é a MESMA pergunta que o TTL do
        plano de contas faz, e duas implementações divergiriam.
        """
        return self.synced_at is None


class ClientTitlesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------ READ ------------------------------

    def _base_query(self, client_id: UUID) -> Select[tuple[ClientTitle]]:
        """Todo `SELECT` desta tabela nasce daqui, já preso ao tenant.

        Um único lugar coloca o `WHERE client_id = …`: é a diferença entre
        esquecer o filtro numa query nova e não ter como esquecê-lo.
        """
        return select(ClientTitle).where(ClientTitle.client_id == client_id)

    async def list_for_client(
        self,
        client_id: UUID,
        *,
        today: date,
        title_type: TitleType | None = None,
        situation: TitleSituation | None = None,
        bucket: AgingBucket | None = None,
        sort_by: TitleSortField = "due_date",
        descending: bool = False,
        limit: int,
        offset: int,
    ) -> tuple[list[ClientTitle], int]:
        """Página da carteira + total que casa com os MESMOS filtros.

        Filtros e ordenação são do SERVIDOR: a tela pagina, e filtrar depois de
        paginar devolveria "3 títulos vencidos" quando existem 80 — o mesmo
        defeito que o aging calculado sobre a página teria.

        A ordenação secundária por `external_id` não é enfeite: sem um critério
        total, duas páginas seguidas podem repetir e pular linhas quando muitos
        títulos compartilham o mesmo vencimento — e vencimento repetido é a
        regra, não a exceção (parcelas).
        """
        stmt = self._base_query(client_id)
        if title_type is not None:
            stmt = stmt.where(ClientTitle.title_type == title_type.value)
        if situation is not None:
            stmt = stmt.where(*_situation_predicates(situation, today))
        if bucket is not None:
            stmt = stmt.where(*_bucket_predicates(bucket, today))

        total_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = (await self._session.execute(total_stmt)).scalar_one()

        column = ClientTitle.due_date if sort_by == "due_date" else ClientTitle.amount
        order = column.desc() if descending else column.asc()
        page_stmt = stmt.order_by(order, ClientTitle.external_id).limit(limit).offset(offset)
        rows = list((await self._session.execute(page_stmt)).scalars().all())
        return rows, total

    async def existing_external_ids(self, client_id: UUID) -> set[str]:
        """Os identificadores que o cliente já tem gravados.

        É o que permite descobrir quem SAIU do conjunto em aberto sem trazer a
        carteira inteira como objetos ORM.
        """
        stmt = select(ClientTitle.external_id).where(ClientTitle.client_id == client_id)
        return set((await self._session.execute(stmt)).scalars().all())

    async def count_for_client(self, client_id: UUID) -> int:
        """Quantas linhas o cliente tem na carteira, em qualquer situação.

        Usado pelo teste de "falha no meio preserva a carteira anterior": a
        contagem tem de ser idêntica antes e depois da tentativa fracassada.
        """
        stmt = select(func.count()).select_from(
            self._base_query(client_id).order_by(None).subquery()
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def aging(self, client_id: UUID, *, today: date) -> dict[TitleType, AgingTotals]:
        """Os agregados dos DOIS tipos, numa query de agregação, no BANCO.

        **Uma query, não duas nem N.** `GROUP BY title_type` com `FILTER` por
        balde: a tela precisa de 2 tipos x (total + a vencer + vencido + 4
        baldes) x (valor + quantidade) = 28 números, e buscá-los um a um seriam
        28 idas ao banco para responder uma pergunta só.

        **Por que agregar no banco e não em Python.** Somar em Python exigiria
        carregar a carteira inteira — que numa organização com centenas de
        clientes é a diferença entre um `SUM` e um `SELECT *`. E, pior, o número
        passaria a depender do recorte que o chamador carregou: é exatamente
        assim que nasce um aging calculado sobre a página.

        **Sem `CAST` de 64 bits aqui, de propósito.** A regra do primer ("cast
        antes de multiplicar") existe para `BIGINT`, que estoura; `SUM` sobre
        `NUMERIC` no Postgres é de precisão arbitrária e não tem teto. Forçar um
        cast só acrescentaria um lugar para perder centavos.

        `COALESCE(..., 0)` em toda soma: `SUM` sobre conjunto vazio devolve
        `NULL`, e `Decimal("0.00")` é a resposta certa para "não há título neste
        balde". Tipo ausente na tabela ganha um `AgingTotals` zerado — a tela não
        pode receber meia resposta.
        """
        aberto = ClientTitle.status == TitleStatus.EM_ABERTO.value
        vencido = ClientTitle.due_date < today
        a_vencer = ClientTitle.due_date >= today

        columns: list[Any] = [
            ClientTitle.title_type.label("title_type"),
            _sum_amount().label("total_em_aberto"),
            _sum_amount(a_vencer).label("total_a_vencer"),
            _sum_amount(vencido).label("total_vencido"),
            func.count().label("qtd_em_aberto"),
            func.count().filter(a_vencer).label("qtd_a_vencer"),
            func.count().filter(vencido).label("qtd_vencido"),
        ]
        for bucket in OVERDUE_BUCKETS:
            predicates = _bucket_predicates(bucket, today)
            columns.append(_sum_amount(*predicates).label(f"valor_{bucket.name}"))
            columns.append(func.count().filter(*predicates).label(f"qtd_{bucket.name}"))

        stmt = (
            select(*columns)
            .where(ClientTitle.client_id == client_id, aberto)
            .group_by(ClientTitle.title_type)
        )
        rows = (await self._session.execute(stmt)).all()

        by_type = {
            TitleType(row.title_type): AgingTotals(
                total_em_aberto=row.total_em_aberto,
                total_a_vencer=row.total_a_vencer,
                total_vencido=row.total_vencido,
                baldes={b: getattr(row, f"valor_{b.name}") for b in OVERDUE_BUCKETS},
                qtd_em_aberto=row.qtd_em_aberto,
                qtd_a_vencer=row.qtd_a_vencer,
                qtd_vencido=row.qtd_vencido,
                baldes_qtd={b: getattr(row, f"qtd_{b.name}") for b in OVERDUE_BUCKETS},
            )
            for row in rows
        }
        # Tipo sem nenhum título em aberto não aparece no `GROUP BY` — e a tela
        # precisa das duas colunas de qualquer forma.
        for title_type in TitleType:
            by_type.setdefault(title_type, _zeroed_totals())
        return by_type

    async def get_sync_state(self, client_id: UUID) -> tuple[datetime | None, datetime | None]:
        """`(última íntegra, última falha)` — as duas colunas de `clients`.

        Fonte ÚNICA do "nunca sincronizou" (as duas NULL) e do aviso de falha
        da tela (11.4/11.5). Não derivar de `MAX(last_synced_at)` das linhas:
        um cliente sem nenhum título em aberto deixaria o MAX em NULL e
        "carteira vazia" viraria indistinguível de "nunca sincronizou" — que é
        exatamente a distinção que o R3 exige.
        """
        stmt = select(Client.titles_synced_at, Client.titles_sync_failed_at).where(
            Client.id == client_id
        )
        row = (await self._session.execute(stmt)).one()
        return row.titles_synced_at, row.titles_sync_failed_at

    # ------------------------------ WRITE -----------------------------

    async def reconcile_cycle(
        self,
        client_id: UUID,
        rows: Sequence[dict[str, Any]],
        *,
        synced_at: datetime,
    ) -> TitleSyncOutcome:
        """Aplica UM ciclo de sincronização: atualiza, insere e marca quem saiu.

        As duas metades são inseparáveis e rodam na MESMA transação — publicar
        o que veio sem fechar o que saiu deixaria a carteira somando títulos que
        a origem já não tem, e é assim que um aging mente.

        **Reemissão sai de graça daqui.** Um título que reaparece com outro
        identificador é, para esta função, duas coisas independentes: o novo
        identificador não está na carteira (entra pelo `INSERT`) e o antigo não
        veio nesta passada (vira `ausente_na_origem`). O contexto do antigo
        continua pendurado na linha antiga, visível como órfão — nunca apagado
        em silêncio.
        """
        upserted = await self._upsert_many(client_id, rows, synced_at=synced_at)
        closed = await self.close_titles_absent_from(
            client_id,
            keep_external_ids=[str(row["external_id"]) for row in rows],
        )
        return TitleSyncOutcome(upserted=upserted, closed=closed)

    async def _upsert_many(
        self,
        client_id: UUID,
        rows: Sequence[dict[str, Any]],
        *,
        synced_at: datetime,
    ) -> int:
        """Grava o que a origem devolveu. Devolve quantas linhas entraram.

        `ON CONFLICT (client_id, external_id) DO UPDATE` — a idempotência é da
        UNIQUE do banco, não de uma leitura anterior: duas sincronizações
        simultâneas do mesmo cliente convergem em vez de estourar.

        Toda linha revista volta para a situação que a origem declarou AGORA:
        título que estava `ausente_na_origem` e reapareceu volta a valer, o
        inverso exato do que `close_titles_absent_from` faz com quem saiu.

        O lote é quebrado em `UPSERT_CHUNK_SIZE` por causa do teto de 65.535
        placeholders por comando do protocolo do Postgres. Os pedaços rodam na
        MESMA transação — quebrar o comando não quebra a atomicidade.
        """
        if not rows:
            return 0

        payload = [dict(row, client_id=client_id, last_synced_at=synced_at) for row in rows]
        for start in range(0, len(payload), UPSERT_CHUNK_SIZE):
            chunk = payload[start : start + UPSERT_CHUNK_SIZE]
            stmt = pg_insert(ClientTitle).values(chunk)
            stmt = stmt.on_conflict_do_update(
                constraint=UQ_CLIENT_TITLE_CLIENT_EXTERNAL_ID,
                set_={
                    "title_type": stmt.excluded.title_type,
                    "due_date": stmt.excluded.due_date,
                    "amount": stmt.excluded.amount,
                    "status": stmt.excluded.status,
                    "category_code": stmt.excluded.category_code,
                    "supplier_code": stmt.excluded.supplier_code,
                    "omie_conta_id": stmt.excluded.omie_conta_id,
                    "document_number": stmt.excluded.document_number,
                    "last_synced_at": stmt.excluded.last_synced_at,
                    "updated_at": func.now(),
                },
            )
            await self._session.execute(stmt)
        return len(payload)

    async def close_titles_absent_from(
        self,
        client_id: UUID,
        *,
        keep_external_ids: Sequence[str],
        status: TitleStatus = TitleStatus.AUSENTE_NA_ORIGEM,
    ) -> int:
        """Fecha quem não veio nesta sincronização. **Nunca apaga.**

        O default é `ausente_na_origem` e não `liquidado` de propósito: a
        ingestão da 11.2 lê **só o conjunto em aberto**, então um título que
        some dali pode ter sido pago, cancelado ou reemitido — e chamar de
        "liquidado" um título que ninguém pagou seria a plataforma afirmando um
        fato que não verificou. `liquidado` só é gravado quando a **origem** o
        declara, e aí entra pelo caminho normal do upsert (o `status` vem do
        DTO), não por inferência daqui.

        `last_synced_at` NÃO é tocado: ele diz quando a linha foi vista pela
        origem pela última vez, e ela não foi.
        """
        stmt = (
            update(ClientTitle)
            .where(
                ClientTitle.client_id == client_id,
                ClientTitle.status == TitleStatus.EM_ABERTO.value,
            )
            .values(status=status.value, updated_at=func.now())
        )
        if keep_external_ids:
            stmt = stmt.where(ClientTitle.external_id.not_in(list(keep_external_ids)))
        result = await self._session.execute(stmt)
        # UPDATE devolve CursorResult (com rowcount); o narrow é para o mypy —
        # o caminho else não existe em runtime.
        if not isinstance(result, CursorResult):  # pragma: no cover
            return 0
        return int(result.rowcount or 0)

    async def mark_sync_succeeded(self, client_id: UUID, *, at: datetime) -> None:
        """Carimba a sincronização ÍNTEGRA e **limpa** a marca de falha.

        Limpar é parte do contrato da tela (R3): "falhou" é sobre a ÚLTIMA
        tentativa. Um sucesso depois de uma falha deixaria o aviso pendurado
        para sempre se a coluna só acumulasse.
        """
        await self._session.execute(
            update(Client)
            .where(Client.id == client_id)
            .values(titles_synced_at=at, titles_sync_failed_at=None)
        )

    async def mark_sync_failed(self, client_id: UUID, *, at: datetime) -> None:
        """Carimba a falha. **Nunca** toca a coluna do último sucesso.

        É o que sustenta "falha preserva a última carteira íntegra" (R1) no
        schema, e não só no fluxo do serviço.
        """
        await self._session.execute(
            update(Client).where(Client.id == client_id).values(titles_sync_failed_at=at)
        )

    async def delete_for_client(self, client_id: UUID) -> None:
        """Purga a carteira do cliente (encerramento, §4.12).

        Nada aqui é cifrado — não há PII —, então nada morre junto com a DEK.
        Some mesmo assim, seguindo o precedente explícito do plano de contas
        (S10): a carteira é **operacional**, e cliente encerrado não opera. A
        retenção que o encerramento protege é a das conciliações e da trilha,
        não a de um espelho de títulos que a origem pode reconstruir.

        Este é o ÚNICO `DELETE` da carteira. A lei "título não se apaga" é sobre
        o ciclo de sincronização; encerrar o cliente é outra coisa.
        """
        await self._session.execute(delete(ClientTitle).where(ClientTitle.client_id == client_id))


# --------------------------- PREDICADOS ------------------------------------
#
# Fora da classe e num lugar só: a agregação dos baldes (11.4) aplica EXATAMENTE
# os mesmos predicados que o filtro da lista. Dois `BETWEEN` escritos à parte
# fariam a soma dos baldes deixar de bater com o que a lista mostra, e ninguém
# veria.


#: O zero do dinheiro, com a escala da coluna. Constante para que "sem título"
#: devolva sempre o MESMO valor — `Decimal(0)` e `Decimal("0.00")` são iguais na
#: comparação, mas serializam diferente, e a tela veria "0" num lugar e "0,00" no
#: outro.
ZERO_MONEY = Decimal("0.00")


def _sum_amount(*predicates: ColumnElement[bool]) -> ColumnElement[Decimal]:
    """`COALESCE(SUM(amount) FILTER (WHERE …), 0)` — a soma de dinheiro do balde.

    `COALESCE` porque `SUM` sobre conjunto vazio é `NULL`, e a resposta certa para
    "não há título neste balde" é zero, não ausência. Sem predicado, soma tudo do
    grupo.
    """
    total: ColumnElement[Decimal] = func.sum(ClientTitle.amount)
    if predicates:
        total = total.filter(*predicates)
    return func.coalesce(total, ZERO_MONEY)


def _zeroed_totals() -> AgingTotals:
    """Agregados de um tipo que não tem nenhum título em aberto.

    Tudo zero e **todas** as quatro chaves de balde presentes: a tela não pode
    receber meia resposta, e um balde ausente viraria `KeyError` ou, pior, uma
    coluna silenciosamente omitida.
    """
    return AgingTotals(
        total_em_aberto=ZERO_MONEY,
        total_a_vencer=ZERO_MONEY,
        total_vencido=ZERO_MONEY,
        baldes=dict.fromkeys(OVERDUE_BUCKETS, ZERO_MONEY),
        qtd_em_aberto=0,
        qtd_a_vencer=0,
        qtd_vencido=0,
        baldes_qtd=dict.fromkeys(OVERDUE_BUCKETS, 0),
    )


def _days_overdue(today: date) -> ColumnElement[int]:
    """`hoje - vencimento`, em dias, como expressão SQL.

    Subtração de `DATE` no Postgres já devolve inteiro de dias; o `CAST` é
    no-op em SQL e existe para o tipo ser declarado de um lado só — sem ele o
    SQLAlchemy tipa a expressão como `date`, e uma comparação com inteiro
    passaria despercebida pelo mypy.

    `literal(today, Date)` em vez de `CURRENT_DATE`: o "hoje" é o do SERVIDOR
    (R3 — quem chama resolve), e como parâmetro o teste pode plantar títulos em
    cada balde sem depender do calendário do dia em que roda. O tipo explícito
    no literal evita que o Postgres tenha de adivinhar o tipo do parâmetro
    dentro da subtração.
    """
    return sa_cast(literal(today, SQLDate) - ClientTitle.due_date, Integer)


def _situation_predicates(situation: TitleSituation, today: date) -> list[ColumnElement[bool]]:
    """Traduz o filtro de situação da tela para `WHERE`.

    As duas opções são sobre o conjunto EM ABERTO: `em_aberto` é tudo que ainda
    está na carteira viva, `vencido` é o subconjunto cuja data já passou.
    Título `liquidado`/`ausente_na_origem` não é nenhum dos dois — ele saiu, e
    a lista da carteira não o mistura com o que ainda se cobra.
    """
    aberto: ColumnElement[bool] = ClientTitle.status == TitleStatus.EM_ABERTO.value
    if situation == "em_aberto":
        return [aberto]
    return [aberto, ClientTitle.due_date < today]


def _bucket_predicates(bucket: AgingBucket, today: date) -> list[ColumnElement[bool]]:
    """Traduz um balde de aging para `WHERE`, sobre o conjunto em aberto.

    `a_vencer` é o complemento (`dias <= 0`); os quatro baldes de vencidos vêm
    de `AGING_BUCKET_BOUNDS`, que é a fonte única dos limites.
    """
    aberto: ColumnElement[bool] = ClientTitle.status == TitleStatus.EM_ABERTO.value
    days = _days_overdue(today)
    if bucket is AgingBucket.A_VENCER:
        return [aberto, days <= 0]

    low, high = AGING_BUCKET_BOUNDS[bucket]
    predicates: list[ColumnElement[bool]] = [aberto, days >= low]
    if high is not None:
        predicates.append(days <= high)
    return predicates
