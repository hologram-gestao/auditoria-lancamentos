"""Acesso ao DB da intenção de lançamento no Omie (BACK 07.2).

**É aqui que mora a dedup primária.** Que a Omie imponha unicidade sobre
`cCodIntLanc` é suposição NÃO-VERIFICADA (S-1 / ADR-019-BE) — a proteção que
existe hoje, e que é verificável hoje, é o estado próprio do ADL. Nenhum
caminho pode enviar um lançamento sem antes passar por `register_intent`.

**Isolamento (S5/R3).** Todo `SELECT`/`UPDATE` daqui carrega
`AND client_id = <tenant>` no próprio comando — nunca "busca por PK e confere
depois". Quem chama passa o `client_id` já autorizado pela rota
(`resolve_client_access`); aqui ele é o filtro.

Este módulo não fala HTTP nem conhece a Omie: quem chama o fornecedor é a
BACK 07.4.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.exceptions import (
    OmieLancamentoAlreadyLinkedError,
    OmiePostingKeyCollisionError,
)
from app.core.logging import get_logger
from app.db.models import (
    FileEntrySituation,
    InstallmentStep,
    OmiePostingKind,
    OmiePostingStatus,
    OmiePostingStep,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
    ReconciliationOmiePostingInstallment,
)
from app.modules.reconciliations.omie_posting.keys import (
    derive_cod_int_lanc,
    derive_installment_payable_key,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.reconciliations.omie_posting.installment_plan import PlannedInstallment

log = get_logger(__name__)


class OmiePostingRepository:
    """Operações de leitura/escrita sobre a intenção de lançamento."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    async def get_by_file_entry(
        self,
        *,
        client_id: UUID,
        file_entry_id: UUID,
    ) -> ReconciliationOmiePosting | None:
        """Intenção registrada para a linha, ou `None`.

        O `client_id` entra no `WHERE` mesmo sendo uma busca por outra chave
        única: recurso de outro tenant tem de **não ser carregado**, não ser
        carregado-e-descartado.
        """
        stmt = select(ReconciliationOmiePosting).where(
            ReconciliationOmiePosting.file_entry_id == file_entry_id,
            ReconciliationOmiePosting.client_id == client_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_by_file_entries(
        self,
        *,
        client_id: UUID,
        file_entry_ids: list[UUID],
    ) -> dict[UUID, ReconciliationOmiePosting]:
        """Intenções das linhas pedidas, indexadas por `file_entry_id`.

        Uma query para o lote inteiro — o caminho da BACK 07.4 checa dezenas de
        linhas antes de enviar qualquer coisa, e um `get_by_file_entry` por
        linha viraria N+1 no ponto mais sensível do fluxo.
        """
        if not file_entry_ids:
            return {}
        stmt = select(ReconciliationOmiePosting).where(
            ReconciliationOmiePosting.client_id == client_id,
            ReconciliationOmiePosting.file_entry_id.in_(file_entry_ids),
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return {row.file_entry_id: row for row in rows}

    # ------------------------------------------------------------------
    # Escrita
    # ------------------------------------------------------------------

    async def register_intent(
        self,
        *,
        client_id: UUID,
        session_id: UUID,
        file_entry_id: UUID,
        kind: OmiePostingKind = OmiePostingKind.DIRETO,
    ) -> ReconciliationOmiePosting:
        """Registra a intenção de lançar a linha. **Idempotente.**

        `kind` só vale na CRIAÇÃO: a 2ª chamada devolve a intenção existente como
        ela está, inclusive no outro modo — quem decide se trocar de modo é seguro
        é o serviço (`reset_kind`), nunca um INSERT que "corrige" a linha.

        A 2ª chamada para a mesma linha **não cria registro novo** — devolve o
        existente. A idempotência é do BANCO (`ON CONFLICT DO NOTHING` sobre
        `uq_recon_omie_postings_file_entry`), não de um "SELECT antes do
        INSERT": entre o SELECT e o INSERT cabe uma requisição concorrente, e
        o custo do erro aqui é um lançamento duplicado na contabilidade do
        cliente.

        Raises:
            OmiePostingKeyCollisionError: a chave derivada já pertence a OUTRA
                linha do mesmo tenant. Erro tratado — nunca silêncio.
        """
        cod_int_lanc = derive_cod_int_lanc(file_entry_id)
        stmt = (
            pg_insert(ReconciliationOmiePosting)
            .values(
                id=uuid4(),
                session_id=session_id,
                client_id=client_id,
                file_entry_id=file_entry_id,
                cod_int_lanc=cod_int_lanc,
                status=OmiePostingStatus.PENDING.value,
                attempts=0,
                kind=kind.value,
                step=(
                    OmiePostingStep.FORNECEDOR.value if kind is OmiePostingKind.PROVISAO else None
                ),
            )
            # SEM alvo de conflito: cobre as DUAS chaves da tabela.
            # Mirar só `uq_..._file_entry` deixava a colisão de `cod_int_lanc`
            # escapar como `IntegrityError` cru — 500 em vez do erro tratado
            # que esta função promete. Foi assim que o teste
            # `test_collision_surfaces_as_a_handled_error` pegou o defeito.
            .on_conflict_do_nothing()
            .returning(ReconciliationOmiePosting)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        if inserted is not None:
            return inserted

        # Conflito: a linha já tinha intenção. Devolvemos a existente — este é
        # o caminho normal de um reenvio/duplo-clique, não um erro.
        existing = await self.get_by_file_entry(client_id=client_id, file_entry_id=file_entry_id)
        if existing is not None:
            return existing

        # Sem inserção e sem linha existente para esta (linha, tenant): o único
        # jeito de chegar aqui é a chave já pertencer a outro registro — ou a
        # outro tenant (impossível: `client_id` faz parte da chave), ou a outra
        # linha deste tenant (colisão do encoding). Ver `keys.py`.
        log.warning(
            "omie_posting_cod_int_lanc_collision",
            client_id=str(client_id),
            session_id=str(session_id),
            file_entry_id=str(file_entry_id),
        )
        raise OmiePostingKeyCollisionError(
            f"cCodIntLanc {cod_int_lanc} já em uso no tenant {client_id}",
        )

    async def mark_confirmed(
        self,
        *,
        client_id: UUID,
        file_entry_id: UUID,
        omie_lancamento_id: int,
    ) -> ReconciliationOmiePosting:
        """Confirma o lançamento e reflete o resultado na linha da conciliação.

        Dois efeitos, na MESMA transação — nunca um sem o outro:
          1. a intenção vira `confirmed` com o `nCodLanc` devolvido;
          2. a `file_entry` recebe `omie_lancamento_id` e passa a `conciliado`,
             saindo das pendências.

        O passo 2 convive com o índice parcial
        `ix_recon_file_entry_session_omie_unique` (`session_id`,
        `omie_lancamento_id`), que impede duas linhas da mesma sessão de
        apontarem para o mesmo lançamento (CLAUDE.md §5.4). Se o `nCodLanc` já
        estiver vinculado a outra linha, o caminho **não** contorna o índice:
        levanta `OmieLancamentoAlreadyLinkedError`.

        Raises:
            OmieLancamentoAlreadyLinkedError: o `nCodLanc` já fecha outra linha
                da mesma sessão.
        """
        posting = await self.get_by_file_entry(client_id=client_id, file_entry_id=file_entry_id)
        if posting is None:
            raise ValueError(
                "mark_confirmed sem intenção registrada — register_intent tem de vir antes"
            )

        conflicting = await self._session.scalar(
            select(ReconciliationFileEntry.id).where(
                ReconciliationFileEntry.session_id == posting.session_id,
                ReconciliationFileEntry.omie_lancamento_id == omie_lancamento_id,
                ReconciliationFileEntry.id != file_entry_id,
            )
        )
        if conflicting is not None:
            raise OmieLancamentoAlreadyLinkedError(
                f"nCodLanc {omie_lancamento_id} já vinculado à linha {conflicting}",
            )

        posting.status = OmiePostingStatus.CONFIRMED.value
        posting.omie_lancamento_id = omie_lancamento_id
        posting.error_code = None
        posting.error_message = None

        await self._session.execute(
            update(ReconciliationFileEntry)
            .where(ReconciliationFileEntry.id == file_entry_id)
            .values(
                omie_lancamento_id=omie_lancamento_id,
                situation=FileEntrySituation.CONCILIADO.value,
            )
        )
        await self._session.flush()
        return posting

    async def reset_kind(
        self,
        *,
        client_id: UUID,
        file_entry_id: UUID,
        kind: OmiePostingKind,
    ) -> ReconciliationOmiePosting:
        """Troca o modo de uma intenção que PROVADAMENTE não escreveu dinheiro.

        Só o serviço sabe quando isso vale (`_kind_switch_allowed`): provisão
        ainda no passo `fornecedor`, ou `direto` recusado de forma definitiva na
        única tentativa. A intenção volta a `pending`, sem tentativas nem erro, no
        primeiro passo do modo novo; a chave `cod_int_lanc` fica (é da LINHA).
        """
        posting = await self._require(client_id=client_id, file_entry_id=file_entry_id)
        posting.kind = kind.value
        posting.step = (
            OmiePostingStep.FORNECEDOR.value if kind is OmiePostingKind.PROVISAO else None
        )
        posting.status = OmiePostingStatus.PENDING.value
        posting.attempts = 0
        posting.supplier_code = None
        posting.payable_id = None
        posting.payment_id = None
        posting.error_code = None
        posting.error_message = None
        await self._session.flush()
        return posting

    async def record_provision_step(
        self,
        *,
        client_id: UUID,
        file_entry_id: UUID,
        next_step: OmiePostingStep,
        supplier_code: int | None = None,
        payable_id: int | None = None,
        payment_id: int | None = None,
    ) -> ReconciliationOmiePosting:
        """Grava o id que um passo da provisão devolveu e avança para o próximo.

        Os ids ausentes não são tocados (o do passo anterior fica). `attempts`
        zera — o contador é do passo corrente — e `concluido` é `confirmed`: o
        CHECK `provisao_steps_coherent` recusa qualquer outra combinação.
        Quem chama COMMITA logo depois: o id gravado é o que impede refazer o
        passo na próxima execução.
        """
        posting = await self._require(client_id=client_id, file_entry_id=file_entry_id)
        if supplier_code is not None:
            posting.supplier_code = supplier_code
        if payable_id is not None:
            posting.payable_id = payable_id
        if payment_id is not None:
            posting.payment_id = payment_id
        posting.step = next_step.value
        posting.attempts = 0
        posting.status = (
            OmiePostingStatus.CONFIRMED.value
            if next_step is OmiePostingStep.CONCLUIDO
            else OmiePostingStatus.PENDING.value
        )
        posting.error_code = None
        posting.error_message = None
        await self._session.flush()
        return posting

    async def _require(self, *, client_id: UUID, file_entry_id: UUID) -> ReconciliationOmiePosting:
        posting = await self.get_by_file_entry(client_id=client_id, file_entry_id=file_entry_id)
        if posting is None:
            raise ValueError("intenção não registrada — register_intent tem de vir antes")
        return posting

    async def mark_failed(
        self,
        *,
        client_id: UUID,
        file_entry_id: UUID,
        error_code: str,
        error_message: str,
        reset_attempts: bool = False,
    ) -> ReconciliationOmiePosting:
        """Marca a tentativa como falha. **Nada** é marcado como lançado.

        `error_message` é a mensagem VERBATIM do provedor — ela é guardada
        porque o operador precisa vê-la inline para agir, e **nunca é logada**
        (ADR-022-BE): é texto livre de terceiro, e supor que texto livre externo
        esteja limpo de PII é exatamente o que o §3.3 do CLAUDE.md proíbe.
        """
        posting = await self.get_by_file_entry(client_id=client_id, file_entry_id=file_entry_id)
        if posting is None:
            raise ValueError(
                "mark_failed sem intenção registrada — register_intent tem de vir antes"
            )
        posting.status = OmiePostingStatus.FAILED.value
        posting.error_code = error_code
        posting.error_message = error_message
        if reset_attempts:
            # Provisão (86e3n70q0): `faultstring` é resposta DEFINITIVA de que o
            # passo não criou nada, então a contagem de envios sem confirmação
            # recomeça. O `direto` mantém o histórico de sempre (`False`).
            posting.attempts = 0
        await self._session.flush()
        return posting

    async def increment_attempts(
        self,
        *,
        client_id: UUID,
        file_entry_id: UUID,
    ) -> int:
        """Soma 1 em `attempts` e devolve o novo valor.

        Incrementado **antes** do POST: se a resposta nunca chegar, o número de
        tentativas já está gravado. Contar depois só registraria as tentativas
        que voltaram — justamente as que não interessam ao caminho de timeout.
        """
        stmt = (
            update(ReconciliationOmiePosting)
            .where(
                ReconciliationOmiePosting.file_entry_id == file_entry_id,
                ReconciliationOmiePosting.client_id == client_id,
            )
            .values(attempts=ReconciliationOmiePosting.attempts + 1)
            .returning(ReconciliationOmiePosting.attempts)
        )
        attempts = (await self._session.execute(stmt)).scalar_one_or_none()
        if attempts is None:
            raise ValueError(
                "increment_attempts sem intenção registrada — register_intent tem de vir antes"
            )
        return attempts


#: Distância máxima, em dias, entre o vencimento de uma parcela registrada e a data
#: da linha que a procura (86e3qhhtb). A identidade (cliente, cartão, fornecedor,
#: valor, `N/M`) já é quase única; a janela só separa duas séries IGUAIS que
#: começaram em meses diferentes, e cobre o dia da compra que o emissor desloca.
INSTALLMENT_MATCH_WINDOW_DAYS = 45


class InstallmentRepository:
    """As parcelas FUTURAS de compra parcelada (86e3qhhtb). `client_id` em todo WHERE."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_posting(
        self, *, client_id: UUID, posting_id: UUID
    ) -> list[ReconciliationOmiePostingInstallment]:
        """As parcelas de UMA série, em ordem de número."""
        stmt = (
            select(ReconciliationOmiePostingInstallment)
            .where(
                ReconciliationOmiePostingInstallment.client_id == client_id,
                ReconciliationOmiePostingInstallment.posting_id == posting_id,
            )
            .order_by(ReconciliationOmiePostingInstallment.installment_number)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_for_postings(
        self, *, client_id: UUID, posting_ids: list[UUID]
    ) -> dict[UUID, list[ReconciliationOmiePostingInstallment]]:
        """As parcelas de várias séries numa query (a página da revisão)."""
        if not posting_ids:
            return {}
        stmt = (
            select(ReconciliationOmiePostingInstallment)
            .where(
                ReconciliationOmiePostingInstallment.client_id == client_id,
                ReconciliationOmiePostingInstallment.posting_id.in_(posting_ids),
            )
            .order_by(ReconciliationOmiePostingInstallment.installment_number)
        )
        out: dict[UUID, list[ReconciliationOmiePostingInstallment]] = {}
        for row in (await self._session.execute(stmt)).scalars().all():
            out.setdefault(row.posting_id, []).append(row)
        return out

    async def register(
        self,
        *,
        client_id: UUID,
        posting_id: UUID,
        file_entry_id: UUID,
        omie_conta_id: int,
        supplier_code: int,
        planned: PlannedInstallment,
    ) -> ReconciliationOmiePostingInstallment:
        """Registra a parcela ANTES do envio. **Idempotente** pelo banco.

        `ON CONFLICT DO NOTHING` sobre as duas UNIQUEs: a 2ª chamada (duplo clique,
        retomada) devolve a parcela existente, que é a memória do título que talvez
        já exista no Omie.
        """
        stmt = (
            pg_insert(ReconciliationOmiePostingInstallment)
            .values(
                id=uuid4(),
                posting_id=posting_id,
                client_id=client_id,
                omie_conta_id=omie_conta_id,
                supplier_code=supplier_code,
                amount=planned.amount,
                installment_number=planned.number,
                installment_total=planned.total,
                due_date=planned.due_date,
                issue_date=planned.issue_date,
                payable_key=derive_installment_payable_key(file_entry_id, planned.number),
                step=InstallmentStep.TITULO.value,
                attempts=0,
            )
            .on_conflict_do_nothing()
            .returning(ReconciliationOmiePostingInstallment)
        )
        inserted = (await self._session.execute(stmt)).scalar_one_or_none()
        if inserted is not None:
            return inserted
        existing = (
            await self._session.execute(
                select(ReconciliationOmiePostingInstallment).where(
                    ReconciliationOmiePostingInstallment.client_id == client_id,
                    ReconciliationOmiePostingInstallment.posting_id == posting_id,
                    ReconciliationOmiePostingInstallment.installment_number == planned.number,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            # A chave derivada já é de OUTRA parcela do tenant: só uma colisão de
            # encoding chega aqui. Falhar alto, nunca seguir sem memória.
            raise OmiePostingKeyCollisionError(
                f"chave da parcela {planned.number} da linha {file_entry_id} já em uso",
            )
        return existing

    async def find_in_series(
        self,
        *,
        client_id: UUID,
        omie_conta_id: int,
        supplier_code: int,
        amount: Decimal,
        number: int,
        total: int,
        around: date,
        exclude_posting_id: UUID | None = None,
    ) -> ReconciliationOmiePostingInstallment | None:
        """A parcela registrada com a MESMA identidade, ainda não encontrada.

        Mesmo cliente, cartão, fornecedor, valor e `N/M`, com vencimento a até
        `INSTALLMENT_MATCH_WINDOW_DAYS` dias de `around`; a mais próxima vence.
        Inclui as parcelas ainda sem título (quem chama decide o que fazer com
        elas: criar outra seria o duplicado).
        """
        window = timedelta(days=INSTALLMENT_MATCH_WINDOW_DAYS)
        model = ReconciliationOmiePostingInstallment
        stmt = select(model).where(
            model.client_id == client_id,
            model.omie_conta_id == omie_conta_id,
            model.supplier_code == supplier_code,
            model.amount == amount,
            model.installment_number == number,
            model.installment_total == total,
            model.claimed_by_file_entry_id.is_(None),
            model.due_date >= around - window,
            model.due_date <= around + window,
        )
        if exclude_posting_id is not None:
            stmt = stmt.where(model.posting_id != exclude_posting_id)
        rows = list((await self._session.execute(stmt)).scalars().all())
        if not rows:
            return None
        return min(rows, key=lambda r: (abs((r.due_date - around).days), r.created_at, r.id))

    async def claim(
        self, *, client_id: UUID, installment_id: UUID, file_entry_id: UUID
    ) -> ReconciliationOmiePostingInstallment | None:
        """A linha da fatura seguinte ENCONTRA a parcela. Condicional no próprio SQL.

        Só vale para parcela com título (`concluido`) e ainda não encontrada; a
        corrida entre duas linhas é decidida pelo `WHERE` e pela UNIQUE de
        `claimed_by_file_entry_id`, nunca por uma leitura anterior. `None` = perdeu.
        """
        model = ReconciliationOmiePostingInstallment
        stmt = (
            update(model)
            .where(
                model.id == installment_id,
                model.client_id == client_id,
                model.claimed_by_file_entry_id.is_(None),
                model.step == InstallmentStep.CONCLUIDO.value,
            )
            .values(claimed_by_file_entry_id=file_entry_id)
            .returning(model)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def claimed_by(
        self, *, client_id: UUID, file_entry_id: UUID
    ) -> ReconciliationOmiePostingInstallment | None:
        """A parcela que esta linha encontrou, ou `None`."""
        stmt = select(ReconciliationOmiePostingInstallment).where(
            ReconciliationOmiePostingInstallment.client_id == client_id,
            ReconciliationOmiePostingInstallment.claimed_by_file_entry_id == file_entry_id,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def increment_attempts(self, *, client_id: UUID, installment_id: UUID) -> None:
        """Soma 1 ANTES do envio: se a resposta não vier, o envio está contado."""
        await self._session.execute(
            update(ReconciliationOmiePostingInstallment)
            .where(
                ReconciliationOmiePostingInstallment.id == installment_id,
                ReconciliationOmiePostingInstallment.client_id == client_id,
            )
            .values(attempts=ReconciliationOmiePostingInstallment.attempts + 1)
        )

    async def mark_done(self, *, client_id: UUID, installment_id: UUID, payable_id: int) -> None:
        """Título confirmado: grava o id e conclui. Quem chama COMMITA em seguida."""
        await self._session.execute(
            update(ReconciliationOmiePostingInstallment)
            .where(
                ReconciliationOmiePostingInstallment.id == installment_id,
                ReconciliationOmiePostingInstallment.client_id == client_id,
            )
            .values(
                step=InstallmentStep.CONCLUIDO.value,
                payable_id=payable_id,
                attempts=0,
                error_code=None,
                error_message=None,
            )
        )

    async def mark_failed(
        self,
        *,
        client_id: UUID,
        installment_id: UUID,
        error_code: str,
        error_message: str,
    ) -> None:
        """`faultstring`: nada foi criado; a contagem de envios recomeça. Nunca loga."""
        await self._session.execute(
            update(ReconciliationOmiePostingInstallment)
            .where(
                ReconciliationOmiePostingInstallment.id == installment_id,
                ReconciliationOmiePostingInstallment.client_id == client_id,
            )
            .values(attempts=0, error_code=error_code, error_message=error_message)
        )


async def invoice_anchor(session: AsyncSession, session_id: UUID) -> date | None:
    """A compra mais recente da conciliação: marca o fim da fatura corrente (86e3qhhtb).

    É a âncora de `installment_plan.current_installment_date`. As linhas de
    parcela com a data ORIGINAL da compra são mais antigas e não a puxam para trás.
    """
    anchor: date | None = await session.scalar(
        select(func.max(ReconciliationFileEntry.transaction_date)).where(
            ReconciliationFileEntry.session_id == session_id
        )
    )
    return anchor
