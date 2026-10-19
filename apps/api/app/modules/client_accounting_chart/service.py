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
    AAD_ACCOUNTING_ACCOUNT_PARTY_DOCUMENT,
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
    AppError,
    ClientClosedError,
    ErrorCode,
    OmieChartExportRefusedError,
    PartiesReportCompanyMismatchError,
)
from app.core.logging import get_logger
from app.core.search_index import compute_fingerprint
from app.db.models import Client
from app.db.models.client_accounting_account import (
    AccountingAccountNature,
    AccountingAccountType,
    AccountingPartyType,
    ClientAccountingAccount,
)
from app.modules.client_accounting_chart.diff import ChartDiff, diff_chart
from app.modules.client_accounting_chart.export import build_chart_csv
from app.modules.client_accounting_chart.nature import derive_nature, resolve_nature
from app.modules.client_accounting_chart.omie_sheet import (
    OmieChartLine,
    OmieExportProblem,
    build_omie_chart_xlsx,
    find_problems,
    problems_details,
)
from app.modules.client_accounting_chart.parties import (
    PartyLineProblem,
    parse_parties_report,
    raise_party_lines,
)
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
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.core.config import Settings
    from app.core.crypto import ClientCipher
    from app.integrations.omie.schemas import ClienteOmie

log = get_logger(__name__)

#: O marcador de nome indecifrável — o MESMO do glossário e das categorias do arquivo.
ACCOUNTING_ACCOUNT_UNDECIPHERABLE = "[indecifrável]"

#: Por que uma conta não recebe decisão nova — vocabulário FECHADO (vai em `details`).
type NotPostableReason = Literal["sintetica", "inativa"]

#: O que a edição manual mudou numa conta (86e3nb816) — vocabulário FECHADO, vai
#: para o evento `plano_contabil_conta_editada` (nunca o valor, só o campo).
type AccountChange = Literal["nome", "tipo", "classificacao", "situacao", "natureza"]


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
    #: A natureza (86e3qhhrr): com `nature_set`, um valor a ESCOLHE à mão (a
    #: reimportação passa a respeitá-la) e `None` volta ao automático (derivada da
    #: classificação). Sem `nature_set`, fica como está.
    nature: AccountingAccountNature | None = None
    nature_set: bool = False


@dataclass(frozen=True, slots=True)
class DecryptedNames:
    """`id → nome` decifrado e os ids que NÃO decifraram (saem `[indecifrável]`)."""

    names: dict[UUID, str]
    failed: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class PartyImportResult:
    """As contagens da importação de uma relação (86e3qhhrn), a resposta e o evento.

    `lines` = pessoas no relatório; `new` + `updated` + `unchanged` = contas vinculadas
    por esta importação; `skipped` = as linhas que ficaram sem vínculo, com o motivo
    fechado e o código da conta (nunca o documento).
    """

    party_type: AccountingPartyType
    lines: int
    new: int
    updated: int
    unchanged: int
    skipped: list[PartyLineProblem]

    @property
    def linked(self) -> int:
        return self.new + self.updated + self.unchanged


@dataclass(frozen=True, slots=True)
class PartyDocuments:
    """`id → documento MASCARADO` e os ids que NÃO decifraram (saem sem documento)."""

    masked: dict[UUID, str]
    kinds: dict[UUID, Literal["cnpj", "cpf"]]
    failed: frozenset[UUID]


@dataclass(frozen=True, slots=True)
class PartyLinksSummary:
    """Quantas contas do plano têm vínculo, por tipo."""

    linked: int
    clientes: int
    fornecedores: int


@dataclass(frozen=True, slots=True)
class OmieExport:
    """A planilha da Omie gerada (86e3qhhrr): os bytes e as contagens do evento."""

    content: bytes
    accounts: int
    linked: int


@dataclass(frozen=True, slots=True)
class OmieExportPreview:
    """O que a planilha da Omie levaria, sem gerar (86e3qhhrr).

    `registry_missing` = contas vinculadas cujo documento não tem cadastro no Omie do
    cliente (`None` quando a conferência não rodou, com o motivo em
    `registry_unavailable`).
    """

    accounts: int
    linked: int
    inactive: int
    absent: int
    nature_overridden: int
    problems: list[OmieExportProblem]
    registry_missing: int | None
    registry_unavailable: RegistryUnavailable | None


#: Por que a prévia não conferiu os documentos no cadastro do Omie.
type RegistryUnavailable = Literal["sem_omie", "origem_com_erro", "omie_indisponivel"]

#: As recusas da origem que querem dizer "este cliente não tem Omie para perguntar"
#: (conectar) e "a conexão existe e está com erro" (reconectar): a taxonomia da S9.
_NO_OMIE_CODES = frozenset({ErrorCode.SEM_CONEXAO, ErrorCode.CAPACIDADE_AUSENTE})
_ORIGIN_ERROR_CODES = frozenset({ErrorCode.ORIGEM_COM_ERRO})


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

    # ------------------------------------- planilha do plano de contas da Omie (86e3qhhrr)

    async def omie_export(self, client: Client) -> OmieExport:
        """A planilha de importação do plano de contas da Omie, preenchida.

        Todas as contas do plano, na ordem dele, com nome e documento decifrados SÓ em
        memória (o documento nunca persiste em claro nem vai para log). Qualquer
        problema recusa com 409 `PLANILHA_OMIE_RECUSADA` e os códigos das contas, sem
        gerar nada. O conteúdo não persiste: cada download monta de novo.
        """
        accounts, lines = await self._omie_lines(client)
        problems = find_problems(lines)
        if problems:
            log.info(
                "omie_chart_export_refused",
                client_id=str(client.id),
                accounts=len(lines),
                reasons=[problem.reason for problem in problems],
            )
            raise OmieChartExportRefusedError(
                f"planilha da Omie recusada para o cliente {client.id}",
                details={"problems": problems_details(problems)},
            )
        content = await run_in_threadpool(build_omie_chart_xlsx, lines)
        linked = sum(1 for line in lines if line.document is not None)
        log.info(
            "omie_chart_exported",
            client_id=str(client.id),
            accounts=len(accounts),
            linked=linked,
            bytes=len(content),
        )
        await self._emit_omie_export(client, accounts=len(accounts), linked=linked)
        return OmieExport(content=content, accounts=len(accounts), linked=linked)

    async def omie_export_preview(
        self,
        client: Client,
        *,
        registry: Callable[[], Awaitable[list[ClienteOmie]]],
    ) -> OmieExportPreview:
        """As contagens da planilha e os problemas, sem gerar o arquivo.

        `registry` lê o diretório de cadastros do Omie do cliente (o cache em memória
        da busca de fornecedor, 86e3qhhrd); só é chamado quando há conta vinculada.
        Cliente sem Omie, só por arquivo ou com a Omie fora do ar: a conferência não
        roda e volta o motivo, nunca 500. Os documentos são comparados em memória.
        """
        accounts, lines = await self._omie_lines(client)
        documents = {line.document for line in lines if line.document is not None}
        missing: int | None = None
        unavailable: RegistryUnavailable | None = None
        if documents:
            try:
                cadastros = await registry()
            except AppError as exc:
                unavailable = _registry_unavailable(exc)
                log.info(
                    "omie_chart_export_registry_unavailable",
                    client_id=str(client.id),
                    reason=unavailable,
                    code=str(exc.code),
                )
            else:
                known = {_digits(c.cnpj_cpf) for c in cadastros if c.cnpj_cpf}
                missing = sum(
                    1 for line in lines if line.document is not None and line.document not in known
                )
        else:
            missing = 0
        return OmieExportPreview(
            accounts=len(accounts),
            linked=sum(1 for line in lines if line.document is not None),
            inactive=sum(1 for account in accounts if not account.active),
            absent=sum(1 for account in accounts if account.absent_since is not None),
            nature_overridden=sum(1 for account in accounts if account.nature_overridden),
            problems=find_problems(lines),
            registry_missing=missing,
            registry_unavailable=unavailable,
        )

    async def _omie_lines(
        self, client: Client
    ) -> tuple[list[ClientAccountingAccount], list[OmieChartLine]]:
        """As contas do plano (na ordem dele) e as linhas da planilha, decifradas."""
        accounts = await self._repo.list_all(client.id)
        if not accounts:
            return accounts, []
        cipher = await load_client_cipher(client, settings=self._settings)
        names = _decrypt_all(client.id, cipher, accounts)
        documents = _decrypt_party_documents(client.id, cipher, accounts)
        lines = [
            OmieChartLine(
                code=account.code,
                classification=account.classification,
                name=None if account.id in names.failed else names.names.get(account.id),
                nature=_effective_nature(account),
                account_type=AccountingAccountType(account.account_type),
                document=documents.plain.get(account.id),
                document_failed=account.id in documents.failed,
            )
            for account in accounts
        ]
        return accounts, lines

    async def _emit_omie_export(self, client: Client, *, accounts: int, linked: int) -> None:
        """`planilha_omie_gerada` depois de montar o arquivo — nunca derruba o download."""
        try:
            await self._usage_events.emit_planilha_omie_gerada(
                client_id=client.id,
                contas=accounts,
                vinculadas=linked,
                sem_vinculo=accounts - linked,
            )
        except Exception:
            log.warning("planilha_omie_gerada_emit_failed", client_id=str(client.id))

    async def create_account(
        self,
        client: Client,
        *,
        actor: CurrentUser,
        code: str,
        name: str,
        account_type: AccountingAccountType,
        classification: str | None,
        nature: AccountingAccountNature | None = None,
    ) -> tuple[ClientAccountingAccount, DecryptedNames]:
        """Inclui UMA conta no plano, sem reimportar a planilha (86e3nb816).

        A natureza (86e3qhhrr) vem da classificação; `nature` informada é a escolhida
        à mão e fica marcada como tal.

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
        if nature is not None:
            inserts[0]["nature"] = nature.value
            inserts[0]["nature_overridden"] = True
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
        # A natureza (86e3qhhrr) só é recalculada quando o pedido a toca ou troca a
        # classificação: edição de outro campo não reescreve a natureza da conta.
        old_overridden = bool(account.nature_overridden)
        nature_touched = patch.nature_set or row.classification != account.classification
        if patch.nature_set:
            new_overridden = patch.nature is not None
            current_nature = patch.nature.value if patch.nature is not None else None
        else:
            new_overridden = old_overridden
            current_nature = account.nature
        new_nature = (
            resolve_nature(
                classification=row.classification,
                current=current_nature,
                overridden=new_overridden,
            )
            if nature_touched
            else account.nature
        )

        changes: list[AccountChange] = []
        if patch.name is not None and row.name != old_name:
            changes.append("nome")
        if new_type.value != account.account_type:
            changes.append("tipo")
        if row.classification != account.classification:
            changes.append("classificacao")
        if new_active != account.active:
            changes.append("situacao")
        if nature_touched and (new_nature != account.nature or new_overridden != old_overridden):
            changes.append("natureza")
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
            "nature": new_nature,
            "nature_overridden": new_overridden,
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

    # ------------------------------------------- vínculo conta ↔ pessoa (86e3qhhrn)

    async def import_parties(
        self, client: Client, *, actor: CurrentUser, content: bytes
    ) -> PartyImportResult:
        """Importa a RELAÇÃO DE CLIENTES ou a DE FORNECEDORES do Domínio — por arquivo,
        tudo ou nada.

        Ordem: cliente encerrado (409) → relatório inteiro lido em memória (422 tipado,
        nada gravado) → trava por cliente → EMPRESA do relatório contra a impressão
        digital dos relatórios anteriores (422 `RELATORIO_DE_OUTRA_EMPRESA`) → cada conta
        contra o plano do cliente (inexistente ou sintética recusa o arquivo inteiro,
        `LINHAS_INVALIDAS`) → DEK → documento cifrado por conta, numa transação →
        commit → métrica.

        Reimportar é idempotente: a conta cujo documento e tipo não mudaram não é
        regravada. A conta vinculada que não vem no relatório FICA como está (como a
        reimportação do plano nunca inativa). O documento nunca entra em log.
        """
        _require_open(client)
        report = await run_in_threadpool(parse_parties_report, content)

        await self._repo.lock_client_chart(client.id)
        fingerprint = compute_fingerprint(
            report.company_document, self._settings.SEARCH_BLIND_INDEX_KEY.get_secret_value()
        )
        current_fingerprint = await self._repo.company_fingerprint_for_update(client.id)
        if current_fingerprint is not None and current_fingerprint != fingerprint:
            # A empresa só muda enquanto NENHUMA conta tem vínculo: é a saída para o
            # relatório da empresa errada importado por engano antes de qualquer
            # vínculo; com vínculo gravado, a troca seria misturar duas empresas.
            if sum((await self._repo.count_links_by_type(client.id)).values()) > 0:
                raise PartiesReportCompanyMismatchError(
                    f"relatório de terceiros de outra empresa para o cliente {client.id}"
                )
            current_fingerprint = None

        codes = {line.account_code for line in report.lines}
        accounts = {a.code: a for a in await self._repo.get_by_codes(client.id, codes)}
        problems: list[PartyLineProblem] = []
        for line in report.lines:
            account = accounts.get(line.account_code)
            if account is None:
                problems.append(
                    PartyLineProblem(line.line, "conta_nao_encontrada", line.account_code)
                )
            elif account.account_type != AccountingAccountType.ANALITICA.value:
                problems.append(PartyLineProblem(line.line, "conta_sintetica", line.account_code))
        if problems:
            raise_party_lines(problems)

        if not report.lines:
            # Nada a vincular (só pulos): não provisiona DEK nem grava a empresa.
            result = PartyImportResult(
                party_type=report.party_type,
                lines=report.persons,
                new=0,
                updated=0,
                unchanged=0,
                skipped=report.skipped,
            )
            log.info(
                "accounting_parties_imported",
                client_id=str(client.id),
                relatorio=result.party_type.value,
                lines=result.lines,
                new=0,
                updated=0,
                unchanged=0,
                skipped=len(result.skipped),
            )
            await self._emit_parties_imported(client, result)
            return result

        cipher = await self._write_cipher(client)
        linked_accounts = [accounts[line.account_code] for line in report.lines]
        current = _decrypt_party_documents(client.id, cipher, linked_accounts)
        author_id = UUID(actor.id)
        updates: list[dict[str, object]] = []
        new = updated = unchanged = 0
        for line in report.lines:
            account = accounts[line.account_code]
            if account.party_document_encrypted is None:
                new += 1
            elif (
                account.id not in current.failed
                and current.plain.get(account.id) == line.document
                and account.party_type == report.party_type.value
            ):
                unchanged += 1
                continue
            else:
                updated += 1
            envelope, iv = cipher.encrypt(
                line.document, field_locator(AAD_ACCOUNTING_ACCOUNT_PARTY_DOCUMENT, account.id)
            )
            updates.append(
                {
                    "b_client": client.id,
                    "b_id": account.id,
                    "b_ct": envelope,
                    "b_iv": iv,
                    "b_type": report.party_type.value,
                    "b_author": author_id,
                }
            )
        await self._repo.update_party_links(updates)
        if current_fingerprint is None:
            await self._repo.set_company_fingerprint(client.id, fingerprint)
        await self._db.commit()

        result = PartyImportResult(
            party_type=report.party_type,
            lines=report.persons,
            new=new,
            updated=updated,
            unchanged=unchanged,
            skipped=report.skipped,
        )
        log.info(
            "accounting_parties_imported",
            client_id=str(client.id),
            relatorio=result.party_type.value,
            lines=result.lines,
            new=result.new,
            updated=result.updated,
            unchanged=result.unchanged,
            skipped=len(result.skipped),
        )
        await self._emit_parties_imported(client, result)
        return result

    async def list_parties(
        self, client: Client, *, party_type: AccountingPartyType | None, limit: int, offset: int
    ) -> tuple[
        list[ClientAccountingAccount], int, DecryptedNames, PartyDocuments, PartyLinksSummary
    ]:
        """As contas COM vínculo, paginadas, com o nome decifrado e o documento MASCARADO.

        O documento inteiro nunca sai em lista: `load_client_cipher` (só leitura) abre o
        envelope e `mask_document` guarda só o fim (filial e dígitos do CNPJ, os quatro
        últimos do CPF). Falha de decifragem sai sem documento, nunca 500.
        """
        rows, total = await self._repo.list_linked_page(
            client.id,
            party_type=party_type.value if party_type is not None else None,
            limit=limit,
            offset=offset,
        )
        by_type = await self._repo.count_links_by_type(client.id)
        summary = PartyLinksSummary(
            linked=sum(by_type.values()),
            clientes=by_type.get(AccountingPartyType.CLIENTE.value, 0),
            fornecedores=by_type.get(AccountingPartyType.FORNECEDOR.value, 0),
        )
        if not rows:
            return (
                rows,
                total,
                DecryptedNames(names={}, failed=frozenset()),
                PartyDocuments(masked={}, kinds={}, failed=frozenset()),
                summary,
            )
        cipher = await load_client_cipher(client, settings=self._settings)
        names = _decrypt_all(client.id, cipher, rows)
        plain = _decrypt_party_documents(client.id, cipher, rows)
        documents = PartyDocuments(
            masked={account_id: mask_document(doc) for account_id, doc in plain.plain.items()},
            kinds={
                account_id: ("cnpj" if len(doc) == 14 else "cpf")
                for account_id, doc in plain.plain.items()
            },
            failed=plain.failed,
        )
        return rows, total, names, documents, summary

    async def _emit_parties_imported(self, client: Client, result: PartyImportResult) -> None:
        """`plano_contabil_vinculos_importados` DEPOIS do commit — nunca derruba a escrita."""
        try:
            await self._usage_events.emit_plano_contabil_vinculos_importados(
                client_id=client.id,
                relatorio=result.party_type.value,
                linhas=result.lines,
                vinculos_novos=result.new,
                vinculos_atualizados=result.updated,
                vinculos_sem_mudanca=result.unchanged,
                nao_vinculadas=len(result.skipped),
            )
        except Exception:
            log.warning("plano_contabil_vinculos_importados_emit_failed", client_id=str(client.id))

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


def _registry_unavailable(exc: AppError) -> RegistryUnavailable:
    """Que remédio a tela oferece quando o diretório do Omie não pôde ser lido."""
    if exc.code in _NO_OMIE_CODES:
        return "sem_omie"
    if exc.code in _ORIGIN_ERROR_CODES:
        return "origem_com_erro"
    return "omie_indisponivel"


def _effective_nature(account: ClientAccountingAccount) -> AccountingAccountNature | None:
    """A natureza que vai para a planilha da Omie.

    A gravada; sem ela, e sem escolha à mão, a derivada da classificação pela MESMA
    regra (a linha gravada pela API anterior na janela de deploy nasce sem natureza).
    """
    if account.nature is not None:
        return AccountingAccountNature(account.nature)
    if account.nature_overridden:
        return None
    return derive_nature(account.classification)


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


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
    a reimportação pode trocar a classificação de uma conta, e a chave acompanha. A
    NATUREZA (86e3qhhrr) também, menos a escolhida à mão.
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
                    "nature": resolve_nature(
                        classification=row.classification, current=None, overridden=False
                    ),
                    "nature_overridden": False,
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
                    # A derivada; o UPDATE mantém a escolhida à mão (`CASE` no repositório).
                    "b_nature": resolve_nature(
                        classification=row.classification, current=None, overridden=False
                    ),
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


@dataclass(frozen=True, slots=True)
class _PlainDocuments:
    """`id → documento em claro` (só em memória) e os ids que não decifraram."""

    plain: dict[UUID, str]
    failed: frozenset[UUID]


def _decrypt_party_documents(
    client_id: UUID, cipher: ClientCipher, accounts: Iterable[ClientAccountingAccount]
) -> _PlainDocuments:
    """Abre o documento das contas VINCULADAS; conta sem vínculo não entra em nenhum mapa."""
    plain: dict[UUID, str] = {}
    failed: set[UUID] = set()
    for account in accounts:
        if account.party_document_encrypted is None or account.party_document_iv is None:
            continue
        try:
            plain[account.id] = cipher.decrypt(
                account.party_document_encrypted,
                account.party_document_iv,
                field_locator(AAD_ACCOUNTING_ACCOUNT_PARTY_DOCUMENT, account.id),
            )
        except Exception:
            # Só IDs (§4.1): nunca plaintext, ciphertext nem IV.
            log.warning(
                "accounting_party_document_decrypt_failed",
                client_id=str(client_id),
                account_id=str(account.id),
            )
            failed.add(account.id)
    return _PlainDocuments(plain=plain, failed=frozenset(failed))


def mask_document(digits: str) -> str:
    """O documento como a tela o mostra: só o fim, nunca o inteiro.

    CNPJ (14 dígitos): `**.***.***/0001-91` (a raiz escondida, filial e verificadores à
    vista: é o que distingue filiais e confirma a pessoa sem identificá-la). CPF (11):
    `***.***.*89-00` (os quatro últimos). Outro tamanho é defeito de gravação: sai
    inteiramente mascarado.
    """
    if len(digits) == 14:
        return f"**.***.***/{digits[8:12]}-{digits[12:]}"
    if len(digits) == 11:
        return f"***.***.*{digits[7:9]}-{digits[9:]}"
    return "*" * len(digits)
