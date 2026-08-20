"""Lógica de negócio do módulo de conciliações.

S8 (BACK 6.2): verificação de duplicata pré-criação de sessão.
S10 (BACK 8.1 + 8.6): criação atômica da sessão + entries (criptografando
descrições) e leitura do status para o polling.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from app.core.crypto_service import (
    AAD_FILE_ENTRY_DESCRIPTION,
    AAD_FILE_NAME,
    field_locator,
    load_client_cipher,
)
from app.core.exceptions import ConflictError, DuplicateFileError, NotFoundError
from app.core.logging import get_logger
from app.core.search_index import compute_search_hmac
from app.db.models import (
    FileEntrySituation,
    OmieAccountType,
    ReconciliationFile,
    ReconciliationFileEntry,
    ReconciliationFileStatus,
    ReconciliationSession,
    ReconciliationStatus,
    SessionAccountType,
)

if TYPE_CHECKING:
    from app.core.config import Settings
    from app.core.crypto import ClientCipher
from app.modules.reconciliations.repository import ReconciliationRepository
from app.modules.reconciliations.schemas import (
    CreateReconciliationRequest,
    ReconciliationFileInput,
    SessionDetailPayload,
    SessionFileItem,
    SessionFilesPayload,
    SessionStatusPayload,
)

logger = get_logger(__name__)

_SESSION_NOT_FOUND_MSG = "Sessão de conciliação não encontrada."
_DUPLICATE_FILE_USER_MSG = (
    "Este arquivo já faz parte desta conciliação. Envie apenas as partes novas."
)


def session_account_type_from_omie_tipo(omie_tipo: str | None) -> str:
    """Mapeia o `tipo` Omie da conta selecionada → `account_type` da sessão.

    Regra:
        - `CR` (Cartão de Crédito) → `credit_card` (Risco #1 da FASE 1,
          validado com dado real da Austral em 18/06).
        - `CA` (Conta Aplicação) → `investment` (mini-fase conta aplicação,
          27/06): a aplicação inverte entrada/saída vs conta corrente e o
          resgate precisa do valor líquido — a qualificação e a extração
          ramificam nisso.
        - Qualquer outro tipo — incluindo `None` (conta não cacheada) → `checking`.

    ⚠️ NUNCA mapear `CA` para cartão: era exatamente o bug M-1 (auditoria
    20/05/2026) — `CA` é investimento, não cartão.
    """
    if omie_tipo == OmieAccountType.CREDIT_CARD.value:  # "CR"
        return SessionAccountType.CREDIT_CARD.value
    if omie_tipo == OmieAccountType.INVESTMENT.value:  # "CA"
        return SessionAccountType.INVESTMENT.value
    return SessionAccountType.CHECKING.value


class ReconciliationService:
    """Operações de domínio sobre conciliações."""

    def __init__(self, repository: ReconciliationRepository, *, settings: Settings) -> None:
        self._repo = repository
        # 86e2u513f: o detalhe precisa do envelope cripto (encargos do cartão
        # são identificados pela DESCRIÇÃO, que é cifrada) — mesma razão do
        # ReviewService carregar settings.
        self._settings = settings

    # ------------------------------------------------------------------
    # BACK 6.2 — verificação de duplicata
    # ------------------------------------------------------------------

    async def check_duplicate(
        self,
        *,
        client_id: UUID,
        omie_conta_id: int,
        reference_month: date,
        file_hash: str,
    ) -> bool:
        """True se a conciliação de (cliente, conta, mês) já contém este arquivo.

        Sprint 4: a pergunta mudou de nível junto com o hash — antes era "existe
        sessão com esta tupla de 4 colunas?", agora é "esta parte já está nesta
        conciliação?". A assinatura é a mesma; o significado, não.

        O caller (route) é responsável pelo RBAC sobre `client_id` antes de
        chamar esta função; aqui é apenas uma consulta sem efeitos colaterais.
        Loga apenas o prefixo de 8 chars do hash — o valor completo é PII de
        higiene (não permite identificar o conteúdo do arquivo, mas evita
        deixar correlação fácil entre logs).
        """
        duplicate = await self._repo.exists_session_with_idempotency_key(
            client_id=client_id,
            omie_conta_id=omie_conta_id,
            reference_month=reference_month,
            file_hash=file_hash,
        )
        logger.info(
            "reconciliation_check_duplicate",
            client_id=str(client_id),
            omie_conta_id=omie_conta_id,
            month=reference_month.isoformat(),
            hash_prefix=file_hash[:8],
            duplicate=duplicate,
        )
        return duplicate

    async def find_parse_duplicate(
        self,
        *,
        client_id: UUID,
        file_hash: str,
    ) -> tuple[UUID, datetime] | None:
        """BACK 02.6 — sessão ativa que já importou este conteúdo, ou None.

        Usada pelo `POST /parse` para barrar a duplicata ANTES de chamar a IA
        (custo). Sessões em `error` não contam (reimportar é permitido). RBAC
        sobre `client_id` é responsabilidade do caller (route valida acesso).
        Retorna `(session_id, created_at)` da mais recente.
        """
        row = await self._repo.find_active_session_by_hash(client_id=client_id, file_hash=file_hash)
        if row is None:
            return None
        session_id, created_at, _status = row
        return session_id, created_at

    # ------------------------------------------------------------------
    # BACK 8.1 — criação atômica da sessão
    # ------------------------------------------------------------------

    def _build_part(
        self,
        file_input: ReconciliationFileInput,
        *,
        cipher: ClientCipher,
        hex_blind_key: str,
    ) -> tuple[ReconciliationFile, list[ReconciliationFileEntry]]:
        """Monta uma parte (linha de `reconciliation_files`) + suas entries.

        Ponto ÚNICO onde uma parte vira objetos de banco — criar e anexar
        passam por aqui, então não existe caminho em que o nome fique em claro
        ou o blind index deixe de ser gravado num deles.
        """
        file_id = uuid4()
        filename_ct: str | None = None
        filename_iv: str | None = None
        if file_input.filename:
            # Nome do arquivo é texto livre de gente e costuma trazer razão
            # social — cifrado com AAD próprio (CLAUDE.md §4.5).
            filename_ct, filename_iv = cipher.encrypt(
                file_input.filename, field_locator(AAD_FILE_NAME, file_id)
            )

        parsed_ok = file_input.statement is not None
        file_obj = ReconciliationFile(
            id=file_id,
            file_hash=file_input.file_hash,
            filename_encrypted=filename_ct,
            filename_iv=filename_iv,
            status=(
                ReconciliationFileStatus.PARSED.value
                if parsed_ok
                else ReconciliationFileStatus.ERROR.value
            ),
            error_code=file_input.error_code,
        )

        entries: list[ReconciliationFileEntry] = []
        if file_input.statement is not None:
            for tx in file_input.statement.transactions:
                # `id` gerado ANTES de cifrar — compõe o AAD (pk) que amarra o
                # ciphertext a ESTA linha (default `uuid4` só valeria no flush).
                entry_id = uuid4()
                ct, iv = cipher.encrypt(
                    tx.description, field_locator(AAD_FILE_ENTRY_DESCRIPTION, entry_id)
                )
                # Blind index (S16) — gravado em paralelo à descrição
                # criptografada. Pode ser None para descrições só com
                # pontuação/whitespace ou tokens curtos — nesses casos a linha
                # fica fora do filtro `search`, mesmo comportamento que sessões
                # pré-S16 (ver migration b6f1c4d29e57).
                search_hmac = compute_search_hmac(tx.description, hex_blind_key)
                entries.append(
                    ReconciliationFileEntry(
                        id=entry_id,
                        transaction_date=tx.date,
                        description_encrypted=ct,
                        description_iv=iv,
                        description_search_hmac=search_hmac,
                        amount=tx.amount,
                        balance=tx.balance,
                        situation=FileEntrySituation.SEM_OMIE.value,
                    )
                )
        return file_obj, entries

    async def create_session_with_entries(
        self,
        *,
        request: CreateReconciliationRequest,
        created_by: UUID,
        cipher: ClientCipher,
        search_blind_index_key: SecretStr,
    ) -> tuple[UUID, int]:
        """Cria a conciliação `status='processing'` com **N partes** e suas linhas.

        BACK 04.2 — uma conciliação é *uma conta + um mês*, com N arquivos
        consolidados num só resumo:

        - a UNIQUE da sessão é `(client_id, omie_conta_id, reference_month)`;
          já existir uma conciliação ativa para a conta+mês é **conflito com
          caminho de saída**: 409 dizendo para ANEXAR o arquivo à existente
          (`POST /{id}/files`), não um beco sem saída;
        - a duplicata de ARQUIVO é `(session_id, file_hash)` → `DuplicateFileError`.

        Ambas as violações também são checadas antes do INSERT, mas a corrida
        entre dois requests só o banco resolve — daí o tratamento de
        `IntegrityError`.

        Args:
            request: payload validado do front (partes + meta da conciliação).
            created_by: UUID do usuário autenticado (vem da dependency).
            cipher: `ClientCipher` do cliente (DEK) — cifra `description` e
                `filename` no envelope corrente + AAD (client_id‖tabela‖coluna‖pk).
                Construído no route com `provision_client_cipher` (mesma regra
                que `omie_factory`: cripto no boundary async, service recebe pronto).
            search_blind_index_key: `SEARCH_BLIND_INDEX_KEY` em SecretStr. Usada
                para computar o índice de busca paralelo
                (`description_search_hmac`) que viabiliza filtro `search` em SQL
                na Tela de Revisão (S16).

        Returns:
            `(session_id, total_files)` — o caller usa o id pra agendar o job e
            a contagem pro evento `conciliacao_criada` / resposta.
        """
        hex_blind_key = search_blind_index_key.get_secret_value()
        files = request.files
        # A janela de datas da sessão cobre TODAS as partes: uma fatura
        # quebrada em 3 PDFs tem período = do início da 1ª ao fim da última.
        # Pegar só o período do 1º arquivo estreitaria a janela Omie e faria
        # linhas legítimas caírem em `sem_omie`.
        statements = [f.statement for f in files if f.statement is not None]
        period_start = min(s.period_start for s in statements)
        period_end = max(s.period_end for s in statements)

        # account_type vem do `tipo` Omie da conta SELECIONADA (cache L1),
        # não do palpite da IA no statement (CLAUDE.md §3.8 — não confiar no
        # client). Conta não cacheada → None → default 'checking'.
        omie_tipo = await self._repo.get_cached_account_type(
            client_id=request.client_id,
            omie_conta_id=request.omie_conta_id,
        )
        account_type = session_account_type_from_omie_tipo(omie_tipo)

        existing = await self._repo.find_active_session_for_account_month(
            client_id=request.client_id,
            omie_conta_id=request.omie_conta_id,
            reference_month=request.reference_month,
        )
        if existing is not None:
            raise self._account_month_conflict(existing)

        session_obj = ReconciliationSession(
            client_id=request.client_id,
            created_by=created_by,
            omie_conta_id=request.omie_conta_id,
            account_type=account_type,
            reference_month=request.reference_month,
            # Período REAL das partes — essencial para a Tela de Revisão
            # consultar /available-omie-entries com o intervalo correto
            # (extratos quebrados, faturas de cartão, atrasos).
            period_start=period_start,
            period_end=period_end,
            # FASE 1: tolerância de data agora é fixa no matcher
            # (DATE_DIVERGENCE_RANGE). Novas sessões gravam 0; a coluna é
            # mantida só por histórico (sessões antigas guardam o valor antigo).
            date_tolerance_days=0,
            # Sprint 4: o hash mora em `reconciliation_files`, por arquivo.
            file_hash=None,
            status=ReconciliationStatus.PROCESSING.value,
        )

        parts = [self._build_part(f, cipher=cipher, hex_blind_key=hex_blind_key) for f in files]
        total_entries = sum(len(entries) for _, entries in parts)

        try:
            await self._repo.add_session_with_entries(session_obj, [])
            await self._repo.add_files_with_entries(session_obj.id, parts)
        except IntegrityError as exc:
            # CLAUDE.md §5.8: UNIQUE violation = duplicata.
            detail = str(exc.orig)
            if "uq_recon_sessions_account_month" in detail:
                # Perdeu a corrida com outro request criando a mesma conta+mês.
                raise self._account_month_conflict(None) from exc
            if "uq_recon_files_session_hash" in detail:
                raise DuplicateFileError(
                    f"Arquivo duplicado na sessão (client_id={request.client_id}, "
                    f"conta={request.omie_conta_id}, mes={request.reference_month})",
                    user_message=_DUPLICATE_FILE_USER_MSG,
                ) from exc
            # Outras violações de UNIQUE/FK/etc: relança — vira 500 INTERNAL.
            raise

        logger.info(
            "reconciliation_session_created",
            session_id=str(session_obj.id),
            client_id=str(request.client_id),
            account_type=account_type,
            total_files=len(parts),
            total_file_entries=total_entries,
            month=request.reference_month.isoformat(),
        )
        return session_obj.id, len(parts)

    # ------------------------------------------------------------------
    # BACK 04.2 — anexar / remover partes de uma conciliação existente
    # ------------------------------------------------------------------

    async def attach_files(
        self,
        *,
        session_id: UUID,
        files: list[ReconciliationFileInput],
        cipher: ClientCipher,
        search_blind_index_key: SecretStr,
    ) -> tuple[int, bool]:
        """Anexa partes a uma conciliação existente e re-consolida (cenário S-3).

        "Criei com a parte 1 e a parte 2 chegou no dia seguinte." Sem este
        caminho a nova unicidade (uma conciliação por conta+mês) seria um beco
        sem saída.

        Regras:
            - `processing` → 409: o job está escrevendo na sessão agora.
            - `done` → 409: conciliação fechada não recebe parte nova.
            - parte já presente (mesmo hash) → 409 `DUPLICATE_FILE`, **sem
              bloquear as partes novas do mesmo envio** (a request é atômica:
              ou entra tudo, ou nada — o usuário remove a repetida e reenvia).

        Re-consolidação: as linhas novas entram na MESMA sessão e o estado de
        matching é zerado (`reset_session_for_reprocess`), para o cruzamento
        Omie rodar **uma vez** sobre o conjunto inteiro — nunca por arquivo.
        `user_action`/`user_note` do analista são preservados pelo reset.

        Returns:
            `(total_files_da_sessão, precisa_reprocessar)`. O caller agenda o
            job quando `precisa_reprocessar` é True (só quando entrou alguma
            parte com linhas — anexar só um registro de falha não muda o
            cruzamento e não vale um reprocessamento inteiro).
        """
        session_obj = await self._repo.get_status_view(session_id)
        if session_obj is None:
            raise NotFoundError(_SESSION_NOT_FOUND_MSG)
        self._assert_accepts_new_files(session_obj.status)

        duplicates = await self._repo.existing_file_hashes(session_id, [f.file_hash for f in files])
        if duplicates:
            raise DuplicateFileError(
                f"{len(duplicates)} arquivo(s) já presente(s) na sessão {session_id}.",
                user_message=_DUPLICATE_FILE_USER_MSG,
            )

        hex_blind_key = search_blind_index_key.get_secret_value()
        parts = [self._build_part(f, cipher=cipher, hex_blind_key=hex_blind_key) for f in files]
        await self._repo.add_files_with_entries(session_id, parts)

        needs_reprocess = any(entries for _, entries in parts)
        if needs_reprocess:
            await self._repo.reset_session_for_reprocess(session_id)

        total_files = await self._repo.count_files(session_id)
        logger.info(
            "reconciliation_files_attached",
            session_id=str(session_id),
            attached=len(parts),
            total_files=total_files,
            reprocess=needs_reprocess,
        )
        return total_files, needs_reprocess

    async def remove_file(self, *, session_id: UUID, file_id: UUID) -> tuple[int, bool]:
        """Remove uma parte da conciliação e re-consolida o que sobrou.

        É o "permite removê-lo sem corromper a sessão" do critério de aceite: as
        linhas da parte removida saem pelo `ON DELETE CASCADE` (vínculo
        `file_entries.file_id`) e as das outras partes ficam intactas.

        Recusa (409) remover:
            - com a sessão em `processing` (o job está escrevendo nela);
            - a ÚLTIMA parte com linhas — uma conciliação sem nenhuma linha não
              tem o que conciliar; o caminho para isso é excluir a conciliação.

        Returns:
            `(total_files_restantes, precisa_reprocessar)`.
        """
        session_obj = await self._repo.get_status_view(session_id)
        if session_obj is None:
            raise NotFoundError(_SESSION_NOT_FOUND_MSG)
        if session_obj.status == ReconciliationStatus.PROCESSING.value:
            raise ConflictError(
                f"Sessão {session_id} em processamento.",
                user_message=(
                    "Não é possível remover um arquivo enquanto a conciliação "
                    "está em processamento. Aguarde o término."
                ),
            )

        file_obj = await self._repo.get_file(session_id, file_id)
        if file_obj is None:
            raise NotFoundError("Arquivo não encontrado nesta conciliação.")

        had_entries = file_obj.status == ReconciliationFileStatus.PARSED.value
        if had_entries and await self._repo.count_parsed_files(session_id) <= 1:
            raise ConflictError(
                f"Arquivo {file_id} é a última parte com linhas da sessão {session_id}.",
                user_message=(
                    "Esta é a única parte com lançamentos da conciliação. "
                    "Para descartar tudo, exclua a conciliação."
                ),
            )

        await self._repo.delete_file(file_id)
        if had_entries:
            await self._repo.reset_session_for_reprocess(session_id)

        total_files = await self._repo.count_files(session_id)
        logger.info(
            "reconciliation_file_removed",
            session_id=str(session_id),
            file_id=str(file_id),
            total_files=total_files,
            reprocess=had_entries,
        )
        return total_files, had_entries

    async def list_session_files(
        self,
        *,
        session_id: UUID,
        cipher: ClientCipher,
    ) -> SessionFilesPayload:
        """Partes da conciliação com o nome DECIFRADO, para a tela.

        Falha de decifragem vira `None` (a UI cai em "Arquivo N") + log — nunca
        derruba a tela nem devolve ciphertext (CLAUDE.md §4.1).
        """
        rows = await self._repo.list_files(session_id)
        items = [
            SessionFileItem(
                file_id=file_obj.id,
                filename=self._decrypt_filename(file_obj, cipher),
                status=file_obj.status,
                error_code=file_obj.error_code,
                entry_count=entry_count,
                created_at=file_obj.created_at,
            )
            for file_obj, entry_count in rows
        ]
        return SessionFilesPayload(
            session_id=session_id,
            total_files=len(items),
            files=items,
        )

    @staticmethod
    def _decrypt_filename(file_obj: ReconciliationFile, cipher: ClientCipher) -> str | None:
        if file_obj.filename_encrypted is None or file_obj.filename_iv is None:
            return None
        try:
            return cipher.decrypt(
                file_obj.filename_encrypted,
                file_obj.filename_iv,
                field_locator(AAD_FILE_NAME, file_obj.id),
            )
        except Exception:
            # Sem ciphertext/IV/plaintext no log (CLAUDE.md §3.3).
            logger.warning("reconciliation_filename_decrypt_failed", file_id=str(file_obj.id))
            return None

    @staticmethod
    def _assert_accepts_new_files(status: str) -> None:
        if status == ReconciliationStatus.PROCESSING.value:
            raise ConflictError(
                f"Sessão em processamento (status={status}).",
                user_message=(
                    "Esta conciliação está em processamento. Aguarde o término "
                    "para anexar mais arquivos."
                ),
            )
        if status == ReconciliationStatus.DONE.value:
            raise ConflictError(
                f"Sessão concluída (status={status}).",
                user_message=("Esta conciliação já foi concluída e não recebe novos arquivos."),
            )

    @staticmethod
    def _account_month_conflict(existing_id: UUID | None) -> ConflictError:
        """409 com CAMINHO DE SAÍDA — o usuário precisa saber o que fazer."""
        return ConflictError(
            f"Já existe conciliação ativa para esta conta e mês (session_id={existing_id}).",
            user_message=(
                "Já existe uma conciliação para esta conta e mês. Abra-a e "
                "adicione o arquivo como uma nova parte."
            ),
            metadata={"session_id": str(existing_id)} if existing_id else {},
        )

    # ------------------------------------------------------------------
    # BACK 8.6 — leitura para polling
    # ------------------------------------------------------------------

    async def get_session_status(self, session_id: UUID) -> SessionStatusPayload:
        """Retorna o estado atual da sessão para o polling do front.

        404 se sessão não existe. RBAC é responsabilidade do caller — esta
        função assume que `require_client_access` já validou.
        """
        session_obj = await self._repo.get_status_view(session_id)
        if session_obj is None:
            raise NotFoundError(_SESSION_NOT_FOUND_MSG)
        return SessionStatusPayload(
            session_id=session_obj.id,
            status=session_obj.status,
            conciliated_count=session_obj.conciliated_count,
            sem_omie_count=session_obj.sem_omie_count,
            omie_sem_arquivo_count=session_obj.omie_sem_arquivo_count,
            anomaly_count=session_obj.anomaly_count,
            error_message=session_obj.error_message,
        )

    # ------------------------------------------------------------------
    # S11 — GET /reconciliations/{id}  (header da Tela de Revisão)
    # ------------------------------------------------------------------

    async def get_session_detail(self, session_id: UUID) -> SessionDetailPayload:
        """Detalhe da conciliação: totalizadores + resumo de saldos (BACK 04.3).

        Espelha `get_session_status` mas devolve `SessionDetailPayload`,
        incluindo `client_id`, `omie_conta_id`, `reference_month` e
        `total_file_entries` — campos que o front antes resolvia via
        scan O(N) do histórico do cliente.

        **Os totalizadores são DERIVADOS das linhas** (`totals.
        compute_session_counters`), a mesma função que materializa as colunas
        que a lista lê e que a revisão re-materializa a cada ação. É por isso
        que o número do detalhe bate com o das abas: não existe um segundo
        cálculo — existe um só, consumido de dois jeitos (fresco aqui, em
        coluna na lista, para a lista não pagar 3 COUNTs por item).

        **Saldos** (`balance_*`) NÃO são recalculados aqui: `compute_balances`
        roda uma vez no fim do processamento e persiste. O export lê as mesmas
        colunas. Recalcular na leitura criaria exatamente a segunda fonte que
        este endpoint existe para evitar.

        404 se sessão não existe. RBAC é responsabilidade do caller.
        """
        session_obj = await self._repo.get_detail_view(session_id)
        if session_obj is None:
            raise NotFoundError(_SESSION_NOT_FOUND_MSG)
        counters = await self._repo.compute_counters(session_id)
        total_files = await self._repo.count_files(session_id)
        # 86e2u513f — somas e breakdown da sessão INTEIRA, no banco e em
        # Decimal (o front somava 50 linhas em float e o número mentia).
        # Sequencial, mesma AsyncSession — nunca gather (ver totals.py).
        amounts = await self._repo.compute_amounts(session_id)
        breakdown = await self._repo.compute_anomaly_breakdown(session_id)
        card_charges: Decimal | None = None
        if session_obj.account_type == SessionAccountType.CREDIT_CARD.value:
            client = await self._repo.get_client(session_obj.client_id)
            if client is not None:
                # Leitura: DEK só se já existe (linhas bare-legadas usam a
                # chave global) — mesmo caminho do ReviewService.
                cipher = await load_client_cipher(client, settings=self._settings)
                card_charges = await self._repo.compute_card_charges(session_id, cipher)
        return SessionDetailPayload(
            session_id=session_obj.id,
            client_id=session_obj.client_id,
            omie_conta_id=session_obj.omie_conta_id,
            account_type=session_obj.account_type,
            reference_month=session_obj.reference_month,
            status=session_obj.status,
            total_file_entries=counters.total_file_entries,
            conciliated_count=counters.conciliated_count,
            sem_omie_count=counters.sem_omie_count,
            omie_sem_arquivo_count=counters.omie_sem_arquivo_count,
            anomaly_count=counters.anomaly_count,
            error_message=session_obj.error_message,
            error_code=session_obj.error_code,
            balance_start=session_obj.balance_start,
            balance_end_file=session_obj.balance_end_file,
            balance_end_omie=session_obj.balance_end_omie,
            balance_difference=session_obj.balance_difference,
            total_files=total_files,
            qualification_used_glossary=session_obj.qualification_used_glossary,
            credits_total=amounts.credits_total,
            debits_total=amounts.debits_total,
            card_charges_total=card_charges,
            anomalies_critical=breakdown.critical,
            anomalies_moderate=breakdown.moderate,
            anomalies_info=breakdown.info,
            anomalies_resolved=breakdown.resolved,
        )
