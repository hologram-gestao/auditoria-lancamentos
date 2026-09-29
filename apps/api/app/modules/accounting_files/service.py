"""Gerar, listar e baixar o arquivo contábil (Sprint 13, BACK 13.4 — R3).

- **Uma fonte de linhas:** `ClientMappingApplyService.materialized_lines` (snapshot do item +
  histórico PELA VIGÊNCIA) → `ExportLine` → o gerador puro da 13.3. Nunca se
  materializa aqui: competência sem materialização no `conta_contabil` é 409.
- **Padrão = a ÚLTIMA materialização** do `conta_contabil` na competência;
  `materialization_id` explícito gera uma versão anterior (tem de ser do cliente E da
  competência pedida — senão 404).
- **Layout da organização DO CLIENTE**, na versão mais recente; layout de outra
  organização é 404, igual a inexistente.
- **Registro sem conteúdo:** a geração grava cliente, materialização, layout e versão,
  autor, data, linhas, total e SHA-256; a trilha grava `export`; COMMIT; só então o evento
  `arquivo_contabil_gerado` (fail-soft, ADR-089-BE) e a resposta.
- **Download regenera e confere o SHA-256.** Bate → bytes + trilha `export`. Não bate (ou
  a regeneração é recusada) → 409 `ARQUIVO_DIVERGENTE` + alerta ao plantão, NUNCA um
  arquivo diferente do registrado. Log e alerta só com IDs.
- **Resposta depois do commit** (a correção geral 86e3fxqqa não é desta sprint):
  todas as escritas destas rotas commitam no serviço antes de devolver (ADR-093-BE).
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass
from typing import TYPE_CHECKING, NoReturn
from uuid import UUID

from app.core.alerting import Alert, AlertCode, send_alert
from app.core.audit import AccessAction, record_access
from app.core.exceptions import (
    AccountingFileDivergentError,
    AccountingFileNoMaterializationError,
    AppError,
    NotFoundError,
)
from app.core.logging import get_logger
from app.db.models import AccountingFileGeneration
from app.modules.accounting_files.generator import (
    ExportLine,
    GeneratedFile,
    MaterializationTotals,
    generate_accounting_file,
)
from app.modules.accounting_files.schemas import (
    AccountingFileGenerationItem,
    accounting_file_name,
)
from app.modules.export_layouts.definition import LayoutDefinition, parse_definition
from app.modules.reconciliations.service import author_for_viewer
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.core.config import Settings
    from app.db.models import Client, ClientMappingMaterialization
    from app.modules.accounting_files.repository import AccountingFileRepository
    from app.modules.client_mapping.materialization import (
        ClientMappingApplyService,
        MaterializedLine,
    )
    from app.modules.export_layouts.repository import ExportLayoutRepository

log = get_logger(__name__)

_LAYOUT_NOT_FOUND = "Layout não encontrado."
_GENERATION_NOT_FOUND = "Arquivo gerado não encontrado."

#: Nome interno do codec no Python → nome MIME do `charset` (IANA). O que não está aqui
#: sai pelo nome do codec, que o navegador trata como desconhecido sem quebrar o download.
_MIME_CHARSETS = {
    "iso8859-1": "iso-8859-1",
    "iso8859-15": "iso-8859-15",
    "utf-8": "utf-8",
    "cp1252": "windows-1252",
    "ascii": "us-ascii",
}


def mime_charset(encoding: str) -> str:
    """O `charset` do `Content-Type` para a codificação do layout (`latin-1` → `iso-8859-1`)."""
    name = codecs.lookup(encoding).name
    return _MIME_CHARSETS.get(name, name)


def export_line(line: MaterializedLine) -> ExportLine:
    """`MaterializedLine` (snapshot + histórico da vigência) → a linha do gerador."""
    item = line.item
    return ExportLine(
        item_id=item.id,
        source_type=item.source_type,
        source_movement_id=item.source_movement_id,
        source_account_id=item.source_account_id,
        movement_date=item.movement_date,
        amount=item.amount,
        category_code=item.category_code,
        situation=item.situation,
        accounting_account_code=item.accounting_account_code,
        bank_account_code=item.bank_account_code,
        history_present=item.history_present,
        history=line.history,
    )


@dataclass(frozen=True, slots=True)
class DownloadedFile:
    content: bytes
    file_name: str
    media_type: str


class AccountingFileService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        repository: AccountingFileRepository,
        layouts: ExportLayoutRepository,
        apply: ClientMappingApplyService,
        settings: Settings,
        usage_events: UsageEventService | None = None,
    ) -> None:
        self._db = db
        self._repo = repository
        self._layouts = layouts
        self._apply = apply
        self._settings = settings
        self._usage_events = usage_events or UsageEventService(UsageEventRepository(db))

    # ------------------------------ GERAR ------------------------------

    async def generate(
        self,
        client: Client,
        *,
        actor: CurrentUser,
        layout_id: UUID,
        competence: date,
        materialization_id: UUID | None = None,
    ) -> AccountingFileGenerationItem:
        layout_version, definition = await self._layout(client, layout_id)
        materialization = await self._materialization(client, competence, materialization_id)
        generated = await self._build(client, materialization, definition)

        generation = AccountingFileGeneration(
            client_id=client.id,
            materialization_id=materialization.id,
            layout_id=layout_id,
            layout_version=layout_version,
            competence=materialization.competence,
            line_count=generated.lines,
            total_amount=generated.total_amount,
            sha256=generated.sha256,
            author_id=UUID(actor.id),
        )
        await self._repo.insert_generation(generation)
        await self._audit_export(client, actor)
        # Barreira: a geração e a trilha são o fato; a métrica e a resposta vêm DEPOIS.
        await self._repo.commit()
        log.info(
            "accounting_file_generated",
            client_id=str(client.id),
            generation_id=str(generation.id),
            materialization_id=str(materialization.id),
            layout_id=str(layout_id),
            layout_version=layout_version,
            lines=generated.lines,
        )
        await self._emit(client, materialization, generation, generated)
        row = await self._repo.get_row(client.id, generation.id)
        if row is None:  # pragma: no cover — acabou de ser gravada, no mesmo cliente
            raise NotFoundError(_GENERATION_NOT_FOUND)
        return AccountingFileGenerationItem.build(row, author_for_viewer(row.author, actor))

    # ------------------------------ LISTAR -----------------------------

    async def list_generations(
        self,
        client: Client,
        *,
        viewer: CurrentUser,
        competence: date | None,
        page: int,
        page_size: int,
    ) -> tuple[list[AccountingFileGenerationItem], PaginationMeta]:
        rows, total = await self._repo.list_generations(
            client.id, competence=competence, limit=page_size, offset=(page - 1) * page_size
        )
        items = [
            AccountingFileGenerationItem.build(row, author_for_viewer(row.author, viewer))
            for row in rows
        ]
        return items, PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        )

    # ------------------------------ BAIXAR -----------------------------

    async def download(
        self, client: Client, *, actor: CurrentUser, generation_id: UUID
    ) -> DownloadedFile:
        generation = await self._repo.get_generation(client.id, generation_id)
        if generation is None:
            raise NotFoundError(_GENERATION_NOT_FOUND)
        materialization = await self._repo.get_materialization(
            client.id, generation.materialization_id
        )
        version = await self._layouts.get_version(generation.layout_id, generation.layout_version)
        if materialization is None or version is None:  # pragma: no cover — FKs RESTRICT
            await self._divergent(client, generation, reason="referencia_ausente")
        try:
            definition = parse_definition(version.definition)
            generated = await self._build(client, materialization, definition)
        except AppError:
            # A regeneração foi RECUSADA (ex.: histórico que não se lê mais): o arquivo
            # registrado não é reproduzível — é divergência, nunca um arquivo diferente.
            await self._divergent(client, generation, reason="regeneracao_recusada")
        if generated.sha256 != generation.sha256:
            await self._divergent(client, generation, reason="sha256_diverge")
        await self._audit_export(client, actor)
        await self._repo.commit()
        log.info(
            "accounting_file_downloaded",
            client_id=str(client.id),
            generation_id=str(generation.id),
        )
        return DownloadedFile(
            content=generated.content,
            file_name=accounting_file_name(generation.competence, materialization.version),
            media_type=f"text/csv; charset={mime_charset(definition.encoding)}",
        )

    # ------------------------------ internals --------------------------

    async def _layout(self, client: Client, layout_id: UUID) -> tuple[int, LayoutDefinition]:
        """O layout TEM de ser da organização do cliente; a versão é a mais recente."""
        layout = await self._layouts.get_layout_for_organization(client.organization_id, layout_id)
        if layout is None:
            raise NotFoundError(_LAYOUT_NOT_FOUND)
        latest = await self._layouts.latest_version(layout.id)
        version = await self._layouts.get_version(layout.id, latest)
        if version is None:  # pragma: no cover — todo layout nasce com a versão 1
            raise NotFoundError(_LAYOUT_NOT_FOUND)
        return version.version, parse_definition(version.definition)

    async def _materialization(
        self, client: Client, competence: date, materialization_id: UUID | None
    ) -> ClientMappingMaterialization:
        if materialization_id is not None:
            materialization = await self._repo.get_materialization(client.id, materialization_id)
            if materialization is None or materialization.competence != competence:
                raise NotFoundError("Materialização não encontrada nesta competência.")
            return materialization
        latest = await self._repo.latest_accounting_materialization(client.id, competence)
        if latest is None:
            raise AccountingFileNoMaterializationError(
                f"Cliente {client.id}: competência {competence} sem materialização no "
                "destino conta_contabil."
            )
        return latest

    async def _build(
        self,
        client: Client,
        materialization: ClientMappingMaterialization,
        definition: LayoutDefinition,
    ) -> GeneratedFile:
        lines = await self._apply.materialized_lines(client, materialization.id)
        totals = MaterializationTotals(
            destination_type=materialization.destination_type,
            competence=materialization.competence,
            partial_coverage_confirmed=materialization.partial_coverage_confirmed,
            not_mapped_amount=materialization.not_mapped_amount,
            undecided_amount=materialization.undecided_amount,
            uncategorized_amount=materialization.uncategorized_amount,
        )
        return generate_accounting_file(totals, [export_line(line) for line in lines], definition)

    async def _audit_export(self, client: Client, actor: CurrentUser) -> None:
        """1 linha `export` em `access_audit` (só IDs); persiste no commit do serviço."""
        await record_access(
            self._db,
            user_id=UUID(actor.id),
            client_id=client.id,
            action=AccessAction.EXPORT,
            user_scope=actor.scope,
            actor_client_id=actor.client_id,
            actor_organization_id=actor.organization_id,
        )

    async def _emit(
        self,
        client: Client,
        materialization: ClientMappingMaterialization,
        generation: AccountingFileGeneration,
        generated: GeneratedFile,
    ) -> None:
        """`arquivo_contabil_gerado` DEPOIS do commit da geração — a métrica nunca derruba
        o que já foi gravado (props em `_props_or_none`; o commit da linha do evento
        também fica no fail-soft)."""
        try:
            await self._usage_events.emit_arquivo_contabil_gerado(
                client_id=client.id,
                destino=materialization.destination_type,
                competencia=materialization.competence,
                materializacao_id=materialization.id,
                layout_id=generation.layout_id,
                layout_versao=generation.layout_version,
                linhas=generated.lines,
                valor_total=generated.total_amount,
            )
            await self._repo.commit()
        except Exception:
            # Só IDs: instrumentação com defeito não vira vazamento nem 500.
            log.warning(
                "arquivo_contabil_gerado_emit_failed",
                client_id=str(client.id),
                generation_id=str(generation.id),
            )

    async def _divergent(
        self, client: Client, generation: AccountingFileGeneration, *, reason: str
    ) -> NoReturn:
        """409 + alerta ao PLANTÃO — nunca um arquivo diferente do registrado."""
        log.error(
            "accounting_file_divergent",
            client_id=str(client.id),
            generation_id=str(generation.id),
            reason=reason,
        )
        await send_alert(
            Alert(
                code=AlertCode.ACCOUNTING_FILE_DIVERGENT,
                message=(
                    f"Arquivo contábil {generation.id} não reproduz o SHA-256 registrado "
                    f"({reason}); download recusado."
                ),
                client_id=str(client.id),
            ),
            self._settings,
        )
        raise AccountingFileDivergentError(
            f"Geração {generation.id}: arquivo regenerado diverge do registrado ({reason})."
        )
