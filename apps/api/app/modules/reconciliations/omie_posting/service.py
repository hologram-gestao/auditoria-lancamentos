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

**Duas formas de lançar (86e3n70q0, bloco B), escolhidas POR LINHA.**

- `direto` — o `IncluirLancCC` acima, sem mudança. Só no processo "na data da
  compra" (ele grava a data da compra) e só com `OMIE_POSTING_ENABLED`.
- `provisionar` (o padrão) — título no Contas a Pagar, com kill-switch próprio
  (`OMIE_PAYABLE_POSTING_ENABLED`). Até três escritas: fornecedor (só se o CNPJ
  não tem cadastro), título e, SÓ no processo "na data da compra", a baixa na
  data da compra. No processo "vencimento da fatura" a provisão para no título:
  a baixa depende do pagamento efetivo da fatura (bloco 0, 11/10/2026).

A provisão é uma máquina de estados com RETOMADA (`step` + ids na intenção):
cada passo lê o estado gravado, pula o que já tem id e grava o id que recebeu
COMMITANDO antes de seguir. As três escritas são idempotentes sobre a chave de
integração (captura de 13/10/2026), então, ao contrário do `direto`, um envio
sem confirmação (timeout) PODE ser repetido com a mesma chave — até
`MAX_STEP_SENDS` envios por passo; depois disso a linha pede conferência no
Omie, como hoje. Sucesso parcial é estado legítimo e a resposta diz o passo.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import select

from app.core.branding import PRODUCT_NAME
from app.core.cnpj import format_cnpj
from app.core.crypto_service import (
    AAD_ANOMALY_RESOLUTION_NOTE,
    AAD_FILE_ENTRY_DESCRIPTION,
    field_locator,
)
from app.core.exceptions import (
    ErrorCode,
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
    CardPostingDateMode,
    FileEntrySituation,
    InstallmentStep,
    OmiePostingKind,
    OmiePostingStatus,
    OmiePostingStep,
    ReconciliationAnomaly,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
    ReconciliationOmiePostingInstallment,
    SessionAccountType,
)
from app.integrations.omie.client import omie_error_code
from app.integrations.omie.schemas import (
    ContaPagarCategoria,
    ContaPagarDistribuicao,
    IncluirClienteRequest,
    IncluirContaPagarRequest,
    IncluirLancCCRequest,
    LancarPagamentoRequest,
    LancCCCabecalho,
    LancCCDetalhes,
)
from app.modules.omie_data.payables_service import pick_supplier
from app.modules.reconciliations.installments import Installment, entry_installment
from app.modules.reconciliations.omie_posting.installment_plan import (
    current_installment_date,
    plan_future_installments,
)
from app.modules.reconciliations.omie_posting.keys import (
    derive_payable_key,
    derive_payment_key,
    derive_supplier_key,
)
from app.modules.reconciliations.omie_posting.missing_installments import (
    flag_missing_installments,
)
from app.modules.reconciliations.omie_posting.repository import (
    InstallmentRepository,
    OmiePostingRepository,
    invoice_anchor,
)
from app.modules.reconciliations.omie_posting.schemas import (
    SOMA_TOLERANCIA,
    OmiePostingBatchPayload,
    OmiePostingInstallmentResult,
    OmiePostingLineRequest,
    OmiePostingLineResult,
    PostingLineReason,
    PostingLineStatus,
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
    from app.db.models import Client, ReconciliationSession
    from app.integrations.omie.client import OmieClient

log = get_logger(__name__)

#: Mensagem do bloqueio de ESTORNO (linha com valor positivo). O contrato real
#: do `IncluirLancCC` não tem campo de sinal (`cNatureza` não existe na
#: escrita — recusa real de 21/08/2026, ver `IncluirLancCCRequest`), então só
#: a COMPRA (débito, valor absoluto) tem representação com evidência. Lançar
#: estorno "no palpite" poderia registrá-lo como uma segunda despesa.
_ESTORNO_BLOCK_MESSAGE = (
    "Estornos ainda não podem ser lançados: a representação de crédito no "
    "Omie ainda não foi verificada. Lance apenas as compras."
)

#: `cTipo` do lançamento. A doc oficial o trata como essencial (a ausência é
#: suspeita do `3102` da captura de 21/08/2026) e o exemplo usa `DIN`. `CRT`
#: (cartão) existe no domínio e pode ser semanticamente melhor para compra de
#: fatura — trocar só com evidência de aceite; o valor enviado aqui deve ser o
#: MESMO que a fixture de aceite provou.
_C_TIPO_DOCUMENTO = "DIN"

#: Nota gravada na anomalia resolvida. Sem PII: só o ID do lançamento Omie.
#: O mínimo de 10 chars da revisão (Doc §17.3) é respeitado com folga.
_RESOLUTION_NOTE = f"Lancado no Omie pelo {PRODUCT_NAME} - lancamento {{omie_lancamento_id}}."
#: Nota da provisão concluída: só os ids do título e (quando houver) da baixa.
_PROVISION_NOTE = f"Provisionado no Omie pelo {PRODUCT_NAME} - titulo {{payable_id}}."
_PROVISION_PAID_NOTE = (
    f"Provisionado no Omie pelo {PRODUCT_NAME} - titulo {{payable_id}}, baixa {{payment_id}}."
)

#: Envios de UM passo da provisão sem resposta conclusiva (timeout/5xx) antes de
#: a linha parar e pedir conferência no Omie: o 1º envio e até 2 reenvios. Os
#: reenvios são seguros porque as três escritas são idempotentes sobre a chave
#: (captura de 13/10/2026); o teto existe para uma Omie instável não virar uma
#: linha reenviada a cada clique para sempre.
MAX_STEP_SENDS = 3

#: Erro da Omie para data em competência FECHADA no Painel do Contador (bloco 0,
#: item g): mostrado linha a linha, sem consulta prévia.
OMIE_CODE_CLOSED_COMPETENCE = "757"

#: Data no formato da Omie.
_OMIE_DATE = "%d/%m/%Y"


class _DependencyFailureError(Exception):
    """A Omie caiu NO MEIO de uma provisão: carrega o resultado da linha (com o
    passo e os ids já gravados) e a causa, para o lote parar como no `direto`."""

    def __init__(self, result: OmiePostingLineResult, cause: Exception) -> None:
        super().__init__(str(cause))
        self.result = result
        self.cause = cause


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
        self._installments = InstallmentRepository(db)
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
        client: Client,
        lines: list[OmiePostingLineRequest],
    ) -> OmiePostingBatchPayload:
        """Lança o lote e devolve o resumo por linha + agregados.

        Cada linha vai na forma que pediu (`modo`): `direto` pelo `IncluirLancCC`
        de sempre, `provisionar` pelo Contas a Pagar. Uma forma desligada no
        ambiente bloqueia só as linhas dela; as duas desligadas recusam o lote.

        Raises:
            OmiePostingDisabledError: as DUAS escritas desligadas (409).
            OmiePostingNotEligibleError: sessão não é de cartão (400).
            ValidationAppError: lote acima do teto do servidor (400).
            OmieAuthError / OmieTimeoutError / OmieServerError: falha de
                dependência quando **nenhuma** linha foi lançada — vira 5xx
                para que o alerting enxergue a indisponibilidade em vez de um
                200 com "deu ruim em todas".
        """
        self._require_some_posting_enabled()
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
        provision = _ProvisionTally()

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
                result = await self._post_one(
                    session=session,
                    client=client,
                    line=line,
                    entries=entries,
                    postings=postings,
                    tally=provision,
                )
            except _DependencyFailureError as exc:
                dependency_failure = exc.cause
                await self._emit_unavailable(session.id, exc.cause)
                result = exc.result
            except (OmieTimeoutError, OmieServerError, OmieAuthError) as exc:
                dependency_failure = exc
                await self._emit_unavailable(session.id, exc)
                result = _line_error(line.file_entry_id, "omie_indisponivel", exc.user_message)
            results.append(result)
            if line.modo == "provisionar":
                provision.count(result)

        # 86e3qhhtb — parcela futura de uma série que a fatura desta conciliação
        # deveria ter trazido e não trouxe vira aviso. Depois do lote, porque o
        # lote pode ter acabado de encontrar parcelas. Fail-soft: o aviso nunca
        # derruba um lote que já escreveu no Omie.
        flagged = await self._flag_missing_installments_safely(session)

        posted = [r for r in results if r.status == "lancada"]
        if posted or flagged:
            # Fonte ÚNICA dos totalizadores (CLAUDE.md §7 / totals.py) — nunca
            # somar/subtrair contador na mão aqui.
            await refresh_session_counters(self._db, session.id)
        elif dependency_failure is not None and not provision.wrote_anything:
            # Nenhuma linha entrou E a Omie caiu: isto é indisponibilidade, não
            # resultado de negócio. 4xx/200 aqui seria invisível ao alerting.
            # Com um título ou fornecedor já gravado no meio do caminho, o 200
            # com o passo de cada linha é a resposta honesta.
            raise dependency_failure

        payload = OmiePostingBatchPayload(
            lines=results,
            lancadas=len(posted),
            bloqueadas=sum(1 for r in results if r.status == "bloqueada"),
            com_erro=sum(1 for r in results if r.status == "erro"),
        )
        duration_ms = round((time.monotonic() - started) * 1000)
        # `duracao_ms` INTEIRO (§3.4) e medido sobre o lote inteiro — é o que
        # sustenta o guardrail "o tempo de conciliação não pode subir".
        await self._events.emit_omie_lancamento_enviado(
            session_id=session.id,
            linhas=len(lines),
            sucesso=payload.lancadas,
            falha=payload.com_erro,
            duracao_ms=duration_ms,
        )
        if provision.linhas:
            await self._events.emit_lancamento_omie_provisao(
                session_id=session.id,
                processo=_process_of(session),
                linhas=provision.linhas,
                concluidas=provision.concluidas,
                parciais=provision.parciais,
                bloqueadas=provision.bloqueadas,
                com_erro=provision.com_erro,
                fornecedores_criados=provision.fornecedores_criados,
                titulos_criados=provision.titulos_criados,
                baixas_criadas=provision.baixas_criadas,
                parcelas_futuras=provision.parcelas_futuras,
                parcelas_encontradas=provision.parcelas_encontradas,
                duracao_ms=duration_ms,
            )
        log.info(
            "omie_posting_batch_done",
            session_id=str(session.id),
            client_id=str(session.client_id),
            requested=len(lines),
            provisao=provision.linhas,
            lancadas=payload.lancadas,
            bloqueadas=payload.bloqueadas,
            com_erro=payload.com_erro,
        )
        return payload

    async def _flag_missing_installments_safely(self, session: ReconciliationSession) -> int:
        """O aviso num SAVEPOINT: falhar (ex.: cliente sem DEK para cifrar o contexto)
        desfaz só o aviso. Um `rollback()` da sessão inteira expiraria os objetos que
        o lote ainda lê depois daqui."""
        session_id = session.id
        try:
            async with self._db.begin_nested():
                created = await flag_missing_installments(
                    self._db, session=session, cipher=self._cipher
                )
        except Exception:
            log.warning("missing_installments_flag_failed", session_id=str(session_id))
            created = 0
        await self._db.commit()
        return created

    async def _emit_unavailable(self, session_id: UUID, exc: Exception) -> None:
        user_message = getattr(exc, "user_message", "")
        await self._events.emit_omie_lancamento_rejeitado(
            session_id=session_id,
            codigo="OMIE_AUTH_ERROR" if isinstance(exc, OmieAuthError) else "OMIE_TIMEOUT",
            fault_message=user_message,
        )

    # ------------------------------------------------------------------
    # Guardas do lote inteiro
    # ------------------------------------------------------------------

    def _require_some_posting_enabled(self) -> None:
        """Ao menos UMA das duas escritas ligada; senão o lote é 409 inteiro.

        Com só uma ligada, quem decide é a linha: a forma desligada volta
        bloqueada com motivo (`direto_desligado` / `provisao_desligada`).
        """
        if not (self._settings.OMIE_POSTING_ENABLED or self._settings.OMIE_PAYABLE_POSTING_ENABLED):
            log.warning("omie_posting_disabled_attempt")
            raise OmiePostingDisabledError(
                "OMIE_POSTING_ENABLED=false e OMIE_PAYABLE_POSTING_ENABLED=false"
            )

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
        client: Client,
        line: OmiePostingLineRequest,
        entries: dict[UUID, ReconciliationFileEntry],
        postings: dict[UUID, ReconciliationOmiePosting],
        tally: _ProvisionTally,
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
        if line.modo == "direto":
            result = await self._post_direct(
                session=session, line=line, entry=entry, existing=existing
            )
            # A `ja_lancada` de uma provisão já vem com `kind`; o resto é `direto`.
            if result.kind is None:
                result = result.model_copy(update={"kind": "direto"})
            return result
        return await self._provision(
            session=session, client=client, line=line, entry=entry, existing=existing, tally=tally
        )

    # ------------------------------------------------------------------
    # Lançamento DIRETO (Sprint 7) — sem mudança de comportamento
    # ------------------------------------------------------------------

    async def _post_direct(
        self,
        *,
        session: ReconciliationSession,
        line: OmiePostingLineRequest,
        entry: ReconciliationFileEntry,
        existing: ReconciliationOmiePosting | None,
    ) -> OmiePostingLineResult:
        """O `IncluirLancCC` da Sprint 7, agora atrás de duas perguntas da linha."""
        if not self._settings.OMIE_POSTING_ENABLED:
            return _line_blocked(entry.id, "direto_desligado", _DIRECT_DISABLED_MSG)
        if _process_of(session) == CardPostingDateMode.INVOICE_DUE_DATE.value:
            # O `IncluirLancCC` grava `dDtLanc` = data da COMPRA; o cliente que
            # lança a fatura no vencimento não usa essa data (86e3n70p0). A
            # provisão, que põe o vencimento no título, é o caminho dele.
            return _line_blocked(
                entry.id,
                "direto_indisponivel_no_modo_vencimento",
                DIRECT_IN_DUE_DATE_PROCESS_MESSAGE,
            )
        if existing is not None and existing.kind == OmiePostingKind.PROVISAO.value:
            if existing.status == OmiePostingStatus.CONFIRMED.value:
                return _provision_already_done(existing)
            if not _kind_switch_allowed(existing):
                return _line_blocked(entry.id, "modo_divergente", _MODE_LOCKED_MSG)
            existing = await self._repo.reset_kind(
                client_id=session.client_id,
                file_entry_id=entry.id,
                kind=OmiePostingKind.DIRETO,
            )
            await self._db.commit()
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

        if line.cod_categoria is None:  # pragma: no cover  -- o schema exige no `direto`
            raise ValueError("lançamento direto sem cod_categoria")
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
    # PROVISÃO no Contas a Pagar (86e3n70q0, bloco B)
    # ------------------------------------------------------------------

    async def _provision(
        self,
        *,
        session: ReconciliationSession,
        client: Client,
        line: OmiePostingLineRequest,
        entry: ReconciliationFileEntry,
        existing: ReconciliationOmiePosting | None,
        tally: _ProvisionTally,
    ) -> OmiePostingLineResult:
        """Fornecedor → título → baixa (só no processo "na data da compra").

        Tudo o que pode recusar a linha SEM escrever no Omie vem antes de
        qualquer chamada: flag, categorias que não fecham, rateio obrigatório
        ausente, linha já lançada, modo travado e o teto de reenvios.
        """
        if not self._settings.OMIE_PAYABLE_POSTING_ENABLED:
            return _line_blocked(entry.id, "provisao_desligada", _PROVISION_DISABLED_MSG)
        total = abs(Decimal(entry.amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
        categorias = _category_split(line, total)
        if categorias is None:
            return _line_blocked(entry.id, "categorias_nao_fecham", _CATEGORIES_MISMATCH_MSG)
        if client.omie_department_required and not line.departamentos:
            return _line_blocked(entry.id, "departamento_pendente", _DEPARTMENT_REQUIRED_MSG)
        # 86e3qhhtb — a parcela é lida pelo SERVIDOR (colunas da extração ou,
        # sem elas, a descrição decifrada), nunca do pedido.
        installment = entry_installment(entry, self._describe(entry))
        if line.parcelas_futuras:
            if not self._settings.OMIE_INSTALLMENT_POSTING_ENABLED:
                return _line_blocked(
                    entry.id, "parcelas_futuras_desligadas", _INSTALLMENTS_DISABLED_MSG
                )
            if installment is None or not installment.has_future:
                return _line_blocked(entry.id, "sem_parcelas_futuras", _NO_FUTURE_MSG)

        if existing is not None:
            if existing.status == OmiePostingStatus.CONFIRMED.value:
                return await self._after_provision(
                    session=session,
                    line=line,
                    entry=entry,
                    posting=existing,
                    installment=installment,
                    categorias=categorias,
                    tally=tally,
                    already_done=True,
                )
            if existing.kind == OmiePostingKind.DIRETO.value:
                if not _kind_switch_allowed(existing):
                    return _line_blocked(entry.id, "modo_divergente", _MODE_LOCKED_MSG)
                existing = await self._repo.reset_kind(
                    client_id=session.client_id,
                    file_entry_id=entry.id,
                    kind=OmiePostingKind.PROVISAO,
                )
                await self._db.commit()
            elif (
                existing.status == OmiePostingStatus.PENDING.value
                and existing.attempts >= MAX_STEP_SENDS
            ):
                return _provision_result(
                    existing,
                    status="bloqueada",
                    reason="envio_anterior_sem_confirmacao",
                    message=_stuck_message(existing, entry.id),
                )
            posting = existing
        else:
            try:
                posting = await self._repo.register_intent(
                    client_id=session.client_id,
                    session_id=session.id,
                    file_entry_id=entry.id,
                    kind=OmiePostingKind.PROVISAO,
                )
            except OmiePostingKeyCollisionError as exc:
                return _line_blocked(entry.id, "chave_em_conflito", exc.user_message)
            await self._db.commit()

        omie_client = await self._factory()
        try:
            while posting.step != OmiePostingStep.CONCLUIDO.value:
                outcome = await self._run_step(
                    session=session,
                    client=client,
                    line=line,
                    entry=entry,
                    posting=posting,
                    omie=omie_client,
                    total=total,
                    categorias=categorias,
                    installment=installment,
                    tally=tally,
                )
                if outcome is not None:
                    return outcome
        finally:
            await omie_client.aclose()

        await self._conclude_provision(session=session, entry=entry, posting=posting)
        return await self._after_provision(
            session=session,
            line=line,
            entry=entry,
            posting=posting,
            installment=installment,
            categorias=categorias,
            tally=tally,
            already_done=False,
        )

    async def _run_step(
        self,
        *,
        session: ReconciliationSession,
        client: Client,
        line: OmiePostingLineRequest,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        omie: OmieClient,
        total: Decimal,
        categorias: list[tuple[str, Decimal]],
        installment: Installment | None,
        tally: _ProvisionTally,
    ) -> OmiePostingLineResult | None:
        """Executa o passo CORRENTE e avança. `None` = seguiu; resultado = parou."""
        step = posting.step
        if step == OmiePostingStep.FORNECEDOR.value:
            return await self._step_supplier(
                session=session,
                client=client,
                line=line,
                entry=entry,
                posting=posting,
                omie=omie,
                tally=tally,
            )
        if step == OmiePostingStep.TITULO.value:
            return await self._step_payable(
                session=session,
                line=line,
                entry=entry,
                posting=posting,
                omie=omie,
                total=total,
                categorias=categorias,
                installment=installment,
                tally=tally,
            )
        if step == OmiePostingStep.BAIXA.value:
            return await self._step_payment(
                session=session,
                entry=entry,
                posting=posting,
                omie=omie,
                total=total,
                tally=tally,
            )
        raise ValueError(f"passo desconhecido na intenção {posting.id}: {step!r}")

    async def _step_supplier(
        self,
        *,
        session: ReconciliationSession,
        client: Client,
        line: OmiePostingLineRequest,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        omie: OmieClient,
        tally: _ProvisionTally,
    ) -> OmiePostingLineResult | None:
        """Quem recebe o título: o cadastro escolhido, o do CNPJ, um novo, ou o padrão.

        86e3qhhrd: o `codigo_fornecedor` da linha é o cadastro que a pessoa escolheu
        na tela (busca pelo nome, sugestão ou CNPJ) e vence tudo, sem nova ida à
        Omie: o título vai para quem ela viu, inclusive fornecedor do exterior sem
        CNPJ. Como o fornecedor padrão, o código não é reconferido aqui; um código
        que sumiu falha no título com a mensagem da Omie.

        Depois, nessa ordem (bloco 0, item e): CNPJ com cadastro → ele; sem cadastro e com
        razão social e e-mail na linha → cadastra pela chave do CNPJ (idempotente:
        o mesmo fornecedor em duas linhas é o MESMO cadastro); senão o fornecedor
        padrão do cliente; senão a linha bloqueia. Nenhum nome, CNPJ ou e-mail é
        gravado aqui — só o código que a Omie devolve.
        """
        code: int | None = line.codigo_fornecedor
        cnpj = line.cnpj_fornecedor
        if code is None and cnpj is not None:
            try:
                cadastros = await omie.listar_clientes_por_cnpj(cnpj_cpf=format_cnpj(cnpj))
            except OmieFaultError as exc:
                return await self._step_fault(
                    session=session, entry=entry, posting=posting, exc=exc
                )
            except (OmieTimeoutError, OmieServerError, OmieAuthError) as exc:
                raise _DependencyFailureError(
                    _provision_result(
                        posting, status="erro", reason="omie_indisponivel", message=exc.user_message
                    ),
                    exc,
                ) from exc
            chosen = pick_supplier(cadastros)
            if chosen is not None:
                code = chosen.codigo_cliente_omie
            elif line.razao_social is not None and line.email is not None:
                request = IncluirClienteRequest(
                    codigo_cliente_integracao=derive_supplier_key(cnpj),
                    razao_social=line.razao_social,
                    # Mesma forma do request ACEITO na captura (com nome fantasia).
                    nome_fantasia=line.razao_social,
                    cnpj_cpf=format_cnpj(cnpj),
                    email=line.email,
                )
                response = await self._send_step(
                    session=session,
                    entry=entry,
                    posting=posting,
                    call=lambda: omie.incluir_cliente(request),
                )
                if isinstance(response, OmiePostingLineResult):
                    return response
                code = response.codigo_cliente_omie
                tally.fornecedores_criados += 1
        if code is None:
            code = client.omie_default_supplier_code
        if code is None:
            return _provision_result(
                posting,
                status="bloqueada",
                reason="fornecedor_pendente",
                message=_SUPPLIER_PENDING_MSG,
            )
        await self._repo.record_provision_step(
            client_id=session.client_id,
            file_entry_id=entry.id,
            next_step=OmiePostingStep.TITULO,
            supplier_code=code,
        )
        await self._db.commit()
        return None

    async def _step_payable(
        self,
        *,
        session: ReconciliationSession,
        line: OmiePostingLineRequest,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        omie: OmieClient,
        total: Decimal,
        categorias: list[tuple[str, Decimal]],
        installment: Installment | None,
        tally: _ProvisionTally,
    ) -> OmiePostingLineResult | None:
        """O título a pagar. Emissão = data da compra; vencimento = o da fatura.

        Sem vencimento gravado na sessão (processo "na data da compra" com fatura
        que não o trouxe), o vencimento é a própria data da compra — a baixa sai
        no mesmo dia, então o título nunca fica "a vencer" num dia inventado.
        """
        if posting.supplier_code is None:  # pragma: no cover  -- CHECK do banco
            raise ValueError(f"intenção {posting.id} no título sem fornecedor")
        if installment is not None and installment.number >= 2:
            # 86e3qhhtb — a parcela desta linha pode já estar no Omie, criada pela
            # fatura anterior. ENCONTRAR é o que impede o título duplicado.
            found = await self._claim_series_installment(
                session=session,
                entry=entry,
                posting=posting,
                total=total,
                installment=installment,
                tally=tally,
            )
            if found is True:
                return None
            if found is not None:
                return found
        due = session.invoice_due_date or entry.transaction_date
        single = categorias[0][0] if len(categorias) == 1 else None
        request = IncluirContaPagarRequest(
            codigo_lancamento_integracao=derive_payable_key(entry.id),
            codigo_cliente_fornecedor=posting.supplier_code,
            data_emissao=entry.transaction_date.strftime(_OMIE_DATE),
            data_vencimento=due.strftime(_OMIE_DATE),
            data_previsao=due.strftime(_OMIE_DATE),
            valor_documento=total,
            codigo_categoria=single,
            categorias=(
                None
                if single is not None
                else [
                    ContaPagarCategoria(
                        codigo_categoria=codigo,
                        valor=valor,
                        percentual=percentual,
                    )
                    for (codigo, valor), percentual in zip(
                        categorias, _percentuals([v for _, v in categorias], total), strict=True
                    )
                ]
            ),
            id_conta_corrente=session.omie_conta_id,
            observacao=self._describe(entry),
            distribuicao=_department_split(line, total),
        )
        response = await self._send_step(
            session=session,
            entry=entry,
            posting=posting,
            call=lambda: omie.incluir_conta_pagar(request),
        )
        if isinstance(response, OmiePostingLineResult):
            return response
        tally.titulos_criados += 1
        next_step = (
            OmiePostingStep.CONCLUIDO
            if _process_of(session) == CardPostingDateMode.INVOICE_DUE_DATE.value
            else OmiePostingStep.BAIXA
        )
        await self._repo.record_provision_step(
            client_id=session.client_id,
            file_entry_id=entry.id,
            next_step=next_step,
            payable_id=response.codigo_lancamento_omie,
        )
        await self._db.commit()
        return None

    async def _step_payment(
        self,
        *,
        session: ReconciliationSession,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        omie: OmieClient,
        total: Decimal,
        tally: _ProvisionTally,
    ) -> OmiePostingLineResult | None:
        """A baixa na conta do cartão, valor total, na data da compra.

        Só existe no processo "na data da compra": `_step_payable` leva o
        processo "vencimento da fatura" direto a `concluido`.
        """
        if posting.payable_id is None:  # pragma: no cover  -- CHECK do banco
            raise ValueError(f"intenção {posting.id} na baixa sem título")
        request = LancarPagamentoRequest(
            codigo_lancamento=posting.payable_id,
            codigo_baixa_integracao=derive_payment_key(entry.id),
            codigo_conta_corrente=session.omie_conta_id,
            valor=total,
            data=entry.transaction_date.strftime(_OMIE_DATE),
        )
        response = await self._send_step(
            session=session,
            entry=entry,
            posting=posting,
            call=lambda: omie.lancar_pagamento(request),
        )
        if isinstance(response, OmiePostingLineResult):
            return response
        tally.baixas_criadas += 1
        await self._repo.record_provision_step(
            client_id=session.client_id,
            file_entry_id=entry.id,
            next_step=OmiePostingStep.CONCLUIDO,
            payment_id=response.codigo_baixa,
        )
        await self._db.commit()
        return None

    async def _send_step[R](
        self,
        *,
        session: ReconciliationSession,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        call: Callable[[], Awaitable[R]],
    ) -> R | OmiePostingLineResult:
        """Um envio de ESCRITA de um passo: conta antes, e trata o desfecho.

        A tentativa é contada e COMMITADA antes do POST, como no `direto`: se a
        resposta nunca chegar, a próxima execução sabe que houve envio e quantos.
        Timeout/5xx sobem como falha de dependência (o lote para), com o passo e
        os ids no resultado; o reenvio na próxima execução é seguro pela chave.
        """
        await self._repo.increment_attempts(client_id=session.client_id, file_entry_id=entry.id)
        await self._db.commit()
        try:
            return await call()
        except OmieFaultError as exc:
            return await self._step_fault(session=session, entry=entry, posting=posting, exc=exc)
        except (OmieTimeoutError, OmieServerError, OmieAuthError) as exc:
            log.info(
                "omie_provision_step_unconfirmed",
                session_id=str(session.id),
                file_entry_id=str(entry.id),
                step=posting.step,
                attempts=posting.attempts,
            )
            raise _DependencyFailureError(
                _provision_result(
                    posting, status="erro", reason="omie_indisponivel", message=exc.user_message
                ),
                exc,
            ) from exc

    async def _step_fault(
        self,
        *,
        session: ReconciliationSession,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        exc: OmieFaultError,
    ) -> OmiePostingLineResult:
        """`faultstring`: o passo NÃO criou nada. Guarda a mensagem, nunca a loga."""
        await self._repo.mark_failed(
            client_id=session.client_id,
            file_entry_id=entry.id,
            error_code=exc.code.value,
            error_message=exc.user_message,
            reset_attempts=True,
        )
        await self._db.commit()
        omie_code = omie_error_code(exc)
        log.info(
            "omie_provision_step_failed",
            session_id=str(session.id),
            file_entry_id=str(entry.id),
            step=posting.step,
            omie_code=omie_code,
        )
        await self._events.emit_omie_lancamento_rejeitado(
            session_id=session.id,
            codigo="OMIE_FAULT",
            fault_message=exc.user_message,
        )
        reason: PostingLineReason = (
            "competencia_fechada" if omie_code == OMIE_CODE_CLOSED_COMPETENCE else "erro_omie"
        )
        return _provision_result(posting, status="erro", reason=reason, message=exc.user_message)

    # ------------------------------------------------------------------
    # Parcelas FUTURAS de compra parcelada (86e3qhhtb)
    # ------------------------------------------------------------------

    async def _claim_series_installment(
        self,
        *,
        session: ReconciliationSession,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        total: Decimal,
        installment: Installment,
        tally: _ProvisionTally,
    ) -> OmiePostingLineResult | Literal[True] | None:
        """A fatura seguinte ENCONTRA a parcela já provisionada, em vez de criar outra.

        `True`: encontrou — a intenção avança com o título EXISTENTE (no processo
        "na data da compra", só a baixa será lançada). Resultado: a parcela está
        registrada, mas o título dela ainda não foi confirmado; criar outro aqui
        poderia duplicar, então a linha para. `None`: não há parcela registrada
        com esta identidade, e a linha cria o próprio título.

        Mesma identidade = mesmo cliente, cartão, fornecedor, valor e `N/M`, com o
        vencimento perto da data desta parcela. Perder a corrida para outra linha
        (duas compras iguais na mesma fatura) só faz procurar a próxima.
        """
        if posting.supplier_code is None:  # pragma: no cover  -- CHECK do banco
            return None
        anchor = (
            await invoice_anchor(self._db, session.id, session.invoice_due_date)
            or entry.transaction_date
        )
        around = current_installment_date(installment, entry.transaction_date, anchor)
        for _ in range(_CLAIM_RETRIES):
            found = await self._installments.find_in_series(
                client_id=session.client_id,
                omie_conta_id=session.omie_conta_id,
                supplier_code=posting.supplier_code,
                amount=total,
                number=installment.number,
                total=installment.total,
                around=around,
                # Uma fatura não traz a parcela 7 e a 8 da MESMA compra: a série
                # criada nesta conciliação nunca é a desta linha.
                exclude_session_id=session.id,
            )
            if found is None:
                return None
            if found.step != InstallmentStep.CONCLUIDO.value or found.payable_id is None:
                return _provision_result(
                    posting,
                    status="bloqueada",
                    reason="parcela_pendente_na_fatura_anterior",
                    message=_PENDING_IN_PREVIOUS_MSG.format(
                        n=installment.number, m=installment.total, key=found.payable_key
                    ),
                )
            claimed = await self._installments.claim(
                client_id=session.client_id, installment_id=found.id, file_entry_id=entry.id
            )
            if claimed is None or claimed.payable_id is None:
                continue
            await self._repo.record_provision_step(
                client_id=session.client_id,
                file_entry_id=entry.id,
                next_step=(
                    OmiePostingStep.CONCLUIDO
                    if _process_of(session) == CardPostingDateMode.INVOICE_DUE_DATE.value
                    else OmiePostingStep.BAIXA
                ),
                payable_id=claimed.payable_id,
            )
            await self._db.commit()
            tally.parcelas_encontradas += 1
            log.info(
                "omie_installment_claimed",
                session_id=str(session.id),
                file_entry_id=str(entry.id),
                installment_id=str(claimed.id),
            )
            return True
        return None

    async def _after_provision(
        self,
        *,
        session: ReconciliationSession,
        line: OmiePostingLineRequest,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        installment: Installment | None,
        categorias: list[tuple[str, Decimal]],
        tally: _ProvisionTally,
        already_done: bool,
    ) -> OmiePostingLineResult:
        """O resultado da linha com o título dela feito: as parcelas futuras, se houver.

        Três casos. A linha que ENCONTROU a parcela de uma série anterior é
        `ja_lancada` com o título dessa série, e as parcelas seguintes dela já
        existem (a série tem um dono só: esta linha nunca as cria). A linha que
        pediu as parcelas, ou que já tem parcelas registradas (retomada), as
        provisiona agora. O resto é o resultado de sempre.
        """
        claimed = await self._installments.claimed_by(
            client_id=session.client_id, file_entry_id=entry.id
        )
        if claimed is not None:
            siblings = [
                row
                for row in await self._installments.list_for_posting(
                    client_id=session.client_id, posting_id=claimed.posting_id
                )
                if row.installment_number > claimed.installment_number
            ]
            return _provision_result(
                posting,
                # Reenviar a linha que já encontrou a parcela não é lançamento novo.
                status="bloqueada" if already_done else "lancada",
                reason="ja_lancada",
                message=_CLAIMED_MSG.format(
                    n=claimed.installment_number,
                    m=claimed.installment_total,
                    payable_id=claimed.payable_id,
                ),
            ).model_copy(
                update={
                    "parcelas": [_installment_result(r, "ja_existia") for r in siblings] or None
                }
            )

        own = await self._installments.list_for_posting(
            client_id=session.client_id, posting_id=posting.id
        )
        if not (line.parcelas_futuras or own):
            if already_done:
                return _provision_already_done(posting)
            return _provision_result(posting, status="lancada")
        pending = [r for r in own if r.step != InstallmentStep.CONCLUIDO.value]
        if pending and not self._settings.OMIE_INSTALLMENT_POSTING_ENABLED:
            # O kill-switch vale também para RETOMAR a série: desligar a flag (o
            # rollback da §3.16) tem de parar toda escrita de parcela.
            return _provision_result(
                posting,
                status="bloqueada",
                reason="parcelas_futuras_desligadas",
                message=_INSTALLMENTS_DISABLED_MSG,
            ).model_copy(
                update={
                    "parcelas": [
                        _installment_result(
                            r,
                            "provisionada"
                            if r.step == InstallmentStep.CONCLUIDO.value
                            else "pendente",
                        )
                        for r in own
                    ]
                }
            )

        before = tally.parcelas_futuras
        parcelas = await self._run_future_installments(
            session=session,
            line=line,
            entry=entry,
            posting=posting,
            installment=installment,
            categorias=categorias,
            own=own,
            tally=tally,
        )
        if any(p.status in ("pendente", "erro") for p in parcelas):
            return _provision_result(
                posting,
                status="erro",
                reason="parcelas_incompletas",
                message=_INCOMPLETE_MSG,
            ).model_copy(update={"parcelas": parcelas})
        if already_done and tally.parcelas_futuras == before:
            return _provision_already_done(posting).model_copy(update={"parcelas": parcelas})
        return _provision_result(posting, status="lancada").model_copy(
            update={"parcelas": parcelas}
        )

    async def _run_future_installments(
        self,
        *,
        session: ReconciliationSession,
        line: OmiePostingLineRequest,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        installment: Installment | None,
        categorias: list[tuple[str, Decimal]],
        own: list[ReconciliationOmiePostingInstallment],
        tally: _ProvisionTally,
    ) -> list[OmiePostingInstallmentResult]:
        """Registra (antes de enviar) e provisiona os títulos das parcelas `N+1` a `M`.

        Mesma disciplina do título da linha: a parcela é REGISTRADA e COMMITADA
        antes do envio, a tentativa é contada antes do POST, a chave é derivada da
        linha e do número (a Omie devolve o mesmo título no 2º envio), `faultstring`
        não cria nada e o timeout reenvia com a MESMA chave até `MAX_STEP_SENDS`.

        Parcela que já existe por OUTRA linha com a mesma identidade (a mesma compra
        provisionada noutra conciliação) não é criada: volta `ja_existia`.
        """
        if posting.supplier_code is None:  # pragma: no cover  -- CHECK do banco
            raise ValueError(f"intenção {posting.id} com parcelas sem fornecedor")
        by_number = {row.installment_number: row for row in own}
        elsewhere: dict[int, OmiePostingInstallmentResult] = {}
        if installment is not None and installment.has_future:
            anchor = (
                await invoice_anchor(self._db, session.id, session.invoice_due_date)
                or entry.transaction_date
            )
            plan = plan_future_installments(
                installment=installment,
                line_date=entry.transaction_date,
                invoice_anchor=anchor,
                amount=abs(Decimal(entry.amount)).quantize(_CENT, rounding=ROUND_HALF_UP),
                process=_process_of(session),
                invoice_due_date=session.invoice_due_date,
            )
            for planned in plan:
                if planned.number in by_number:
                    continue
                other = await self._installments.find_in_series(
                    client_id=session.client_id,
                    omie_conta_id=session.omie_conta_id,
                    supplier_code=posting.supplier_code,
                    amount=planned.amount,
                    number=planned.number,
                    total=planned.total,
                    around=planned.due_date,
                    exclude_posting_id=posting.id,
                    # Duas compras idênticas na MESMA fatura são duas séries (§3.16).
                    exclude_session_id=session.id,
                )
                if other is not None:
                    elsewhere[planned.number] = _installment_result(other, "ja_existia")
                    continue
                by_number[planned.number] = await self._installments.register(
                    client_id=session.client_id,
                    posting_id=posting.id,
                    file_entry_id=entry.id,
                    omie_conta_id=session.omie_conta_id,
                    supplier_code=posting.supplier_code,
                    planned=planned,
                )
            # Barreira de durabilidade: a parcela existe na memória do ADL ANTES de
            # qualquer envio, como a intenção da linha.
            await self._db.commit()

        results: dict[int, OmiePostingInstallmentResult] = dict(elsewhere)
        to_send = [
            row
            for row in sorted(by_number.values(), key=lambda r: r.installment_number)
            if row.step != InstallmentStep.CONCLUIDO.value
        ]
        for row in by_number.values():
            if row.step == InstallmentStep.CONCLUIDO.value:
                results[row.installment_number] = _installment_result(row, "provisionada")
        if to_send:
            omie_client = await self._factory()
            try:
                for row in to_send:
                    results[row.installment_number] = await self._send_installment(
                        session=session,
                        line=line,
                        entry=entry,
                        posting=posting,
                        row=row,
                        categorias=categorias,
                        omie=omie_client,
                        tally=tally,
                        done_so_far=[results[k] for k in sorted(results)],
                    )
            finally:
                await omie_client.aclose()
        return [results[k] for k in sorted(results)]

    async def _send_installment(
        self,
        *,
        session: ReconciliationSession,
        line: OmiePostingLineRequest,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
        row: ReconciliationOmiePostingInstallment,
        categorias: list[tuple[str, Decimal]],
        omie: OmieClient,
        tally: _ProvisionTally,
        done_so_far: list[OmiePostingInstallmentResult],
    ) -> OmiePostingInstallmentResult:
        """O título de UMA parcela futura: conta antes, envia, grava o id e commita."""
        if row.attempts >= MAX_STEP_SENDS:
            return _installment_result(
                row,
                "pendente",
                message=_INSTALLMENT_STUCK_MSG.format(attempts=row.attempts, key=row.payable_key),
            )
        scaled = _scale_categories(categorias, row.amount)
        single = scaled[0][0] if len(scaled) == 1 else None
        description = self._describe(entry)
        observacao = _INSTALLMENT_NOTE.format(
            description=description or "Compra parcelada",
            n=row.installment_number,
            m=row.installment_total,
        )
        request = IncluirContaPagarRequest(
            codigo_lancamento_integracao=row.payable_key,
            codigo_cliente_fornecedor=row.supplier_code,
            data_emissao=row.issue_date.strftime(_OMIE_DATE),
            data_vencimento=row.due_date.strftime(_OMIE_DATE),
            data_previsao=row.issue_date.strftime(_OMIE_DATE),
            valor_documento=row.amount,
            codigo_categoria=single,
            categorias=(
                None
                if single is not None
                else [
                    ContaPagarCategoria(codigo_categoria=codigo, valor=valor, percentual=pct)
                    for (codigo, valor), pct in zip(
                        scaled, _percentuals([v for _, v in scaled], row.amount), strict=True
                    )
                ]
            ),
            id_conta_corrente=row.omie_conta_id,
            observacao=observacao[:_OBSERVACAO_MAX],
            distribuicao=_department_split(line, row.amount),
        )
        await self._installments.increment_attempts(
            client_id=session.client_id, installment_id=row.id
        )
        await self._db.commit()
        try:
            response = await omie.incluir_conta_pagar(request)
        except OmieFaultError as exc:
            await self._installments.mark_failed(
                client_id=session.client_id,
                installment_id=row.id,
                error_code=exc.code.value,
                error_message=exc.user_message,
            )
            await self._db.commit()
            log.info(
                "omie_installment_failed",
                session_id=str(session.id),
                file_entry_id=str(entry.id),
                installment_number=row.installment_number,
                omie_code=omie_error_code(exc),
            )
            await self._events.emit_omie_lancamento_rejeitado(
                session_id=session.id, codigo="OMIE_FAULT", fault_message=exc.user_message
            )
            return _installment_result(row, "erro", message=exc.user_message)
        except (OmieTimeoutError, OmieServerError, OmieAuthError) as exc:
            log.info(
                "omie_installment_unconfirmed",
                session_id=str(session.id),
                file_entry_id=str(entry.id),
                installment_number=row.installment_number,
            )
            partial = [*done_so_far, _installment_result(row, "pendente", message=exc.user_message)]
            raise _DependencyFailureError(
                _provision_result(
                    posting, status="erro", reason="omie_indisponivel", message=exc.user_message
                ).model_copy(update={"parcelas": partial}),
                exc,
            ) from exc
        await self._installments.mark_done(
            client_id=session.client_id,
            installment_id=row.id,
            payable_id=response.codigo_lancamento_omie,
        )
        await self._db.commit()
        tally.parcelas_futuras += 1
        row.step = InstallmentStep.CONCLUIDO.value
        row.payable_id = response.codigo_lancamento_omie
        return _installment_result(row, "provisionada")

    async def _conclude_provision(
        self,
        *,
        session: ReconciliationSession,
        entry: ReconciliationFileEntry,
        posting: ReconciliationOmiePosting,
    ) -> None:
        """Fecha a anomalia `missing_in_omie` com os ids do título e da baixa.

        A LINHA da conciliação não muda de situação: ela continua `sem_omie` e sem
        `omie_lancamento_id`, porque o lançamento que a casaria no extrato (a
        baixa, ou nada, no processo do vencimento) tem um id que a Omie não
        devolve e só aparece no extrato depois (a captura levou 20 s). Inventar o
        vínculo seria afirmar um casamento que ninguém viu; reprocessar a
        conciliação encontra o par de verdade. Quem diz "já lançada" é a intenção
        `confirmed`, que bloqueia o segundo envio.
        """
        note = (
            _PROVISION_PAID_NOTE.format(
                payable_id=posting.payable_id, payment_id=posting.payment_id
            )
            if posting.payment_id is not None
            else _PROVISION_NOTE.format(payable_id=posting.payable_id)
        )
        await self._resolve_missing_in_omie(
            session_id=session.id, file_entry_id=entry.id, note=note
        )
        # Barreira de durabilidade, como no `_confirm` do direto.
        await self._db.commit()

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

        Formato ANINHADO (`cCodIntLanc` no topo + `cabecalho`/`detalhes`),
        corroborado pela recusa real do formato plano em 21/08/2026 — ver
        `IncluirLancCCRequest`. O contrato de escrita NÃO tem campo de sinal:
        `nValorLanc` vai sempre **absoluto**, e só COMPRA (valor negativo no
        extrato) chega aqui — estorno é bloqueado antes, em
        `_eligibility_block`, até a direção do crédito ser verificada.
        """
        amount = Decimal(entry.amount)
        valor = abs(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return IncluirLancCCRequest(
            c_cod_int_lanc=cod_int_lanc,
            cabecalho=LancCCCabecalho(
                n_cod_cc=session.omie_conta_id,
                d_dt_lanc=entry.transaction_date.strftime("%d/%m/%Y"),
                n_valor_lanc=valor,
            ),
            detalhes=LancCCDetalhes(
                c_cod_categ=cod_categoria,
                c_obs=self._describe(entry),
                c_tipo=_C_TIPO_DOCUMENTO,
            ),
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
            note=_RESOLUTION_NOTE.format(omie_lancamento_id=omie_lancamento_id),
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
        note: str,
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

#: Lançamento DIRETO num processo "vencimento da fatura" (86e3n70q0, bloco B). A
#: provisão, que põe o vencimento da fatura no título, é o caminho desse processo.
DIRECT_IN_DUE_DATE_PROCESS_MESSAGE = (
    "Esta conciliação cruza as compras pelo vencimento da fatura: o lançamento direto "
    "gravaria a data da compra. Provisione a compra no Contas a Pagar."
)

_DIRECT_DISABLED_MSG = (
    "O lançamento direto no Omie está desativado neste ambiente. Provisione a compra "
    "no Contas a Pagar."
)
_PROVISION_DISABLED_MSG = (
    "O lançamento como Contas a Pagar está desativado neste ambiente. Fale com o suporte."
)
_CATEGORIES_MISMATCH_MSG = (
    "A soma das categorias não fecha com o valor da compra. Ajuste os valores e repita."
)
_DEPARTMENT_REQUIRED_MSG = (
    "Este cliente exige rateio por departamento. Escolha os departamentos da compra."
)
_SUPPLIER_PENDING_MSG = (
    "Fornecedor não definido: informe o CNPJ (e, se ele não tiver cadastro no Omie, "
    "razão social e e-mail) ou configure um fornecedor padrão no cliente."
)
_MODE_LOCKED_MSG = (
    "Esta compra já teve um envio ao Omie na outra forma de lançamento. Continue por ela, "
    "ou confira no Omie antes: trocar agora poderia lançar a mesma compra duas vezes."
)

_INSTALLMENTS_DISABLED_MSG = (
    "A provisão das parcelas futuras está desativada neste ambiente. Provisione só a "
    "compra desta fatura (sem as parcelas) ou fale com o suporte."
)
_NO_FUTURE_MSG = (
    "Esta compra não é uma parcela com outras depois dela: não há parcelas futuras a provisionar."
)
_PENDING_IN_PREVIOUS_MSG = (
    "A parcela {n}/{m} desta compra já foi registrada por uma fatura anterior, mas o título "
    "dela ainda não foi confirmado no Omie (chave {key}). Termine o lançamento naquela "
    "conciliação antes: criar outro título aqui poderia duplicar."
)
_CLAIMED_MSG = (
    "A parcela {n}/{m} já estava provisionada no Omie por uma fatura anterior (título nº "
    "{payable_id}): nenhum título novo foi criado."
)
_INCOMPLETE_MSG = (
    "O título da compra está no Omie, mas alguma parcela futura não entrou. Tentar de novo "
    "continua delas, sem recriar o que já existe."
)
_INSTALLMENT_STUCK_MSG = (
    "Houve {attempts} envios sem confirmação do Omie (título {key}). Confira no Omie antes "
    "de tentar de novo."
)
#: Observação do título da parcela futura: a compra e QUAL parcela é. A descrição da
#: linha diz `7/10`; sem o número a parcela 8 pareceria a 7 (§4.5: vai só ao Omie).
_INSTALLMENT_NOTE = "{description} (parcela {n} de {m})"
#: `observacao` do `IncluirContaPagar` (`text`, sem teto documentado); limitamos
#: para o título nunca carregar um texto inteiro de extrato.
_OBSERVACAO_MAX = 500
#: Quantas vezes a linha procura de novo quando perde a parcela para outra linha.
_CLAIM_RETRIES = 3

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)

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
    if Decimal(entry.amount) > 0:
        # Estorno. O contrato real de escrita não tem `cNatureza` e a
        # representação do crédito segue não-verificada (S-1) — lançar no
        # palpite poderia registrar o estorno como uma segunda despesa.
        return _line_blocked(entry.id, "estorno_nao_verificado", _ESTORNO_BLOCK_MESSAGE)
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


# ----------------------------------------------------------------------
# Provisão: helpers puros (86e3n70q0, bloco B)
# ----------------------------------------------------------------------


@dataclass(slots=True)
class _ProvisionTally:
    """Contagens do lote para o evento `lancamento_omie_provisao` (só números)."""

    linhas: int = 0
    concluidas: int = 0
    parciais: int = 0
    bloqueadas: int = 0
    com_erro: int = 0
    fornecedores_criados: int = 0
    titulos_criados: int = 0
    baixas_criadas: int = 0
    #: 86e3qhhtb: títulos de parcelas futuras CRIADOS neste lote.
    parcelas_futuras: int = 0
    #: 86e3qhhtb: linhas que ENCONTRARAM a parcela provisionada por fatura anterior.
    parcelas_encontradas: int = 0

    @property
    def wrote_anything(self) -> bool:
        """Algum passo ESCREVEU no Omie neste lote (fornecedor, título, baixa ou parcela)."""
        return bool(
            self.fornecedores_criados
            or self.titulos_criados
            or self.baixas_criadas
            or self.parcelas_futuras
        )

    def count(self, result: OmiePostingLineResult) -> None:
        self.linhas += 1
        if result.status == "lancada":
            self.concluidas += 1
        elif result.status == "bloqueada":
            self.bloqueadas += 1
        else:
            self.com_erro += 1
        if result.status != "lancada" and result.payable_id is not None:
            # Título criado e a linha parou depois dele: sucesso PARCIAL.
            self.parciais += 1


def _process_of(session: ReconciliationSession) -> str:
    """O processo do cartão da sessão. Sessão antiga (NULL) é o de sempre."""
    return session.card_posting_date_mode or CardPostingDateMode.PURCHASE_DATE.value


def _kind_switch_allowed(existing: ReconciliationOmiePosting) -> bool:
    """Trocar de forma só quando NENHUM dinheiro pode ter sido escrito.

    - provisão ainda no passo `fornecedor`: no máximo um cadastro de fornecedor
      existiu, nunca título nem baixa;
    - direto recusado por `faultstring` na ÚNICA tentativa: a Omie disse "não"
      ao único envio. (`failed` por vínculo em conflito NÃO serve: ali o
      lançamento entrou no Omie.)
    """
    if existing.status == OmiePostingStatus.CONFIRMED.value:
        return False
    if existing.kind == OmiePostingKind.PROVISAO.value:
        return existing.step == OmiePostingStep.FORNECEDOR.value
    return (
        existing.status == OmiePostingStatus.FAILED.value
        and existing.attempts == 1
        and existing.error_code == ErrorCode.OMIE_FAULT.value
        and existing.omie_lancamento_id is None
    )


def _provision_result(
    posting: ReconciliationOmiePosting,
    *,
    status: PostingLineStatus,
    reason: PostingLineReason | None = None,
    message: str | None = None,
) -> OmiePostingLineResult:
    """Resultado de uma linha de provisão: o passo onde está e os ids gravados."""
    return OmiePostingLineResult(
        file_entry_id=posting.file_entry_id,
        status=status,
        reason=reason,
        message=message,
        kind="provisao",
        step=OmiePostingStep(posting.step).value if posting.step else None,
        supplier_code=posting.supplier_code,
        payable_id=posting.payable_id,
        payment_id=posting.payment_id,
    )


def _provision_already_done(existing: ReconciliationOmiePosting) -> OmiePostingLineResult:
    return _provision_result(
        existing,
        status="bloqueada",
        reason="ja_lancada",
        message=f"Esta compra já foi provisionada no Omie (título nº {existing.payable_id}).",
    )


def _stuck_message(existing: ReconciliationOmiePosting, file_entry_id: UUID) -> str:
    """O passo parado e a chave para achar no Omie. Sem nome, só a chave."""
    key = {
        OmiePostingStep.TITULO.value: f" (título {derive_payable_key(file_entry_id)})",
        OmiePostingStep.BAIXA.value: f" (baixa {derive_payment_key(file_entry_id)})",
    }.get(existing.step or "", "")
    return (
        f"Houve {existing.attempts} envios sem confirmação do Omie no passo "
        f"{existing.step}{key}. Confira no Omie antes de tentar de novo."
    )


def _category_split(
    line: OmiePostingLineRequest, total: Decimal
) -> list[tuple[str, Decimal]] | None:
    """As categorias do título com valores que somam EXATAMENTE o da compra.

    Uma categoria só (`cod_categoria`) leva o valor inteiro. Com `categorias`, a
    soma tem de fechar com a compra dentro de 0,01; o centavo de diferença vai
    para a última categoria, para o título nunca ter valor diferente da compra.
    `None` = não fecha.
    """
    if line.categorias is None:
        if line.cod_categoria is None:  # pragma: no cover  -- o schema exige um dos dois
            return None
        return [(line.cod_categoria, total)]
    values = [c.valor for c in line.categorias]
    diff = total - sum(values, Decimal(0))
    if abs(diff) > SOMA_TOLERANCIA:
        return None
    values[-1] += diff
    if values[-1] <= 0:
        return None
    return [(c.codigo, v) for c, v in zip(line.categorias, values, strict=True)]


def _percentuals(values: list[Decimal], total: Decimal) -> list[Decimal]:
    """O percentual de cada parcela (2 casas), somando exatamente 100."""
    if total == 0:
        return [Decimal(0) for _ in values]
    parts = [(v / total * _HUNDRED).quantize(_CENT, rounding=ROUND_HALF_UP) for v in values]
    parts[-1] = _HUNDRED - sum(parts[:-1], Decimal(0))
    return parts


def _department_split(
    line: OmiePostingLineRequest, total: Decimal
) -> list[ContaPagarDistribuicao] | None:
    """O rateio com valor E percentual (a forma aceita na captura), fechando o total.

    O percentual da última parcela fecha 100 e o valor dela fecha o total: um
    rateio que não soma o título seria recusado ou, pior, aceito torto.
    """
    if not line.departamentos:
        return None
    pcts = [d.percentual for d in line.departamentos]
    pcts[-1] = _HUNDRED - sum(pcts[:-1], Decimal(0))
    values = [(total * p / _HUNDRED).quantize(_CENT, rounding=ROUND_HALF_UP) for p in pcts]
    values[-1] = total - sum(values[:-1], Decimal(0))
    return [
        ContaPagarDistribuicao(c_cod_dep=d.codigo, n_val_dep=v, n_per_dep=p)
        for d, v, p in zip(line.departamentos, values, pcts, strict=True)
    ]


def _installment_result(
    row: ReconciliationOmiePostingInstallment,
    status: str,
    *,
    message: str | None = None,
) -> OmiePostingInstallmentResult:
    """Uma parcela futura como a tela a lê: datas, valor, título e desfecho."""
    return OmiePostingInstallmentResult.model_validate(
        {
            "numero": row.installment_number,
            "total": row.installment_total,
            "vencimento": row.due_date,
            "emissao": row.issue_date,
            "valor": row.amount,
            "status": status,
            "payable_id": row.payable_id,
            "message": message if message is not None else row.error_message,
        }
    )


def _scale_categories(
    categorias: list[tuple[str, Decimal]], amount: Decimal
) -> list[tuple[str, Decimal]]:
    """As categorias da linha levadas ao valor da parcela, somando exatamente.

    A parcela futura tem o valor da parcela corrente, então o caso comum é a
    divisão igual à da linha. Se um dia diferirem, a proporção se mantém e o
    centavo que sobra vai para a última, como em `_category_split`.
    """
    line_total = sum((v for _, v in categorias), Decimal(0))
    if line_total == amount or line_total == 0:
        return categorias if line_total == amount else [(categorias[0][0], amount)]
    values = [
        (v / line_total * amount).quantize(_CENT, rounding=ROUND_HALF_UP) for _, v in categorias
    ]
    values[-1] = amount - sum(values[:-1], Decimal(0))
    return [(c, v) for (c, _), v in zip(categorias, values, strict=True)]
