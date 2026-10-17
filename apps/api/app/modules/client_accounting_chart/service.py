"""Serviço do plano contábil do cliente (Sprint 16, BACK 16.1 — R1).

Regra de negócio pura, sem HTTP. Três responsabilidades, e só elas:

1. **Importar** a planilha (`import_sheet`): tudo ou nada. O arquivo INTEIRO é
   validado (`sheet.parse_chart_sheet`, em threadpool) ANTES de qualquer escrita;
   só depois a importação trava o plano do cliente, cifra os nomes e grava numa
   transação só. Reimportação casa por código reduzido e SÓ ADICIONA E ATUALIZA
   (86e3qhhrm): nova entra, existente que mudou atualiza nome/tipo/classificação, e
   a ausente fica como estava, com o selo "não veio na última importação". A
   situação nunca muda pela planilha. A diferença é `diff.diff_chart`, a MESMA da
   PRÉVIA (`preview_sheet`), que lê a planilha e não grava; e a EXPORTAÇÃO
   (`export_csv`) devolve o plano no modelo que a importação lê.
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
from datetime import datetime
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
    AccountingAccountCodeExistsError,
    AccountingAccountInUseError,
    AccountingAccountInvalidError,
    AccountingAccountNotFoundError,
    AccountingAccountNotPostableError,
    AccountingChartUndecipherableError,
    ClientClosedError,
)
from app.core.logging import get_logger
from app.db.models import Client
from app.db.models.client_accounting_account import (
    AccountingAccountType,
    ClientAccountingAccount,
)
from app.modules.client_accounting_chart.diff import ChartDiff, diff_chart
from app.modules.client_accounting_chart.export import build_chart_csv
from app.modules.client_accounting_chart.repository import (
    AccountingChartFilters,
    AccountingChartRepository,
)
from app.modules.client_accounting_chart.sheet import (
    REASON_FIELD,
    ChartLayout,
    ChartSheetRow,
    parse_chart_sheet,
    validate_account,
)
from app.modules.client_accounting_chart.sort_key import chart_sort_key
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

#: O que a edição manual mudou numa conta (86e3nb816) — vocabulário FECHADO, vai
#: para o evento `plano_contabil_conta_editada` (nunca o valor, só o campo).
type AccountChange = Literal["nome", "tipo", "classificacao", "situacao"]


@dataclass(frozen=True, slots=True)
class ChartImportResult:
    """As contagens da importação — a resposta e (16.4) o evento `plano_contabil_importado`.

    `accounts` = contas na planilha; `new` = as que o cliente não tinha; `updated` =
    as existentes em que nome, tipo ou classificação mudaram; `absent` = as do
    cliente que não vieram na planilha (continuam como estavam, com o selo).
    """

    accounts: int
    new: int
    updated: int
    absent: int
    #: De onde veio a planilha (`modelo` ou o export nativo do `dominio`). Vai só
    #: para a métrica, não para a resposta.
    layout: ChartLayout = "modelo"


@dataclass(frozen=True, slots=True)
class ChartPreview:
    """O que a importação de uma planilha FARIA, sem gravar (86e3qhhrm).

    `names` são os nomes ATUAIS decifrados (as ausentes são mostradas por eles);
    `absent_in_use` são as ausentes que decisões do de-para (qualquer vigência) ou a
    conta do banco usam: são as que podem levar conta inexistente ao arquivo contábil.
    """

    accounts: int
    layout: ChartLayout
    diff: ChartDiff
    names: DecryptedNames
    absent_in_use: frozenset[UUID]


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
    #: O selo "não veio na última importação" (86e3qhhrm): a conta pode não existir
    #: mais no sistema contábil. `None` = veio na última.
    absent_since: datetime | None = None


@dataclass(frozen=True, slots=True)
class AccountPatch:
    """A edição de UMA conta (86e3nb816). `None` = o campo não veio no pedido.

    O CÓDIGO não se edita: é a chave da reimportação e o que já foi para o snapshot
    das materializações e para o arquivo contábil. A classificação precisa de
    `classification_set` porque `null` explícito LIMPA, e ausência mantém.
    """

    name: str | None = None
    account_type: AccountingAccountType | None = None
    classification: str | None = None
    classification_set: bool = False
    active: bool | None = None


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
                absent_since=a.absent_since,
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
        tipado, nada gravado) → trava por cliente → DEK → diferença (`diff_chart`,
        a mesma da prévia) → escrita numa transação → commit. O commit é daqui (e não
        do fim do request) porque a métrica da 16.4 é emitida DEPOIS dele e nunca
        pode derrubar a importação gravada.

        Só a conta nova e a alterada são escritas, mais a que VOLTOU (para tirar o
        selo); a ausente ganha o selo e mais nada. A situação não muda (86e3qhhrm).
        """
        _require_open(client)
        # CPU síncrona fora do event loop (limites do leitor checados antes de iterar).
        parsed = await run_in_threadpool(parse_chart_sheet, content)
        rows = parsed.rows

        await self._repo.lock_client_chart(client.id)
        cipher = await self._write_cipher(client)
        author_id = UUID(actor.id)
        existing = await self._repo.list_all(client.id)
        current = _decrypt_all(client.id, cipher, existing)
        diff = diff_chart(rows, existing, names=current.names, failed=current.failed)

        inserts, _ = _plan_writes(client.id, diff.new_rows, {}, cipher=cipher, author_id=author_id)
        # A que voltou sem mudar também é regravada: é o UPDATE que tira o selo.
        returned_codes = {account.code for account in diff.returned}
        to_update = [item.row for item in diff.changed] + [
            row for row in diff.unchanged_rows if row.code in returned_codes
        ]
        _, updates = _plan_writes(
            client.id,
            to_update,
            {account.code: account for account in existing},
            cipher=cipher,
            author_id=author_id,
        )
        await self._repo.insert_accounts(inserts)
        await self._repo.update_accounts(updates)
        newly_absent = await self._repo.mark_absent(
            client.id, account_ids=[account.id for account in diff.absent], author_id=author_id
        )
        await self._db.commit()

        result = ChartImportResult(
            accounts=len(rows),
            new=len(inserts),
            updated=len(diff.changed),
            absent=len(diff.absent),
            layout=parsed.layout,
        )
        log.info(
            "accounting_chart_imported",
            client_id=str(client.id),
            layout=result.layout,
            accounts=result.accounts,
            new=result.new,
            updated=result.updated,
            absent=result.absent,
            newly_absent=newly_absent,
            returned=len(diff.returned),
        )
        await self._emit_imported(client, result)
        return result

    async def preview_sheet(self, client: Client, *, content: bytes) -> ChartPreview:
        """O que `import_sheet` faria com esta planilha — SEM GRAVAR (86e3qhhrm).

        O MESMO leitor (os dois layouts e as mesmas recusas 422) e a MESMA diferença
        da importação. Não trava, não provisiona DEK (cliente sem plano não tem o que
        decifrar) e não emite evento: é uma leitura. A trilha também não ganha linha
        (navegação no próprio tenant, §4.7).
        """
        _require_open(client)
        parsed = await run_in_threadpool(parse_chart_sheet, content)
        existing = await self._repo.list_all(client.id)
        current = await self.decrypt_names(client, existing)
        diff = diff_chart(parsed.rows, existing, names=current.names, failed=current.failed)
        in_use = await self._repo.accounts_in_use(
            client.id, [account.id for account in diff.absent]
        )
        log.info(
            "accounting_chart_import_previewed",
            client_id=str(client.id),
            layout=parsed.layout,
            accounts=len(parsed.rows),
            new=len(diff.new_rows),
            updated=len(diff.changed),
            absent=len(diff.absent),
            absent_in_use=len(in_use),
            returned=len(diff.returned),
        )
        return ChartPreview(
            accounts=len(parsed.rows),
            layout=parsed.layout,
            diff=diff,
            names=current,
            absent_in_use=frozenset(in_use),
        )

    async def export_csv(self, client: Client) -> tuple[bytes, int]:
        """O plano inteiro no modelo da plataforma, em CSV (86e3qhhrm), e quantas contas.

        Todas as contas, ativas e inativas, na ordem do plano: exportar e reimportar
        o mesmo arquivo não muda nada. Nome que não decifra recusa a exportação (409
        `PLANO_CONTABIL_INDECIFRAVEL`): o marcador `[indecifrável]` viraria nome de
        conta na reimportação. Plano vazio sai só com o cabeçalho (é o modelo).
        """
        accounts = await self._repo.list_all(client.id)
        names = await self.decrypt_names(client, accounts)
        if names.failed:
            raise AccountingChartUndecipherableError(
                f"plano contábil do cliente {client.id}: {len(names.failed)} nomes indecifráveis",
                details={"count": len(names.failed)},
            )
        content = await run_in_threadpool(build_chart_csv, accounts, names.names)
        log.info("accounting_chart_exported", client_id=str(client.id), accounts=len(accounts))
        return content, len(accounts)

    async def create_account(
        self,
        client: Client,
        *,
        actor: CurrentUser,
        code: str,
        name: str,
        account_type: AccountingAccountType,
        classification: str | None,
    ) -> tuple[ClientAccountingAccount, DecryptedNames]:
        """Inclui UMA conta no plano, sem reimportar a planilha (86e3nb816).

        A MESMA regra de linha da planilha (`validate_account`), a MESMA trava por
        cliente da importação (as duas portas não se cruzam), o MESMO caminho de
        gravação (`_plan_writes`: nome cifrado com a pk no AAD, `sort_key` derivada)
        e a conta nasce ATIVA. Código que o cliente já tem, ativo ou inativo, é 409:
        a conta inativa se reativa pela edição, não por uma segunda conta.
        """
        _require_open(client)
        row = _validated(
            code=code, name=name, account_type=account_type, classification=classification
        )

        await self._repo.lock_client_chart(client.id)
        existing = await self._repo.get_by_codes(client.id, [row.code])
        if existing:
            raise AccountingAccountCodeExistsError(
                f"código já existe no plano do cliente {client.id}",
                details={"code": row.code, "accountId": str(existing[0].id)},
            )
        cipher = await self._write_cipher(client)
        inserts, _ = _plan_writes(client.id, [row], {}, cipher=cipher, author_id=UUID(actor.id))
        await self._repo.insert_accounts(inserts)
        await self._db.commit()

        account_id = _as_uuid(inserts[0]["id"])
        account = await self._repo.get(client.id, account_id)
        if account is None:  # pragma: no cover - acabou de ser gravada na mesma conexão
            raise AccountingAccountNotFoundError(f"conta {account_id} sumiu depois do commit")
        log.info("accounting_account_created", client_id=str(client.id), account_id=str(account.id))
        await self._emit_edited(client, account.id, operation="criada", changes=[])
        return account, DecryptedNames(names={account.id: row.name}, failed=frozenset())

    async def update_account(
        self,
        client: Client,
        account_id: UUID,
        *,
        actor: CurrentUser,
        patch: AccountPatch,
    ) -> tuple[ClientAccountingAccount, DecryptedNames]:
        """Edita nome, tipo, classificação e situação de UMA conta (86e3nb816).

        Conta de outro cliente ou inexistente → 404 (o `SELECT` carrega `client_id`).
        Campo que a planilha recusaria → 422 tipado no campo. Conta que decisões do
        de-para ou a conta do banco usam não passa a sintética nem a inativa (422
        com as contagens). Sem mudança nenhuma: nada é gravado nem emitido.
        """
        _require_open(client)
        await self._repo.lock_client_chart(client.id)
        account = await self._repo.get_for_update(client.id, account_id)
        if account is None:
            raise AccountingAccountNotFoundError(
                f"conta contábil {account_id} não pertence ao cliente {client.id}"
            )
        current = await self.decrypt_names(client, [account])
        old_name = None if account.id in current.failed else current.names.get(account.id)

        new_type = patch.account_type or AccountingAccountType(account.account_type)
        new_classification = (
            patch.classification if patch.classification_set else account.classification
        )
        # O nome que não veio no pedido não é revalidado: o que está gravado já passou
        # pela regra (e pode estar indecifrável, num caso de chave perdida).
        row = _validated(
            code=account.code,
            name=patch.name if patch.name is not None else _NAME_NOT_EDITED,
            account_type=new_type,
            classification=new_classification,
        )
        new_active = account.active if patch.active is None else patch.active

        changes: list[AccountChange] = []
        if patch.name is not None and row.name != old_name:
            changes.append("nome")
        if new_type.value != account.account_type:
            changes.append("tipo")
        if row.classification != account.classification:
            changes.append("classificacao")
        if new_active != account.active:
            changes.append("situacao")
        if not changes:
            return account, current

        await self._refuse_if_in_use(
            client.id,
            account,
            to_synthetic=new_type is AccountingAccountType.SINTETICA,
            to_inactive=not new_active,
        )

        values: dict[str, object] = {
            "account_type": new_type.value,
            "classification": row.classification,
            "sort_key": chart_sort_key(row.classification, account.code),
            "active": new_active,
            "updated_by": UUID(actor.id),
        }
        name = old_name
        if "nome" in changes:
            cipher = await self._write_cipher(client)
            envelope, iv = cipher.encrypt(
                row.name, field_locator(AAD_ACCOUNTING_ACCOUNT_NAME, account.id)
            )
            values["name_encrypted"] = envelope
            values["name_iv"] = iv
            name = row.name
        await self._repo.update_account(client.id, account.id, values)
        await self._db.commit()
        await self._db.refresh(account)

        log.info(
            "accounting_account_updated",
            client_id=str(client.id),
            account_id=str(account.id),
            changes=changes,
        )
        await self._emit_edited(client, account.id, operation="editada", changes=changes)
        names = (
            DecryptedNames(names={account.id: name}, failed=frozenset())
            if name is not None
            else current
        )
        return account, names

    async def _refuse_if_in_use(
        self,
        client_id: UUID,
        account: ClientAccountingAccount,
        *,
        to_synthetic: bool,
        to_inactive: bool,
    ) -> None:
        """422 se a edição tira do conjunto lançável uma conta que alguém usa.

        Só a TRANSIÇÃO conta: a conta que já era sintética ou inativa não é
        recontada (a importação pode tê-la inativado, e isso a planilha decide).
        """
        reason: NotPostableReason | None = None
        if to_synthetic and account.account_type != AccountingAccountType.SINTETICA.value:
            reason = "sintetica"
        elif to_inactive and account.active:
            reason = "inativa"
        if reason is None:
            return
        decision_count, binding_count = await self._repo.count_usage(client_id, account.id)
        if decision_count or binding_count:
            raise AccountingAccountInUseError(
                f"conta {account.id} em uso: {decision_count} decisões, {binding_count} bancos",
                user_message=_in_use_message(reason, decision_count, binding_count),
                details={
                    "reason": reason,
                    "decisionCount": decision_count,
                    "bindingCount": binding_count,
                },
            )

    async def _emit_edited(
        self,
        client: Client,
        account_id: UUID,
        *,
        operation: Literal["criada", "editada"],
        changes: list[AccountChange],
    ) -> None:
        """`plano_contabil_conta_editada` DEPOIS do commit — nunca derruba a escrita."""
        try:
            await self._usage_events.emit_plano_contabil_conta_editada(
                client_id=client.id,
                account_id=account_id,
                operacao=operation,
                campos=changes,
            )
        except Exception:
            log.warning("plano_contabil_conta_editada_emit_failed", client_id=str(client.id))

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
                contas_alteradas=result.updated,
                contas_ausentes=result.absent,
                layout=result.layout,
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
        "Esta conta está inativa e não recebe lançamento. Escolha uma conta ativa ou "
        "reative esta conta no plano contábil do cliente."
    ),
}


#: Marcador do nome que NÃO veio na edição: só serve para a validação conferir os
#: outros campos. Nunca é gravado (o nome só é cifrado quando `nome` mudou).
_NAME_NOT_EDITED = "-"


def _as_uuid(value: object) -> UUID:
    """O `id` que `_plan_writes` gerou, de volta ao tipo (o dicionário é `object`)."""
    if not isinstance(value, UUID):  # pragma: no cover - `_plan_writes` sempre gera UUID
        raise TypeError("id da conta não é UUID")
    return value


def _require_open(client: Client) -> None:
    """Cliente encerrado é só-leitura (§4.12): a escrita recusa ANTES de tocar a DEK."""
    if client.closed_at is not None:
        raise ClientClosedError(
            f"Cliente {client.id} está encerrado desde {client.closed_at.isoformat()}."
        )


def _validated(
    *,
    code: str,
    name: str,
    account_type: AccountingAccountType,
    classification: str | None,
) -> ChartSheetRow:
    """A conta pela regra da planilha, ou o 422 no CAMPO certo."""
    row, reason = validate_account(
        code=code, name=name, account_type=account_type, classification=classification
    )
    if reason is not None or row is None:
        reason = reason or "codigo_vazio"
        field = REASON_FIELD[reason]
        raise AccountingAccountInvalidError(
            f"conta contábil inválida: {field} {reason}",
            user_message=_INVALID_MESSAGES[field],
            details={"field": field, "reason": reason},
        )
    return row


_INVALID_MESSAGES: dict[str, str] = {
    "code": (
        "O código reduzido aceita letras, números, ponto e hífen (até 20 caracteres), "
        "começando e terminando com letra ou número."
    ),
    "name": "Informe o nome da conta (até 200 caracteres).",
    "type": "O tipo da conta é analítica ou sintética.",
    "classification": "A classificação tem até 40 caracteres.",
}


def _in_use_message(reason: NotPostableReason, decisions: int, bindings: int) -> str:
    """A recusa da conta em uso, com as CONTAGENS (nunca quais categorias)."""
    uses: list[str] = []
    if decisions:
        uses.append(f"{decisions} {'decisão' if decisions == 1 else 'decisões'} do de-para")
    if bindings:
        uses.append(f"{bindings} {'conta' if bindings == 1 else 'contas'} do banco")
    state = "sintética" if reason == "sintetica" else "inativa"
    return (
        f"Esta conta não pode ficar {state}: ela está em uso por {' e '.join(uses)}. "
        "Aponte essas configurações para outra conta antes."
    )


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
    A chave da ORDEM (`sort_key`) é derivada aqui, em toda inserção E atualização:
    a reimportação pode trocar a classificação de uma conta, e a chave acompanha.
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
                    "sort_key": chart_sort_key(row.classification, row.code),
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
                    "b_sort": chart_sort_key(row.classification, row.code),
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
