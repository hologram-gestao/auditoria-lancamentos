"""Lançamento em lote das linhas `sem_omie` da fatura no Omie (BACK 07.4).

⚠️ **Esta é a primeira e única escrita do ADL no ERP do cliente.** O invariante
"Omie read-only" (CLAUDE.md §10) termina aqui. O guardrail da sprint é **zero
lançamento duplicado**, e o critério de rollback é "um único duplicado desliga o
recurso" — então todo caminho abaixo prefere **não lançar** a lançar duas vezes.

**A ordem importa e não é negociável**, por linha:

    1. o ADL consulta o PRÓPRIO estado (`reconciliation_omie_postings`) antes de
       qualquer POST — nunca o estado do fornecedor;
    2. intenção registrada ANTES do envio (é ela que existe quando a resposta
       não chega);
    3. POST;
    4. confirmação reflete na conciliação (linha vira `conciliado`, anomalia
       `missing_in_omie` resolvida, contadores recalculados pela fonte única).

**A janela de timeout é o caminho real do "único duplicado".** O `OmieClient`
retenta 5xx/timeout com backoff; se a Omie tiver aceitado o POST que expirou,
retentar cria duplicata. Por isso, quando existe uma intenção `pending` com
tentativa anterior, o serviço **reconcilia antes de decidir** — e só reenvia se
a reconciliação for CONCLUSIVA em dizer que o lançamento não entrou.
Inconclusivo ⇒ **não reenvia** e devolve o motivo para o operador conferir.

**Sequencial, nunca em paralelo.** A Omie impõe `X-Omie-ParallelRateLimit: 1/4`
por método e pune concorrência com `1880`/`6 - Consumo redundante`
(`omie/client.py`). Além disso, `asyncio.gather` sobre a MESMA `AsyncSession`
quebraria a conexão.

**Nada de PII em log.** `cObs` carrega a descrição da compra (§4.5) e a
mensagem de erro do provedor é texto livre (ADR-023-BE): nenhuma das duas entra
em log — só IDs, códigos e contadores.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select

from app.core.crypto_service import (
    AAD_ANOMALY_RESOLUTION_NOTE,
    AAD_FILE_ENTRY_DESCRIPTION,
    field_locator,
)
from app.core.exceptions import (
    OmieAuthError,
    OmieFaultError,
    OmieLancamentoAlreadyLinkedError,
    OmiePostingDisabledError,
    OmiePostingKeyCollisionError,
    OmiePostingNotEligibleError,
    OmieServerError,
    OmieTimeoutError,
    ValidationAppError,
)
from app.core.logging import get_logger
from app.db.models import (
    AnomalyType,
    FileEntrySituation,
    OmiePostingStatus,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
    SessionAccountType,
)
from app.integrations.omie.schemas import IncluirLancCCRequest
from app.modules.reconciliations.omie_posting.repository import OmiePostingRepository
from app.modules.reconciliations.omie_posting.schemas import (
    OmiePostingBatchPayload,
    OmiePostingLineRequest,
    OmiePostingLineResult,
    PostingLineReason,
)
from app.modules.reconciliations.processing.anomalies import ANOMALY_CODE_MISSING_IN_OMIE
from app.modules.reconciliations.totals import refresh_session_counters
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.core.crypto import ClientCipher
    from app.db.models import ReconciliationSession
    from app.integrations.omie.client import OmieClient

log = get_logger(__name__)

#: `cNatureza` — é ele que carrega o sinal; `nValorLanc` vai absoluto.
#: Convenção confirmada só do lado de LEITURA (`ListarExtrato`); no lado de
#: escrita segue NÃO-VERIFICADA (S-1 / ADR-019-BE).
NATUREZA_DEBITO = "D"  # compra
NATUREZA_CREDITO = "C"  # estorno

#: Nota gravada na anomalia resolvida. Sem PII: só o ID do lançamento Omie.
#: O mínimo de 10 chars da revisão (Doc §17.3) é respeitado com folga.
_RESOLUTION_NOTE = "Lancado no Omie pelo ADL - lancamento {omie_lancamento_id}."


@dataclass(frozen=True, slots=True)
class _Reconciliation:
    """Resultado de olhar o extrato atrás de um lançamento que pode ter entrado.

    Três estados, e o terceiro é o que evita o duplicado:
      - `found` — está lá, com o `nCodLanc`;
      - `absent` — CONCLUSIVAMENTE não está (o extrato traz `cCodIntLanc` em
        alguma linha, logo o campo existe, logo a ausência significa algo);
      - `inconclusive` — não dá para saber (nenhuma linha traz `cCodIntLanc`).
        Reenviar aqui seria apostar o dinheiro do cliente numa suposição.
    """

    state: str  # "found" | "absent" | "inconclusive"
    omie_lancamento_id: int | None = None


class OmiePostingService:
    """Executa um lote de lançamentos, uma linha por vez."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        *,
        omie_client_factory: Callable[[], Awaitable[OmieClient]],
        cipher: ClientCipher,
    ) -> None:
        self._db = db
        self._settings = settings
        self._factory = omie_client_factory
        self._cipher = cipher
        self._repo = OmiePostingRepository(db)
        # Instrumentação da BACK 07.5. Fail-soft por construção (o `emit` do
        # service engole a falha e loga): sink fora do ar não pode derrubar um
        # lançamento que já aconteceu no ERP do cliente.
        self._events = UsageEventService(UsageEventRepository(db))

    # ------------------------------------------------------------------
    # Entrada
    # ------------------------------------------------------------------

    async def post_batch(
        self,
        *,
        session: ReconciliationSession,
        lines: list[OmiePostingLineRequest],
    ) -> OmiePostingBatchPayload:
        """Lança o lote e devolve o resumo por linha + agregados.

        Raises:
            OmiePostingDisabledError: kill-switch desligado (409).
            OmiePostingNotEligibleError: sessão não é de cartão (400).
            ValidationAppError: lote acima do teto do servidor (400).
            OmieAuthError / OmieTimeoutError / OmieServerError: falha de
                dependência quando **nenhuma** linha foi lançada — vira 5xx
                para que o alerting enxergue a indisponibilidade em vez de um
                200 com "deu ruim em todas".
        """
        self._require_enabled()
        self._require_credit_card(session)
        self._require_batch_within_cap(lines)

        started = time.monotonic()
        entries = await self._load_entries(session.id, [ln.file_entry_id for ln in lines])
        postings = await self._repo.list_by_file_entries(
            client_id=session.client_id,
            file_entry_ids=[ln.file_entry_id for ln in lines],
        )

        results: list[OmiePostingLineResult] = []
        dependency_failure: Exception | None = None

        for line in lines:
            if dependency_failure is not None:
                # A Omie está fora. Continuar bateria no rate limit e só
                # geraria ruído — as linhas restantes ficam explicitamente
                # como "indisponível", não como "erro do lançamento".
                results.append(
                    _line_error(line.file_entry_id, "omie_indisponivel", _UNAVAILABLE_MSG)
                )
                continue
            try:
                results.append(
                    await self._post_one(
                        session=session, line=line, entries=entries, postings=postings
                    )
                )
            except (OmieTimeoutError, OmieServerError, OmieAuthError) as exc:
                dependency_failure = exc
                await self._events.emit_omie_lancamento_rejeitado(
                    session_id=session.id,
                    codigo=(
                        "OMIE_AUTH_ERROR" if isinstance(exc, OmieAuthError) else "OMIE_TIMEOUT"
                    ),
                    fault_message=exc.user_message,
                )
                results.append(
                    _line_error(line.file_entry_id, "omie_indisponivel", exc.user_message)
                )

        posted = [r for r in results if r.status == "lancada"]
        if posted:
            # Fonte ÚNICA dos totalizadores (CLAUDE.md §7 / totals.py) — nunca
            # somar/subtrair contador na mão aqui.
            await refresh_session_counters(self._db, session.id)
        elif dependency_failure is not None:
            # Nenhuma linha entrou E a Omie caiu: isto é indisponibilidade, não
            # resultado de negócio. 4xx/200 aqui seria invisível ao alerting.
            raise dependency_failure

        payload = OmiePostingBatchPayload(
            lines=results,
            lancadas=len(posted),
            bloqueadas=sum(1 for r in results if r.status == "bloqueada"),
            com_erro=sum(1 for r in results if r.status == "erro"),
        )
        # `duracao_ms` INTEIRO (§3.4) e medido sobre o lote inteiro — é o que
        # sustenta o guardrail "o tempo de conciliação não pode subir".
        await self._events.emit_omie_lancamento_enviado(
            session_id=session.id,
            linhas=len(lines),
            sucesso=payload.lancadas,
            falha=payload.com_erro,
            duracao_ms=round((time.monotonic() - started) * 1000),
        )
        log.info(
            "omie_posting_batch_done",
            session_id=str(session.id),
            client_id=str(session.client_id),
            requested=len(lines),
            lancadas=payload.lancadas,
            bloqueadas=payload.bloqueadas,
            com_erro=payload.com_erro,
        )
        return payload

    # ------------------------------------------------------------------
    # Guardas do lote inteiro
    # ------------------------------------------------------------------

    def _require_enabled(self) -> None:
        if not self._settings.OMIE_POSTING_ENABLED:
            log.warning("omie_posting_disabled_attempt")
            raise OmiePostingDisabledError("OMIE_POSTING_ENABLED=false")

    @staticmethod
    def _require_credit_card(session: ReconciliationSession) -> None:
        """Só cartão. ⚠️ `credit_card` é o `CR` do Omie — o PRD diz `CA` e está
        errado (`CA` é Conta Aplicação; ver ADR-020-BE)."""
        if session.account_type != SessionAccountType.CREDIT_CARD.value:
            raise OmiePostingNotEligibleError(
                f"Sessão {session.id} é '{session.account_type}', não 'credit_card'.",
            )

    def _require_batch_within_cap(self, lines: list[OmiePostingLineRequest]) -> None:
        cap = self._settings.OMIE_POSTING_MAX_BATCH
        if len(lines) > cap:
            raise ValidationAppError(
                f"Lote com {len(lines)} linhas; teto do servidor é {cap}.",
                user_message=(
                    f"Só é possível lançar até {cap} compras por vez. "
                    "Selecione menos linhas e repita."
                ),
            )

    # ------------------------------------------------------------------
    # Uma linha
    # ------------------------------------------------------------------

    async def _post_one(
        self,
        *,
        session: ReconciliationSession,
        line: OmiePostingLineRequest,
        entries: dict[UUID, ReconciliationFileEntry],
        postings: dict[UUID, ReconciliationOmiePosting],
    ) -> OmiePostingLineResult:
        entry = entries.get(line.file_entry_id)
        if entry is None:
            # Inclui a linha de OUTRA sessão: o `_load_entries` já filtra por
            # `session_id`, então "não está no dicionário" cobre inexistente e
            # alheia com a mesma resposta — sem distinguir, sem enumerar.
            return _line_blocked(line.file_entry_id, "linha_inexistente", "Linha não encontrada.")

        blocked = _eligibility_block(entry)
        if blocked is not None:
            return blocked

        existing = postings.get(line.file_entry_id)
        decided = await self._decide_from_own_state(session=session, line=line, existing=existing)
        if decided is not None:
            return decided

        return await self._send(session=session, line=line, entry=entry)

    async def _decide_from_own_state(
        self,
        *,
        session: ReconciliationSession,
        line: OmiePostingLineRequest,
        existing: ReconciliationOmiePosting | None,
    ) -> OmiePostingLineResult | None:
        """Dedup PRIMÁRIA: o ADL olha o próprio estado antes de qualquer POST.

        Devolve o resultado final quando o estado já decide; `None` quando pode
        seguir para o envio.
        """
        if existing is None:
            return None
        if existing.status == OmiePostingStatus.CONFIRMED.value:
            return _line_blocked(
                line.file_entry_id,
                "ja_lancada",
                "Esta compra já foi lançada no Omie.",
                omie_lancamento_id=existing.omie_lancamento_id,
            )
        if existing.status == OmiePostingStatus.PENDING.value and existing.attempts > 0:
            # Houve envio anterior sem confirmação (o caso do timeout). NÃO
            # reenvia às cegas — pergunta ao Omie primeiro.
            return await self._resolve_pending(session=session, line=line, existing=existing)
        return None

    async def _resolve_pending(
        self,
        *,
        session: ReconciliationSession,
        line: OmiePostingLineRequest,
        existing: ReconciliationOmiePosting,
    ) -> OmiePostingLineResult | None:
        cod_int_lanc = existing.cod_int_lanc
        entry_date = await self._db.scalar(
            select(ReconciliationFileEntry.transaction_date).where(
                ReconciliationFileEntry.id == line.file_entry_id
            )
        )
        if entry_date is None:  # pragma: no cover  -- a linha já foi carregada acima
            return _line_blocked(line.file_entry_id, "linha_inexistente", "Linha não encontrada.")

        outcome = await self._reconcile(
            omie_conta_id=session.omie_conta_id,
            cod_int_lanc=cod_int_lanc,
            transaction_date=entry_date,
        )
        if outcome.state == "found" and outcome.omie_lancamento_id is not None:
            await self._confirm(
                session=session,
                file_entry_id=line.file_entry_id,
                omie_lancamento_id=outcome.omie_lancamento_id,
            )
            return OmiePostingLineResult(
                file_entry_id=line.file_entry_id,
                status="lancada",
                reason="reconciliada",
                message="O lançamento já estava no Omie — vínculo restabelecido.",
                omie_lancamento_id=outcome.omie_lancamento_id,
            )
        if outcome.state == "absent":
            return None  # conclusivamente não entrou → pode reenviar
        return _line_blocked(
            line.file_entry_id,
            "envio_anterior_sem_confirmacao",
            (
                "Houve uma tentativa anterior cujo resultado não foi confirmado e não "
                "foi possível verificar no Omie. Confira o lançamento por lá antes de "
                "tentar de novo — reenviar às cegas poderia duplicar."
            ),
        )

    async def _send(
        self,
        *,
        session: ReconciliationSession,
        line: OmiePostingLineRequest,
        entry: ReconciliationFileEntry,
    ) -> OmiePostingLineResult:
        try:
            posting = await self._repo.register_intent(
                client_id=session.client_id,
                session_id=session.id,
                file_entry_id=entry.id,
            )
        except OmiePostingKeyCollisionError as exc:
            return _line_blocked(entry.id, "chave_em_conflito", exc.user_message)

        # Contar ANTES do POST: se a resposta nunca chegar, a tentativa já está
        # gravada e é ela que dispara a reconciliação na próxima execução.
        await self._repo.increment_attempts(client_id=session.client_id, file_entry_id=entry.id)
        # **Barreira de durabilidade.** A transação da request só commitaria no
        # fim do handler; se o processo morrer durante o POST, a intenção e a
        # tentativa sumiriam — e a próxima execução reenviaria às cegas para um
        # lançamento que talvez já esteja no Omie. Commit aqui é o que torna
        # "eu tentei" um fato antes de haver efeito externo.
        await self._db.commit()

        request = self._build_request(
            session=session,
            entry=entry,
            cod_categoria=line.cod_categoria,
            cod_int_lanc=posting.cod_int_lanc,
        )

        omie_client = await self._factory()
        try:
            response = await omie_client.incluir_lanc_cc(request)
        except OmieFaultError as exc:
            # `faultstring` (HTTP 200 em erro) = falha DEFINITIVA daquela linha.
            # Nada é marcado como lançado. A mensagem do provedor volta ao
            # usuário — é o que torna o erro acionável — e não vai para o log.
            await self._repo.mark_failed(
                client_id=session.client_id,
                file_entry_id=entry.id,
                error_code=exc.code.value,
                error_message=exc.user_message,
            )
            await self._db.commit()
            log.info(
                "omie_posting_line_failed",
                session_id=str(session.id),
                file_entry_id=str(entry.id),
                error_code=exc.code.value,
            )
            # O `user_message` entra só para ser CLASSIFICADO — a família vai
            # para o sink, o texto não (ADR-031-BE).
            await self._events.emit_omie_lancamento_rejeitado(
                session_id=session.id,
                codigo="OMIE_FAULT",
                fault_message=exc.user_message,
            )
            return _line_error(entry.id, "erro_omie", exc.user_message)
        finally:
            await omie_client.aclose()

        try:
            await self._confirm(
                session=session,
                file_entry_id=entry.id,
                omie_lancamento_id=response.n_cod_lanc,
            )
        except OmieLancamentoAlreadyLinkedError as exc:
            # O lançamento ENTROU no Omie, mas o ID já fecha outra linha desta
            # sessão. Não dá para desfazer (ExcluirLancCC está fora de escopo):
            # registramos a falha do vínculo e devolvemos o motivo — nunca
            # contornamos o índice.
            await self._repo.mark_failed(
                client_id=session.client_id,
                file_entry_id=entry.id,
                error_code=exc.code.value,
                error_message=exc.user_message,
            )
            await self._db.commit()
            return _line_blocked(entry.id, "lancamento_ja_vinculado", exc.user_message)

        return OmiePostingLineResult(
            file_entry_id=entry.id,
            status="lancada",
            omie_lancamento_id=response.n_cod_lanc,
        )

    # ------------------------------------------------------------------
    # Montagem do request
    # ------------------------------------------------------------------

    def _build_request(
        self,
        *,
        session: ReconciliationSession,
        entry: ReconciliationFileEntry,
        cod_categoria: str,
        cod_int_lanc: str,
    ) -> IncluirLancCCRequest:
        """Monta o `IncluirLancCC` de UMA linha. Uma parcela = um lançamento.

        O sinal vai em `cNatureza`, **não** no número: `nValorLanc` é sempre
        absoluto, com 2 casas. Compra (valor negativo no extrato) → `'D'`;
        estorno (valor positivo) → `'C'`. Inverter isso lança um estorno como
        despesa na contabilidade do cliente.
        """
        amount = Decimal(entry.amount)
        natureza = NATUREZA_CREDITO if amount > 0 else NATUREZA_DEBITO
        valor = abs(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return IncluirLancCCRequest(
            n_cod_cc=session.omie_conta_id,
            d_dt_lanc=entry.transaction_date.strftime("%d/%m/%Y"),
            n_valor_lanc=valor,
            c_natureza=natureza,
            c_cod_categ=cod_categoria,
            c_cod_int_lanc=cod_int_lanc,
            c_obs=self._describe(entry),
            # `cTipo` NÃO é enviado: `DIN` é palpite não-verificado (S-1). Um
            # valor inventado faria a Omie recusar a linha inteira por um campo
            # opcional. Só passa a ser enviado quando a fixture confirmar.
            c_tipo=None,
        )

    def _describe(self, entry: ReconciliationFileEntry) -> str | None:
        """Descrição da compra, decifrada em memória. **Nunca logada** (§4.5).

        Falha de decrypt não derruba o lançamento: o `cObs` é opcional no Omie,
        e deixar de lançar por causa de um campo de texto seria pior do que
        lançar sem observação. A falha fica registrada como métrica.
        """
        try:
            return self._cipher.decrypt(
                entry.description_encrypted,
                entry.description_iv,
                field_locator(AAD_FILE_ENTRY_DESCRIPTION, entry.id),
            )
        except Exception:
            log.warning("omie_posting_decrypt_failed", file_entry_id=str(entry.id))
            return None

    # ------------------------------------------------------------------
    # Reconciliação pós-timeout
    # ------------------------------------------------------------------

    async def _reconcile(
        self,
        *,
        omie_conta_id: int,
        cod_int_lanc: str,
        transaction_date: date,
    ) -> _Reconciliation:
        """ "Este lançamento entrou?" — perguntado ao extrato, pelo `cCodIntLanc`.

        ⚠️ Que o `ListarExtrato` devolva `cCodIntLanc` é **NÃO-VERIFICADO**
        (S-1). Por isso o resultado tem três estados e não dois: se **nenhuma**
        linha do extrato trouxer o campo, não dá para distinguir "não entrou"
        de "a Omie não devolve esse campo" — e o serviço trata isso como
        INCONCLUSIVO, que nunca leva a reenvio.

        A janela é o dia exato do lançamento: foi essa a data enviada em
        `dDtLanc`, e alargá-la só aumentaria a chance de casar com outra coisa.
        """
        omie_client = await self._factory()
        try:
            movimentos = await omie_client.listar_extrato(
                n_cod_cc=omie_conta_id,
                data_inicial=transaction_date,
                data_final=transaction_date,
            )
        finally:
            await omie_client.aclose()

        for mov in movimentos:
            if mov.c_cod_int_lanc == cod_int_lanc:
                return _Reconciliation("found", mov.n_cod_lancamento)
        if any(mov.c_cod_int_lanc is not None for mov in movimentos):
            # O campo existe e é populado — a ausência é informação.
            return _Reconciliation("absent")
        log.warning(
            "omie_posting_reconcile_inconclusive",
            omie_conta_id=omie_conta_id,
            movimentos=len(movimentos),
        )
        return _Reconciliation("inconclusive")

    # ------------------------------------------------------------------
    # Confirmação
    # ------------------------------------------------------------------

    async def _confirm(
        self,
        *,
        session: ReconciliationSession,
        file_entry_id: UUID,
        omie_lancamento_id: int,
    ) -> None:
        """Confirma a intenção, reflete na linha, resolve a anomalia e COMMITA.

        O reflexo na `file_entry` (situação + `omie_lancamento_id`) é do
        repositório — um único lugar escreve esse par, e é ele que convive com
        o índice parcial `ix_recon_file_entry_session_omie_unique`.

        O commit no fim não é otimização: é o que impede que uma falha
        posterior apague a memória de um efeito externo irreversível.
        """
        await self._repo.mark_confirmed(
            client_id=session.client_id,
            file_entry_id=file_entry_id,
            omie_lancamento_id=omie_lancamento_id,
        )
        await self._resolve_missing_in_omie(
            session_id=session.id,
            file_entry_id=file_entry_id,
            omie_lancamento_id=omie_lancamento_id,
        )
        # **Barreira de durabilidade** (ADR-028-BE). O lançamento JÁ existe no
        # Omie e não há como desfazê-lo (`ExcluirLancCC` está fora de escopo).
        # Sem este commit, qualquer exceção mais adiante no lote — outra linha,
        # o recálculo de contadores, um erro de rede — daria rollback na
        # memória de "esta linha foi lançada", e a próxima execução criaria o
        # DUPLICADO que a sprint inteira existe para evitar.
        await self._db.commit()

    async def _resolve_missing_in_omie(
        self,
        *,
        session_id: UUID,
        file_entry_id: UUID,
        omie_lancamento_id: int,
    ) -> None:
        """Fecha a anomalia `missing_in_omie` da linha, referenciando o lançamento.

        A anomalia dizia "existe no arquivo e não existe no Omie". Depois do
        lançamento isso deixou de ser verdade — mantê-la aberta deixaria a
        pendência na tela para sempre. A nota é cifrada como todo
        `resolution_note` e **não contém PII**: só o ID do lançamento.

        Sem anomalia correspondente (linha `sem_omie` que nunca gerou uma), não
        há o que fazer — e isso não é erro.
        """
        anomaly = (
            await self._db.execute(
                select(ReconciliationAnomaly)
                .join(AnomalyType, AnomalyType.id == ReconciliationAnomaly.anomaly_type_id)
                .where(
                    ReconciliationAnomaly.session_id == session_id,
                    ReconciliationAnomaly.file_entry_id == file_entry_id,
                    ReconciliationAnomaly.resolved.is_(False),
                    AnomalyType.code == ANOMALY_CODE_MISSING_IN_OMIE,
                )
            )
        ).scalar_one_or_none()
        if anomaly is None:
            return
        note = _RESOLUTION_NOTE.format(omie_lancamento_id=omie_lancamento_id)
        try:
            ct, iv = self._cipher.encrypt(
                note, field_locator(AAD_ANOMALY_RESOLUTION_NOTE, anomaly.id)
            )
        except Exception:
            # Cifrar exige a DEK do cliente; cliente sem DEK provisionada (o
            # `decrypt` ainda lê o legado bare, o `encrypt` NÃO tem esse
            # caminho) faria isto explodir. **Fail-soft de propósito:** neste
            # ponto o lançamento JÁ está no Omie. Derrubar a request daria
            # rollback na confirmação, e a próxima tentativa criaria o
            # DUPLICADO que a sprint existe para evitar — perder a NOTA da
            # anomalia é incomparavelmente mais barato. A anomalia é resolvida
            # assim mesmo: continuar afirmando "não existe no Omie" passou a
            # ser falso.
            log.warning(
                "omie_posting_resolution_note_encrypt_failed",
                anomaly_id=str(anomaly.id),
                omie_lancamento_id=omie_lancamento_id,
            )
            anomaly.resolved = True
            await self._db.flush()
            return
        anomaly.resolution_note_encrypted = ct
        anomaly.resolution_note_iv = iv
        anomaly.resolved = True
        await self._db.flush()

    # ------------------------------------------------------------------
    # Leitura
    # ------------------------------------------------------------------

    async def _load_entries(
        self,
        session_id: UUID,
        file_entry_ids: list[UUID],
    ) -> dict[UUID, ReconciliationFileEntry]:
        """Linhas DA SESSÃO, indexadas por id.

        O `AND session_id` é o que impede lançar a linha de outra conciliação
        passando o UUID dela no body — e, como a sessão já foi resolvida com o
        filtro de tenant, também impede cruzar tenants.
        """
        rows = (
            (
                await self._db.execute(
                    select(ReconciliationFileEntry).where(
                        ReconciliationFileEntry.session_id == session_id,
                        ReconciliationFileEntry.id.in_(file_entry_ids),
                    )
                )
            )
            .scalars()
            .all()
        )
        return {row.id: row for row in rows}


# ----------------------------------------------------------------------
# Helpers de resultado
# ----------------------------------------------------------------------

_UNAVAILABLE_MSG = (
    "O Omie não respondeu. Nenhum lançamento foi confirmado para esta linha — "
    "confira no Omie antes de tentar de novo."
)


def _eligibility_block(entry: ReconciliationFileEntry) -> OmiePostingLineResult | None:
    """Bloqueios que dependem só do estado da linha. `None` = elegível."""
    if entry.situation == FileEntrySituation.IGNORADO.value:
        return _line_blocked(
            entry.id, "linha_ignorada", "Linha ignorada na revisão — não é lançada."
        )
    if entry.omie_lancamento_id is not None:
        return _line_blocked(
            entry.id,
            "ja_lancada",
            "Esta linha já está vinculada a um lançamento do Omie.",
            omie_lancamento_id=entry.omie_lancamento_id,
        )
    if entry.situation != FileEntrySituation.SEM_OMIE.value:
        return _line_blocked(
            entry.id,
            "nao_e_sem_omie",
            "Só compras sem correspondente no Omie podem ser lançadas.",
        )
    return None


def _line_blocked(
    file_entry_id: UUID,
    reason: PostingLineReason,
    message: str,
    *,
    omie_lancamento_id: int | None = None,
) -> OmiePostingLineResult:
    return OmiePostingLineResult(
        file_entry_id=file_entry_id,
        status="bloqueada",
        reason=reason,
        message=message,
        omie_lancamento_id=omie_lancamento_id,
    )


def _line_error(
    file_entry_id: UUID, reason: PostingLineReason, message: str
) -> OmiePostingLineResult:
    return OmiePostingLineResult(
        file_entry_id=file_entry_id,
        status="erro",
        reason=reason,
        message=message,
    )
