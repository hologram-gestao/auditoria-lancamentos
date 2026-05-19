"""Lógica de domínio da Tela de Revisão (S11 BACK 9.1, 9.3-9.9).

Aqui:
    - Descriptografia em memória (CLAUDE.md §4).
    - Combinação de cache L2 (lançamentos Omie) + DB (linhas + anomalias).
    - Validação de regras: "trocar Omie" (idempotência + unicidade dentro
      da sessão), criação manual de anomaly (XOR de related entry, tipo
      ativo), resolução com nota mínima (Doc §17.3).

Princípios:
    - Service NÃO chama `db.commit` — quem fecha é o `DbSessionDep`. Mas
      faz `flush` para garantir que UPDATEs/INSERTs sejam visíveis nas
      subsequentes consultas dentro da MESMA request.
    - RBAC é responsabilidade do caller (rota usa `require_client_access`).
    - Nunca loga plaintext de description/note/context/resolution_note.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from app.core.crypto import decrypt, encrypt
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.logging import get_logger
from app.db.models import (
    AnomalyDetectedBy,
    FileEntrySituation,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmieEntry,
    ReconciliationSession,
)
from app.modules.reconciliations.review.repository import ReviewRepository
from app.modules.reconciliations.review.schemas import (
    AnomalyItem,
    AnomalyRelatedFileEntry,
    AnomalyRelatedOmieEntry,
    AnomalyTypeRef,
    AvailableOmieEntry,
    CreateAnomalyRequest,
    ListedFileEntry,
    OmieEntryItem,
    ResolveAnomalyRequest,
    UpdateFileEntryRequest,
    UpdateOmieEntryRequest,
)
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from app.integrations.omie.client import OmieClient
    from app.integrations.omie.lancamento_cache import (
        OmieLancamentoCache,
    )

logger = get_logger(__name__)

# Mínimo de chars para resolution_note quando `resolved=true` (Doc §17.3).
_RESOLUTION_NOTE_MIN_LENGTH = 10

# Mensagem PT-BR única para conflito de vínculo Omie — reusada pelo caminho
# aplicativo (checagem otimista) e pelo caminho do IntegrityError (race),
# garantindo UX idêntica em ambos.
_OMIE_ID_TAKEN_USER_MSG = (
    "Este lançamento Omie já está vinculado a outra movimentação. Escolha outro."
)


class ReviewService:
    """Coordena revisão da sessão — leitura, mutação e cache."""

    def __init__(
        self,
        repository: ReviewRepository,
        *,
        cache: OmieLancamentoCache,
        encryption_key: SecretStr,
    ) -> None:
        self._repo = repository
        self._cache = cache
        self._hex_key = encryption_key.get_secret_value()

    # ------------------------------------------------------------------
    # BACK 9.1 — Listar movimentações com filtros + paginação Python
    # ------------------------------------------------------------------

    async def list_file_entries(
        self,
        *,
        session_id: UUID,
        situation: str | None,
        type_filter: str | None,
        search: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ListedFileEntry], PaginationMeta]:
        """Lista entries com filtros + paginação Python.

        Por que paginar em Python (e não SQL): o filtro `search` precisa
        rodar APÓS decrypt da descrição — o resultado paginado depende do
        conjunto pós-filtro. Sessões raramente passam de centenas de linhas
        (Doc §12.2 limite 20MB → ~500 entries típicos), então é seguro.

        Descrição e nota descriptografadas com IV próprio por linha. Linhas
        com payload corrompido aparecem com `description='[indecifrável]'`
        e nota em branco — falha graciosa em vez de derrubar a página.
        """
        rows = await self._repo.list_file_entries_all(
            session_id=session_id,
            situation=situation,
            type_filter=type_filter,
        )

        # Decrypt em memória
        decrypted: list[ListedFileEntry] = []
        for row in rows:
            description = self._decrypt_optional(row.description_encrypted, row.description_iv)
            user_note = self._decrypt_pair(row.user_note_encrypted, row.user_note_iv)
            decrypted.append(
                ListedFileEntry(
                    id=row.id,
                    transaction_date=row.transaction_date,
                    description=description or "",
                    amount=row.amount,
                    balance=row.balance,
                    situation=row.situation,
                    user_action=row.user_action,
                    user_note=user_note,
                    omie_lancamento_id=row.omie_lancamento_id,
                )
            )

        # Filtro `search` PÓS-decrypt
        if search:
            needle = search.strip().lower()
            if needle:
                decrypted = [
                    item for item in decrypted if needle in (item.description or "").lower()
                ]

        # Paginação em Python (estável, ordenada pelo repo)
        total = len(decrypted)
        start = (page - 1) * page_size
        page_items = decrypted[start : start + page_size]
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        pagination = PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )
        return page_items, pagination

    # ------------------------------------------------------------------
    # BACK 9.3 — Atualizar ação em linha do arquivo
    # ------------------------------------------------------------------

    async def update_file_entry(
        self,
        *,
        session_id: UUID,
        entry_id: UUID,
        body: UpdateFileEntryRequest,
        omie_lancamento_provided: bool,
    ) -> ListedFileEntry:
        """Aplica PATCH parcial e recalcula contadores.

        Args:
            omie_lancamento_provided: True se o front EXPLICITAMENTE enviou
                a chave `omie_lancamento_id` no body (independente do valor —
                pode ser `null` para limpar). Pydantic não distingue
                "omitido" de "nulo explícito" — caller passa essa flag.
        """
        entry = await self._repo.get_file_entry(session_id=session_id, entry_id=entry_id)
        if entry is None:
            raise NotFoundError("Linha não encontrada nesta sessão de conciliação.")

        # Trocar Omie
        if omie_lancamento_provided:
            new_id = body.omie_lancamento_id
            if new_id is None:
                # Limpa vínculo → situation volta para sem_omie (a menos que
                # o caller queira marcar 'ignorado' explicitamente).
                entry.omie_lancamento_id = None
                if body.situation is None:
                    entry.situation = FileEntrySituation.SEM_OMIE.value
            else:
                # Idempotente: se a linha JÁ tem esse mesmo ID, no-op no FK,
                # mas continuamos avaliando outros campos.
                if entry.omie_lancamento_id != new_id:
                    taken = await self._repo.file_entry_omie_id_taken_by_another(
                        session_id=session_id,
                        omie_lancamento_id=new_id,
                        exclude_entry_id=entry_id,
                    )
                    if taken:
                        raise ValidationAppError(
                            f"Omie ID {new_id} já está vinculado a outra linha desta sessão.",
                            user_message=_OMIE_ID_TAKEN_USER_MSG,
                        )
                    entry.omie_lancamento_id = new_id
                # Se o caller não decidiu uma situation explícita, marca como
                # conciliado.
                if body.situation is None:
                    entry.situation = FileEntrySituation.CONCILIADO.value

        if body.situation is not None:
            entry.situation = body.situation
        if body.user_action is not None:
            entry.user_action = body.user_action

        # `user_note`: None semanticamente "não tocar" (PATCH parcial).
        # Para limpar nota, o front envia string vazia.
        if body.user_note is not None:
            if body.user_note == "":
                entry.user_note_encrypted = None
                entry.user_note_iv = None
            else:
                ct, iv = encrypt(body.user_note, self._hex_key)
                entry.user_note_encrypted = ct
                entry.user_note_iv = iv

        # Guarda contra race em "Trocar Omie": a checagem aplicativa acima
        # (`file_entry_omie_id_taken_by_another`) cobre o caso comum em 1
        # round trip, mas 2 requests concorrentes podem ambos passar pela
        # checagem antes do flush. O índice único parcial
        # `ix_recon_file_entry_session_omie_unique` (CLAUDE.md §5.4) detecta
        # a colisão no banco; aqui convertemos o IntegrityError na MESMA
        # ValidationAppError para que o caminho de erro fique idêntico,
        # com ou sem race.
        try:
            await self._repo.flush()
        except IntegrityError as exc:
            if (
                omie_lancamento_provided
                and body.omie_lancamento_id is not None
                and "ix_recon_file_entry_session_omie_unique" in str(exc.orig)
            ):
                raise ValidationAppError(
                    f"Omie ID {body.omie_lancamento_id} já está vinculado a outra "
                    "linha desta sessão (race).",
                    user_message=_OMIE_ID_TAKEN_USER_MSG,
                ) from exc
            raise

        # Recalcula contadores se mexemos em situation
        if body.situation is not None or omie_lancamento_provided:
            await self._repo.recompute_file_entry_counters(session_id)
            await self._repo.flush()

        logger.info(
            "review_file_entry_updated",
            session_id=str(session_id),
            entry_id=str(entry_id),
            situation=entry.situation,
            user_action=entry.user_action,
            omie_lancamento_changed=omie_lancamento_provided,
        )

        return self._file_entry_to_listed(entry)

    # ------------------------------------------------------------------
    # BACK 9.4 — Lançamentos disponíveis para "Trocar"
    # ------------------------------------------------------------------

    async def list_available_omie_entries(
        self,
        *,
        session: ReconciliationSession,
        omie_client: OmieClient,
        search: str | None,
    ) -> list[AvailableOmieEntry]:
        """Busca extrato Omie + remove IDs já vinculados na sessão.

        Popula o cache L2 — chamadas subsequentes a /omie/lancamentos
        reaproveitam.

        Período usado:
            - Se `session.period_start` E `period_end` estão preenchidos,
              usa o período REAL do statement (cobre extratos quebrados
              tipo 15/04→14/05, faturas de cartão e lançamentos nos
              primeiros dias do mês seguinte).
            - Fallback `[reference_month, last_day_of_month]` para sessões
              criadas antes da migration `4a2f9e8b1c3d` — mesma lógica
              do worker em `processing/omie_fetch.fetch_pending`.
        """
        if session.period_start is not None and session.period_end is not None:
            period_start, period_end = session.period_start, session.period_end
        else:
            period_start, period_end = _month_bounds(session.reference_month)
        expanded_start, expanded_end = self._repo.expand_period(
            period_start, period_end, session.date_tolerance_days
        )

        populated = await self._cache.populate_from_extrato(
            client_id=session.client_id,
            omie_client=omie_client,
            omie_conta_id=session.omie_conta_id,
            period_start=expanded_start,
            period_end=expanded_end,
        )

        in_use = await self._repo.list_session_omie_ids_in_use(session_id=session.id)

        candidates = [data for oid, data in populated.items() if oid not in in_use]

        if search:
            needle = search.strip().lower()
            if needle:
                candidates = [
                    item
                    for item in candidates
                    if needle in (item.description or "").lower()
                    or needle in (item.supplier or "").lower()
                ]

        # Ordenação estável: data asc, omie_id asc
        candidates.sort(key=lambda d: (d.transaction_date, d.omie_id))

        return [
            AvailableOmieEntry(
                omie_id=item.omie_id,
                transaction_date=item.transaction_date,
                description=item.description,
                supplier=item.supplier,
                category=item.category,
                amount=item.amount,
                status=item.status,
            )
            for item in candidates
        ]

    # ------------------------------------------------------------------
    # BACK 9.5 — Listar divergências Omie
    # ------------------------------------------------------------------

    async def list_omie_entries(
        self,
        *,
        session: ReconciliationSession,
        page: int,
        page_size: int,
    ) -> tuple[list[OmieEntryItem], PaginationMeta]:
        """Lista omie_entries paginados, enriquecidos com cache L2.

        Lookup-only — não chama Omie aqui. Cache pode ter expirado ou nunca
        sido populado (1ª visita à aba): nesses casos, `supplier/category/
        amount` virão `None` e a UI mostra placeholder. Para forçar
        repopulação, o front chama /available-omie-entries (que popula).
        """
        rows, total = await self._repo.list_omie_entries_paginated(
            session_id=session.id,
            page=page,
            page_size=page_size,
        )

        omie_ids = [row.omie_lancamento_id for row in rows]
        cached = await self._cache.get_many(
            client_id=session.client_id,
            omie_ids=omie_ids,
        )

        items: list[OmieEntryItem] = []
        for row in rows:
            data = cached.get(row.omie_lancamento_id)
            user_note = self._decrypt_pair(row.user_note_encrypted, row.user_note_iv)
            items.append(
                OmieEntryItem(
                    id=row.id,
                    omie_lancamento_id=row.omie_lancamento_id,
                    transaction_date=row.transaction_date,
                    omie_status=row.omie_status,
                    supplier=data.supplier if data is not None else None,
                    category=data.category if data is not None else None,
                    amount=data.amount if data is not None else None,
                    user_action=row.user_action,
                    user_note=user_note,
                )
            )

        total_pages = (total + page_size - 1) // page_size if page_size else 0
        pagination = PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )
        return items, pagination

    # ------------------------------------------------------------------
    # BACK 9.6 — Atualizar omie_entry
    # ------------------------------------------------------------------

    async def update_omie_entry(
        self,
        *,
        session: ReconciliationSession,
        entry_id: UUID,
        body: UpdateOmieEntryRequest,
    ) -> OmieEntryItem:
        entry = await self._repo.get_omie_entry(session_id=session.id, entry_id=entry_id)
        if entry is None:
            raise NotFoundError("Divergência Omie não encontrada nesta sessão.")

        if body.user_action is not None:
            entry.user_action = body.user_action
        if body.user_note is not None:
            if body.user_note == "":
                entry.user_note_encrypted = None
                entry.user_note_iv = None
            else:
                ct, iv = encrypt(body.user_note, self._hex_key)
                entry.user_note_encrypted = ct
                entry.user_note_iv = iv

        await self._repo.flush()

        logger.info(
            "review_omie_entry_updated",
            session_id=str(session.id),
            entry_id=str(entry_id),
            user_action=entry.user_action,
        )

        # Lookup cache pra enriquecer a resposta
        cached = await self._cache.get_many(
            client_id=session.client_id,
            omie_ids=[entry.omie_lancamento_id],
        )
        data = cached.get(entry.omie_lancamento_id)

        return OmieEntryItem(
            id=entry.id,
            omie_lancamento_id=entry.omie_lancamento_id,
            transaction_date=entry.transaction_date,
            omie_status=entry.omie_status,
            supplier=data.supplier if data is not None else None,
            category=data.category if data is not None else None,
            amount=data.amount if data is not None else None,
            user_action=entry.user_action,
            user_note=self._decrypt_pair(entry.user_note_encrypted, entry.user_note_iv),
        )

    # ------------------------------------------------------------------
    # BACK 9.7 — Listar anomalias
    # ------------------------------------------------------------------

    async def list_anomalies(
        self,
        *,
        session_id: UUID,
        resolved_filter: bool | None,
        severity_filter: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AnomalyItem], PaginationMeta]:
        rows, total = await self._repo.list_anomalies_paginated(
            session_id=session_id,
            resolved_filter=resolved_filter,
            severity_filter=severity_filter,
            page=page,
            page_size=page_size,
        )

        file_entry_ids = [a.file_entry_id for a, _ in rows if a.file_entry_id is not None]
        omie_entry_ids = [a.omie_entry_id for a, _ in rows if a.omie_entry_id is not None]
        file_entries = await self._repo.get_file_entries_by_ids(file_entry_ids)
        omie_entries = await self._repo.get_omie_entries_by_ids(omie_entry_ids)

        items = [
            self._anomaly_to_item(
                anomaly=anomaly,
                anomaly_type_row=atype,
                file_entries=file_entries,
                omie_entries=omie_entries,
            )
            for anomaly, atype in rows
        ]
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        pagination = PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )
        return items, pagination

    # ------------------------------------------------------------------
    # BACK 9.8 — Criar anomalia manual
    # ------------------------------------------------------------------

    async def create_anomaly(
        self,
        *,
        session_id: UUID,
        body: CreateAnomalyRequest,
    ) -> AnomalyItem:
        anomaly_type = await self._repo.get_active_anomaly_type(body.anomaly_type_id)
        if anomaly_type is None:
            raise ValidationAppError(
                f"AnomalyType {body.anomaly_type_id} não existe ou está inativo.",
                user_message=("Tipo de anomalia inválido. Atualize a página e selecione outro."),
            )

        if body.file_entry_id is not None:
            ok = await self._repo.file_entry_belongs_to_session(
                session_id=session_id,
                entry_id=body.file_entry_id,
            )
            if not ok:
                raise ValidationAppError(
                    "Linha referenciada não pertence a esta sessão.",
                    user_message=(
                        "A linha indicada não existe nesta conciliação. Atualize a página."
                    ),
                )
        if body.omie_entry_id is not None:
            ok = await self._repo.omie_entry_belongs_to_session(
                session_id=session_id,
                entry_id=body.omie_entry_id,
            )
            if not ok:
                raise ValidationAppError(
                    "Divergência referenciada não pertence a esta sessão.",
                    user_message=(
                        "O lançamento Omie indicado não existe nesta conciliação. "
                        "Atualize a página."
                    ),
                )

        context_ct: str | None = None
        context_iv: str | None = None
        if body.context:
            context_ct, context_iv = encrypt(body.context, self._hex_key)

        anomaly = ReconciliationAnomaly(
            session_id=session_id,
            anomaly_type_id=anomaly_type.id,
            file_entry_id=body.file_entry_id,
            omie_entry_id=body.omie_entry_id,
            detected_by=AnomalyDetectedBy.MANUAL.value,
            context_encrypted=context_ct,
            context_iv=context_iv,
            resolved=False,
        )
        await self._repo.add_anomaly(anomaly)
        await self._repo.recompute_anomaly_count(session_id)
        await self._repo.flush()

        logger.info(
            "review_anomaly_created",
            session_id=str(session_id),
            anomaly_id=str(anomaly.id),
            anomaly_type_id=str(anomaly_type.id),
            severity=anomaly_type.severity,
            redacted_context=bool(context_ct),
        )

        # Reusa o builder para shape consistente com 9.7
        file_entries = (
            await self._repo.get_file_entries_by_ids([body.file_entry_id])
            if body.file_entry_id
            else {}
        )
        omie_entries = (
            await self._repo.get_omie_entries_by_ids([body.omie_entry_id])
            if body.omie_entry_id
            else {}
        )
        return self._anomaly_to_item(
            anomaly=anomaly,
            anomaly_type_row=anomaly_type,
            file_entries=file_entries,
            omie_entries=omie_entries,
        )

    # ------------------------------------------------------------------
    # BACK 9.9 — Resolver anomalia
    # ------------------------------------------------------------------

    async def resolve_anomaly(
        self,
        *,
        session_id: UUID,
        anomaly_id: UUID,
        body: ResolveAnomalyRequest,
    ) -> AnomalyItem:
        pair = await self._repo.get_anomaly(session_id=session_id, anomaly_id=anomaly_id)
        if pair is None:
            raise NotFoundError("Anomalia não encontrada nesta sessão.")
        anomaly, anomaly_type = pair

        if body.resolved:
            note = (body.resolution_note or "").strip()
            if len(note) < _RESOLUTION_NOTE_MIN_LENGTH:
                raise ValidationAppError(
                    "Resolution note exige ao menos 10 caracteres.",
                    user_message=(
                        "Para marcar como resolvida, descreva a resolução com "
                        "ao menos 10 caracteres."
                    ),
                )
            ct, iv = encrypt(note, self._hex_key)
            anomaly.resolution_note_encrypted = ct
            anomaly.resolution_note_iv = iv
            anomaly.resolved = True
        else:
            # Desfazer resolução — limpa nota.
            anomaly.resolved = False
            anomaly.resolution_note_encrypted = None
            anomaly.resolution_note_iv = None

        await self._repo.flush()
        await self._repo.recompute_anomaly_count(session_id)
        await self._repo.flush()

        logger.info(
            "review_anomaly_resolved",
            session_id=str(session_id),
            anomaly_id=str(anomaly_id),
            resolved=anomaly.resolved,
        )

        file_entries = (
            await self._repo.get_file_entries_by_ids([anomaly.file_entry_id])
            if anomaly.file_entry_id
            else {}
        )
        omie_entries = (
            await self._repo.get_omie_entries_by_ids([anomaly.omie_entry_id])
            if anomaly.omie_entry_id
            else {}
        )
        return self._anomaly_to_item(
            anomaly=anomaly,
            anomaly_type_row=anomaly_type,
            file_entries=file_entries,
            omie_entries=omie_entries,
        )

    # ------------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------------

    def _decrypt_optional(self, ct: str | None, iv: str | None) -> str:
        """Descriptografa um campo obrigatório (description). Falha silenciosa
        retorna placeholder para não derrubar a página."""
        if not ct or not iv:
            return ""
        try:
            return decrypt(ct, iv, self._hex_key)
        except Exception:
            logger.warning("review_decrypt_failed", field="description")
            return "[indecifrável]"

    def _decrypt_pair(self, ct: str | None, iv: str | None) -> str | None:
        """Descriptografa pair opcional. None passa direto. Erro vira None."""
        if ct is None or iv is None:
            return None
        try:
            return decrypt(ct, iv, self._hex_key)
        except Exception:
            logger.warning("review_decrypt_failed", field="user_note_or_context")
            return None

    def _file_entry_to_listed(self, entry: ReconciliationFileEntry) -> ListedFileEntry:
        return ListedFileEntry(
            id=entry.id,
            transaction_date=entry.transaction_date,
            description=self._decrypt_optional(entry.description_encrypted, entry.description_iv),
            amount=entry.amount,
            balance=entry.balance,
            situation=entry.situation,
            user_action=entry.user_action,
            user_note=self._decrypt_pair(entry.user_note_encrypted, entry.user_note_iv),
            omie_lancamento_id=entry.omie_lancamento_id,
        )

    def _anomaly_to_item(
        self,
        *,
        anomaly: ReconciliationAnomaly,
        anomaly_type_row: object,
        file_entries: dict[UUID, ReconciliationFileEntry],
        omie_entries: dict[UUID, ReconciliationOmieEntry],
    ) -> AnomalyItem:
        related_file: AnomalyRelatedFileEntry | None = None
        if anomaly.file_entry_id is not None and (fe := file_entries.get(anomaly.file_entry_id)):
            related_file = AnomalyRelatedFileEntry(
                id=fe.id,
                transaction_date=fe.transaction_date,
                description=self._decrypt_optional(fe.description_encrypted, fe.description_iv),
                amount=fe.amount,
            )
        related_omie: AnomalyRelatedOmieEntry | None = None
        if anomaly.omie_entry_id is not None and (oe := omie_entries.get(anomaly.omie_entry_id)):
            related_omie = AnomalyRelatedOmieEntry(
                id=oe.id,
                transaction_date=oe.transaction_date,
                omie_lancamento_id=oe.omie_lancamento_id,
            )

        # `anomaly_type_row` é o ORM `AnomalyType` — extrai atributos.
        atype = anomaly_type_row
        return AnomalyItem(
            id=anomaly.id,
            anomaly_type=AnomalyTypeRef(
                id=atype.id,  # type: ignore[attr-defined]
                code=atype.code,  # type: ignore[attr-defined]
                name=atype.name,  # type: ignore[attr-defined]
                severity=atype.severity,  # type: ignore[attr-defined]
            ),
            detected_by=anomaly.detected_by,
            resolved=anomaly.resolved,
            context=self._decrypt_pair(anomaly.context_encrypted, anomaly.context_iv),
            resolution_note=self._decrypt_pair(
                anomaly.resolution_note_encrypted, anomaly.resolution_note_iv
            ),
            created_at=anomaly.created_at,
            related_file_entry=related_file,
            related_omie_entry=related_omie,
        )


def _month_bounds(reference_month: date) -> tuple[date, date]:
    """1º dia → (1º dia, último dia do mês). Mesma lógica do worker."""
    from calendar import monthrange

    last_day = monthrange(reference_month.year, reference_month.month)[1]
    return reference_month, reference_month.replace(day=last_day)
