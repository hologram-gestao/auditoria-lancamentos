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

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.exceptions import (
    OmieLancamentoAlreadyLinkedError,
    OmiePostingKeyCollisionError,
)
from app.core.logging import get_logger
from app.db.models import (
    FileEntrySituation,
    OmiePostingStatus,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
)
from app.modules.reconciliations.omie_posting.keys import derive_cod_int_lanc

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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
    ) -> ReconciliationOmiePosting:
        """Registra a intenção de lançar a linha. **Idempotente.**

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

    async def mark_failed(
        self,
        *,
        client_id: UUID,
        file_entry_id: UUID,
        error_code: str,
        error_message: str,
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
