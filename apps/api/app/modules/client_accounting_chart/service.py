"""Serviço do plano contábil do cliente (Sprint 16, BACK 16.1 — R1).

Regra de negócio pura, sem HTTP. Três responsabilidades, e só elas:

1. **Importar** a planilha (`import_sheet`): tudo ou nada. O arquivo INTEIRO é
   validado (`sheet.parse_chart_sheet`, em threadpool) ANTES de qualquer escrita;
   só depois a importação trava o plano do cliente, cifra os nomes e grava numa
   transação só. Reimportação casa por código reduzido: nova entra, existente
   atualiza nome/tipo/classificação e volta a ativa, ausente vira inativa — nunca
   apagada.
2. **Validar conta para decisão nova** (`require_postable_account`): o validador
   ÚNICO consumido pelo de-para (16.2) e pela conta do banco (16.3). Outro cliente
   ou inexistente → 404 sem vazar existência; sintética ou inativa → 422 tipado.
3. **Decifrar nomes na leitura** (`decrypt_names`): falha vira `[indecifrável]` +
   warning só com IDs (§4.1), nunca célula vazia silenciosa.

O nome da conta, o código e o conteúdo de célula NUNCA entram em log: o que se loga
são IDs e contagens.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.crypto_service import (
    AAD_ACCOUNTING_ACCOUNT_NAME,
    field_locator,
    load_client_cipher,
    provision_client_cipher,
)
from app.core.exceptions import (
    AccountingAccountNotFoundError,
    AccountingAccountNotPostableError,
    ClientClosedError,
)
from app.core.logging import get_logger
from app.db.models import Client
from app.db.models.client_accounting_account import (
    AccountingAccountType,
    ClientAccountingAccount,
)
from app.modules.client_accounting_chart.repository import (
    AccountingChartFilters,
    AccountingChartRepository,
)
from app.modules.client_accounting_chart.sheet import ChartSheetRow, parse_chart_sheet
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.core.config import Settings
    from app.core.crypto import ClientCipher

log = get_logger(__name__)

#: O marcador de nome indecifrável — o MESMO do glossário e das categorias do arquivo.
ACCOUNTING_ACCOUNT_UNDECIPHERABLE = "[indecifrável]"

#: Por que uma conta não recebe decisão nova — vocabulário FECHADO (vai em `details`).
type NotPostableReason = Literal["sintetica", "inativa"]


@dataclass(frozen=True, slots=True)
class ChartImportResult:
    """As contagens da importação — a resposta e (16.4) o evento `plano_contabil_importado`.

    `accounts` = contas na planilha; `new` = as que o cliente não tinha;
    `inactivated` = as que eram ATIVAS e sumiram da planilha (passaram a inativas
    nesta importação).
    """

    accounts: int
    new: int
    inactivated: int


@dataclass(frozen=True, slots=True)
class AccountRef:
    """Uma conta do plano como as outras telas a mostram: código em claro, nome da leitura.

    O de-para (16.2) e a conta do banco (16.3) exibem a conta escolhida por AQUI — o
    nome nunca vem de snapshot: é decifrado a cada leitura.
    """

    id: UUID
    code: str
    name: str
    name_resolved: bool
    postable: bool


@dataclass(frozen=True, slots=True)
class DecryptedNames:
    """`id → nome` decifrado e os ids que NÃO decifraram (saem `[indecifrável]`)."""

    names: dict[UUID, str]
    failed: frozenset[UUID]


def not_postable_reason(account: ClientAccountingAccount) -> NotPostableReason | None:
    """Função PURA: por que esta conta não recebe decisão nova (`None` = recebe).

    Sintética vem antes de inativa: é a mais estável das duas (não muda com a
    próxima planilha), e é a que a pessoa corrige escolhendo outra conta.
    """
    if account.account_type != AccountingAccountType.ANALITICA.value:
        return "sintetica"
    if not account.active:
        return "inativa"
    return None


class AccountingChartService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings,
        repository: AccountingChartRepository | None = None,
        usage_events: UsageEventService | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._repo = repository or AccountingChartRepository(db)
        # S16 (BACK 16.4): `plano_contabil_importado` depois do commit — o default é emitir.
        self._usage_events = usage_events or UsageEventService(UsageEventRepository(db))

    # ------------------------------------------------------------------ leitura

    async def list_page(
        self,
        client: Client,
        *,
        filters: AccountingChartFilters,
        limit: int,
        offset: int,
    ) -> tuple[list[ClientAccountingAccount], int, DecryptedNames]:
        rows, total = await self._repo.list_page(
            client.id, filters=filters, limit=limit, offset=offset
        )
        return rows, total, await self.decrypt_names(client, rows)

    async def decrypt_names(
        self, client: Client, accounts: Sequence[ClientAccountingAccount]
    ) -> DecryptedNames:
        """Decifra os nomes NA LEITURA, com a DEK do cliente — só leitura.

        `load_client_cipher` não provisiona DEK: cliente encerrado continua legível,
        e os nomes dele saem `[indecifrável]` (o estado honesto depois do
        crypto-shredding), nunca 500.
        """
        if not accounts:
            return DecryptedNames(names={}, failed=frozenset())
        cipher = await load_client_cipher(client, settings=self._settings)
        return _decrypt_all(client.id, cipher, accounts)

    async def account_refs(
        self, client: Client, account_ids: Iterable[UUID | None]
    ) -> dict[UUID, AccountRef]:
        """Código e nome (decifrado NA LEITURA) das contas pedidas — SÓ as do cliente."""
        wanted = {a for a in account_ids if a is not None}
        if not wanted:
            return {}
        accounts = await self._repo.get_many(client.id, wanted)
        names = await self.decrypt_names(client, accounts)
        return {
            a.id: AccountRef(
                id=a.id,
                code=a.code,
                name=names.names.get(a.id, ACCOUNTING_ACCOUNT_UNDECIPHERABLE),
                name_resolved=a.id in names.names and a.id not in names.failed,
                postable=not_postable_reason(a) is None,
            )
            for a in accounts
        }

    async def accounts_by_code(
        self, client_id: UUID, codes: Iterable[str]
    ) -> dict[str, ClientAccountingAccount]:
        """As contas do cliente pelo código reduzido — SÓ encontra, não valida
        postabilidade (`require_postable_account(s)` segue sendo o validador único).

        Usado pela portabilidade do de-para (86e3fxqqe) pra resolver `codigo_alvo`
        da planilha no destino `conta_contabil`: a planilha carrega o código, não o
        `UUID` da conta.
        """
        wanted = {c for c in codes if c}
        if not wanted:
            return {}
        accounts = await self._repo.get_by_codes(client_id, wanted)
        return {a.code: a for a in accounts}

    # ------------------------------------------------------------- validador único

    async def require_postable_account(
        self, client_id: UUID, account_id: UUID
    ) -> ClientAccountingAccount:
        """A conta, se ela puder receber decisão NOVA; senão, a recusa tipada.

        - de OUTRO cliente ou inexistente → 404 (`AccountingAccountNotFoundError`):
          o `SELECT` carrega `client_id` no próprio WHERE, a linha alheia nem é lida;
        - sintética ou inativa → 422 `CONTA_CONTABIL_NAO_LANCAVEL`, com
          `details.reason` e `details.accountId` (só id e vocabulário fechado).

        É o ÚNICO lugar que decide isso: o de-para (16.2) e a conta do banco (16.3)
        chamam este método, nunca repetem a regra.
        """
        return (await self.require_postable_accounts(client_id, [account_id]))[account_id]

    async def require_postable_accounts(
        self, client_id: UUID, account_ids: Iterable[UUID]
    ) -> dict[UUID, ClientAccountingAccount]:
        """`require_postable_account` para um LOTE, numa query — a MESMA regra.

        O lote de decisões do de-para (até 500) não faz uma ida ao banco por conta.
        A primeira conta problemática (ordem do id, para a mensagem ser estável)
        decide a recusa: 404 antes de 422, porque a de outro cliente não pode nem
        ter a postabilidade revelada.
        """
        wanted = sorted(set(account_ids), key=str)
        found = {a.id: a for a in await self._repo.get_many(client_id, wanted)}
        for account_id in wanted:
            if account_id not in found:
                raise AccountingAccountNotFoundError(
                    f"conta contábil {account_id} não pertence ao cliente {client_id}"
                )
        for account_id in wanted:
            reason = not_postable_reason(found[account_id])
            if reason is not None:
                raise AccountingAccountNotPostableError(
                    f"conta contábil {account_id} não lançável: {reason}",
                    user_message=_NOT_POSTABLE_MESSAGES[reason],
                    details={"accountId": str(account_id), "reason": reason},
                )
        return found

    # ------------------------------------------------------------------ escrita

    async def import_sheet(
        self, client: Client, *, actor: CurrentUser, content: bytes
    ) -> ChartImportResult:
        """Importa (ou reimporta) o plano do cliente — tudo ou nada.

        Ordem: cliente encerrado (409) → planilha inteira validada em memória (422
        tipado, nada gravado) → trava por cliente → DEK → escrita numa transação →
        commit. O commit é daqui (e não do fim do request) porque a métrica da 16.4
        é emitida DEPOIS dele e nunca pode derrubar a importação gravada.
        """
        if client.closed_at is not None:
            raise ClientClosedError(
                f"Cliente {client.id} está encerrado desde {client.closed_at.isoformat()}."
            )
        # CPU síncrona fora do event loop (limites do leitor checados antes de iterar).
        rows = await run_in_threadpool(parse_chart_sheet, content)

        await self._repo.lock_client_chart(client.id)
        cipher = await self._write_cipher(client)
        author_id = UUID(actor.id)
        existing = {account.code: account for account in await self._repo.list_all(client.id)}

        inserts, updates = _plan_writes(
            client.id, rows, existing, cipher=cipher, author_id=author_id
        )
        await self._repo.insert_accounts(inserts)
        await self._repo.update_accounts(updates)
        inactivated = await self._repo.deactivate_absent(
            client.id, present_codes=[row.code for row in rows], author_id=author_id
        )
        await self._db.commit()

        result = ChartImportResult(accounts=len(rows), new=len(inserts), inactivated=inactivated)
        log.info(
            "accounting_chart_imported",
            client_id=str(client.id),
            accounts=result.accounts,
            new=result.new,
            updated=len(updates),
            inactivated=result.inactivated,
        )
        await self._emit_imported(client, result)
        return result

    async def _emit_imported(self, client: Client, result: ChartImportResult) -> None:
        """A métrica da 16.4 DEPOIS do commit — e ela nunca derruba a importação gravada.

        O emissor já é fail-soft (props em `_props_or_none`, INSERT sob `try`); a
        guarda aqui cobre o que escapar dele (a importação já foi confirmada ao
        banco quando esta linha roda). Só IDs e contagens no warning.
        """
        try:
            await self._usage_events.emit_plano_contabil_importado(
                client_id=client.id,
                contas=result.accounts,
                contas_novas=result.new,
                contas_inativadas=result.inactivated,
            )
        except Exception:
            log.warning("plano_contabil_importado_emit_failed", client_id=str(client.id))

    async def _write_cipher(self, client: Client) -> ClientCipher:
        """Cipher de ESCRITA, provisionando a DEK só pelo caminho existente.

        Cliente sem origem ainda não tem DEK (§4.8: ela nasce na primeira conexão),
        e o escritório pode importar o plano antes de conectar. O `Client` da rota
        foi carregado ANTES da trava; provisionar sobre ele deixaria duas
        importações simultâneas gerarem DEKs diferentes (landmine da cripto,
        ADR-084-BE). Por isso a linha é RELIDA sob `FOR UPDATE` depois da trava
        (`populate_existing` atualiza a mesma instância): a segunda importação vê a
        DEK que a primeira gravou. O check de encerrado já rodou (e a rota usa
        `OpenClientDep`): DEK nova nunca nasce num tenant morto.
        """
        if client.dek_wrapped is None:
            stmt = (
                select(Client)
                .where(Client.id == client.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            client = (await self._db.execute(stmt)).scalar_one()
        return await provision_client_cipher(client, settings=self._settings)


_NOT_POSTABLE_MESSAGES: dict[NotPostableReason, str] = {
    "sintetica": (
        "Esta conta é sintética (só agrupa outras contas) e não recebe lançamento. "
        "Escolha uma conta analítica do plano contábil do cliente."
    ),
    "inativa": (
        "Esta conta está inativa: ela não veio na última planilha do plano contábil "
        "importada. Escolha uma conta ativa ou reimporte o plano com ela."
    ),
}


def _plan_writes(
    client_id: UUID,
    rows: Iterable[ChartSheetRow],
    existing: dict[str, ClientAccountingAccount],
    *,
    cipher: ClientCipher,
    author_id: UUID,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Separa a planilha em INSERTs (código novo) e UPDATEs (código existente).

    O nome é cifrado com a pk da linha no AAD — por isso a conta nova tem o `id`
    gerado AQUI, antes do INSERT. IV novo por operação, inclusive na atualização.
    """
    inserts: list[dict[str, object]] = []
    updates: list[dict[str, object]] = []
    for row in rows:
        account = existing.get(row.code)
        account_id = account.id if account is not None else uuid4()
        envelope, iv = cipher.encrypt(
            row.name, field_locator(AAD_ACCOUNTING_ACCOUNT_NAME, account_id)
        )
        if account is None:
            inserts.append(
                {
                    "id": account_id,
                    "client_id": client_id,
                    "code": row.code,
                    "classification": row.classification,
                    "name_encrypted": envelope,
                    "name_iv": iv,
                    "account_type": row.account_type.value,
                    "active": True,
                    "created_by": author_id,
                    "updated_by": author_id,
                }
            )
        else:
            updates.append(
                {
                    "b_client": client_id,
                    "b_id": account_id,
                    "b_ct": envelope,
                    "b_iv": iv,
                    "b_type": row.account_type.value,
                    "b_class": row.classification,
                    "b_author": author_id,
                }
            )
    return inserts, updates


def _decrypt_all(
    client_id: UUID, cipher: ClientCipher, accounts: Iterable[ClientAccountingAccount]
) -> DecryptedNames:
    names: dict[UUID, str] = {}
    failed: set[UUID] = set()
    for account in accounts:
        try:
            names[account.id] = cipher.decrypt(
                account.name_encrypted,
                account.name_iv,
                field_locator(AAD_ACCOUNTING_ACCOUNT_NAME, account.id),
            )
        except Exception:
            # Só IDs (§4.1): nunca plaintext, ciphertext, IV nem código. Sem este
            # warning (a métrica `decrypt_failed` da casa) o nome sumiria calado.
            log.warning(
                "accounting_account_decrypt_failed",
                client_id=str(client_id),
                account_id=str(account.id),
            )
            names[account.id] = ACCOUNTING_ACCOUNT_UNDECIPHERABLE
            failed.add(account.id)
    return DecryptedNames(names=names, failed=frozenset(failed))
