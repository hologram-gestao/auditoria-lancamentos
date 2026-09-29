"""O destino `conta_contabil` no de-para: conta do plano do cliente + histórico cifrado (Sprint 16, BACK 16.2).

Tudo o que o de-para precisa saber do plano contábil (16.1) e do histórico passa por
AQUI — o serviço de decisão, a leitura, a prévia e a leitura da materialização não
tocam cifra nem o plano diretamente:

    - `require_accounts`: o validador ÚNICO da 16.1 (`require_postable_accounts`) —
      conta de outro cliente 404, sintética/inativa 422;
    - `encrypt_history` / `decrypt_histories`: o histórico padrão, cifrado com a DEK
      do cliente e AAD pela pk da DECISÃO (`AAD_DECISION_HISTORY`, 17º par);
    - `accounts_by_id`: código e nome (decifrado NA LEITURA) das contas referenciadas;
    - `item_histories`: o histórico de itens de materialização, lido pela VIGÊNCIA
      referenciada no item (`decision_id`), com `client_id` no WHERE — nunca pela
      vigência atual.

**O histórico nunca entra em log, evento nem resposta de erro.** Falha de decifragem
é `[indecifrável]` + warning só com IDs; decisão purgada (cliente encerrado) é `None`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from sqlalchemy import select

from app.core.crypto_service import AAD_DECISION_HISTORY, field_locator, load_client_cipher
from app.core.logging import get_logger
from app.db.models import ClientMappingDecision
from app.modules.client_accounting_chart.service import (
    ACCOUNTING_ACCOUNT_UNDECIPHERABLE,
    AccountingChartService,
    AccountRef,
    not_postable_reason,
)
from app.modules.client_source_accounts.repository import SourceAccountBindingRepository

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.core.crypto import ClientCipher
    from app.db.models import Client
    from app.db.models.client_accounting_account import ClientAccountingAccount
    from app.modules.client_mapping.partida import BindingKey

log = get_logger(__name__)

#: O marcador do histórico indecifrável — o MESMO dos outros campos cifrados.
HISTORY_UNDECIPHERABLE = ACCOUNTING_ACCOUNT_UNDECIPHERABLE


class HasHistory(Protocol):
    @property
    def id(self) -> UUID: ...
    @property
    def history_encrypted(self) -> str | None: ...
    @property
    def history_iv(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class DecryptedHistories:
    """`decision_id → histórico` (só das que TÊM histórico) e as que não decifraram."""

    texts: dict[UUID, str]
    failed: frozenset[UUID]


def normalize_history(value: str | None) -> str | None:
    """O histórico como a regra o entende: pontas aparadas; vazio = sem histórico.

    UM lugar só: a borda (Pydantic) e a comparação de "mesmo efeito" usam esta
    função — o texto que vira vigência é sempre o normalizado.
    """
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class AccountingDecisionSupport:
    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings,
        chart: AccountingChartService | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._chart = chart or AccountingChartService(db, settings=settings)

    # ------------------------------------------------------------ validação

    async def require_accounts(
        self, client: Client, account_ids: Iterable[UUID]
    ) -> dict[UUID, ClientAccountingAccount]:
        """O validador ÚNICO da 16.1, em lote (outro cliente 404; sintética/inativa 422)."""
        return await self._chart.require_postable_accounts(client.id, account_ids)

    # ------------------------------------------------------------ histórico

    async def write_cipher(self, client: Client) -> ClientCipher:
        """Cipher de ESCRITA do histórico — `load_`, nunca `provision_`.

        Decisão com conta do plano exige plano importado, e a importação (16.1) já
        provisionou a DEK. Provisionar aqui, sobre o `Client` carregado pela rota,
        abriria a corrida de DEK (ADR-084-BE); sem DEK o `encrypt` falha ALTO
        (`CryptoError`) antes de qualquer escrita.
        """
        return await load_client_cipher(client, settings=self._settings)

    @staticmethod
    def encrypt_history(cipher: ClientCipher, decision_id: UUID, text: str) -> tuple[str, str]:
        """`(envelope, iv)` do histórico, com a pk da DECISÃO no AAD e IV novo."""
        return cipher.encrypt(text, field_locator(AAD_DECISION_HISTORY, decision_id))

    async def decrypt_histories(
        self, client: Client, decisions: Iterable[HasHistory]
    ) -> DecryptedHistories:
        """Decifra o histórico das decisões que o têm — só leitura (`load_`)."""
        with_history = [d for d in decisions if d.history_encrypted and d.history_iv]
        if not with_history:
            return DecryptedHistories(texts={}, failed=frozenset())
        cipher = await load_client_cipher(client, settings=self._settings)
        texts: dict[UUID, str] = {}
        failed: set[UUID] = set()
        for decision in with_history:
            try:
                texts[decision.id] = cipher.decrypt(
                    decision.history_encrypted or "",
                    decision.history_iv or "",
                    field_locator(AAD_DECISION_HISTORY, decision.id),
                )
            except Exception:
                # Só IDs (§4.1): nunca plaintext, ciphertext nem IV. `except: pass`
                # é proibido — sem o warning o histórico sumiria calado.
                log.warning(
                    "mapping_decision_history_decrypt_failed",
                    client_id=str(client.id),
                    decision_id=str(decision.id),
                )
                texts[decision.id] = HISTORY_UNDECIPHERABLE
                failed.add(decision.id)
        return DecryptedHistories(texts=texts, failed=frozenset(failed))

    async def item_histories(
        self, client: Client, decision_ids: Iterable[UUID | None]
    ) -> dict[UUID, str | None]:
        """O histórico de itens de materialização, pela VIGÊNCIA que decidiu cada um.

        A vigência é append-only e imutável: ler por ela devolve sempre o texto que
        valia quando a linha foi materializada — nunca o da vigência atual. O
        `SELECT` carrega `client_id` no WHERE (§3.15). Decisão que não existe mais
        (encerramento purgou) → `None`; decifragem falha (DEK destruída) →
        `[indecifrável]`. Nunca 500.
        """
        wanted = {d for d in decision_ids if d is not None}
        if not wanted:
            return {}
        stmt = select(ClientMappingDecision).where(
            ClientMappingDecision.client_id == client.id,
            ClientMappingDecision.id.in_(wanted),
        )
        rows = list((await self._db.execute(stmt)).scalars().all())
        decrypted = await self.decrypt_histories(client, rows)
        result: dict[UUID, str | None] = dict.fromkeys(wanted)
        for row in rows:
            result[row.id] = decrypted.texts.get(row.id)
        return result

    # ------------------------------------------------------------ contas

    async def bank_codes(self, client: Client) -> dict[BindingKey, str]:
        """As associações conta de origem → código da conta do BANCO (S16, 16.3).

        A resolução por linha (explícita → padrão só sem conta → pendente) é a função
        pura `partida.resolve_bank_code`; aqui só se carrega o mapa, com `client_id`
        no WHERE e no JOIN.
        """
        return await SourceAccountBindingRepository(self._db).bank_codes(client.id)

    async def accounts_by_id(
        self, client: Client, account_ids: Iterable[UUID | None]
    ) -> dict[UUID, AccountRef]:
        """Código e nome (decifrado na leitura) das contas referenciadas — do cliente.

        Delega ao serviço do plano (`account_refs`, um lugar só): o nome NUNCA vem de
        snapshot, e conta de outro cliente não é carregada (`client_id` no WHERE).
        """
        return await self._chart.account_refs(client, account_ids)

    async def accounts_by_code(
        self, client: Client, codes: Iterable[str]
    ) -> dict[str, ClientAccountingAccount]:
        """As contas do cliente pelo código reduzido (86e3fxqqe: a portabilidade do
        de-para resolve `codigo_alvo` da planilha por aqui, antes de validar
        postabilidade com `require_accounts`)."""
        return await self._chart.accounts_by_code(client.id, codes)

    async def classify_target_codes(
        self, client: Client, codes_by_line: Iterable[tuple[int, str]]
    ) -> tuple[dict[str, UUID], list[dict[str, int | str]]]:
        """Resolve `codigo_alvo` → `accounting_account_id`, linha a linha
        (86e3fxqqe — portabilidade do de-para no destino `conta_contabil`).

        Devolve `(código → id resolvido, [{line, reason}] dos que não resolveram)`
        — `line` como INT (molde de `FileLinesInvalidError.details.lines` da S14,
        `client_file_ingestion/reader.py::LineProblem`); `reason` no MESMO
        vocabulário de `not_postable_reason` (`sintetica`/`inativa`) mais
        `conta_inexistente` (código que o cliente não tem). Quem chama decide o
        que fazer com a lista de inválidos; hoje é a portabilidade, que recusa a
        planilha INTEIRA (molde S14) — resolver conta é erro contábil, não uma
        lacuna que se completa depois.
        """
        pairs = list(codes_by_line)
        accounts = await self.accounts_by_code(client, {code for _, code in pairs})
        resolved: dict[str, UUID] = {}
        invalid: list[dict[str, int | str]] = []
        for line_number, code in pairs:
            account = accounts.get(code)
            if account is None:
                invalid.append({"line": line_number, "reason": "conta_inexistente"})
                continue
            reason = not_postable_reason(account)
            if reason is not None:
                invalid.append({"line": line_number, "reason": reason})
                continue
            resolved[code] = account.id
        return resolved, invalid
