"""Serviço de ingestão do arquivo do cliente sem ERP (Sprint 14, BACK 14.3 — R1/R2/R3).

**A ordem das recusas é a regra, não estética** (R2: "arquivo processa inteiro ou
não processa"):

    1. conexão `arquivo` ATIVA (taxonomia dos três 409 da S9);
    2. mapeamento existe — senão 409 `SEM_MAPEAMENTO` com as colunas encontradas;
    3. convenção de sinal declarada (`SINAL_NAO_DECLARADO`, defesa em profundidade);
    4. formato do arquivo é o do mapeamento (`FORMATO_NAO_SUPORTADO`);
    5. hash já processado nesta competência (`ARQUIVO_JA_PROCESSADO`);
    6. CABEÇALHO conferido ANTES da primeira linha (`CABECALHO_DIVERGENTE`);
    7. TODAS as linhas lidas acumulando problemas (`LINHAS_INVALIDAS`);
    8. total informado x soma algébrica COM SINAL, `Decimal` exato (`TOTAL_DIVERGENTE`);
    9. só então UMA transação sob `pg_advisory_xact_lock` por cliente: registro do
       arquivo (a UNIQUE dá o 409 sob corrida), categorias de origem (registry da
       14.4), linhas como `ProviderEntry` pelo `FileProvider` (14.1) e o MESMO ciclo
       `_persist` do R0 (S12) com `source_type='arquivo'`; commit; `arquivo_processado`.

**Toda recusa a partir do passo 2 emite `arquivo_processado{rejeitado=true, motivo}`
pelo caminho DURÁVEL da 14.2** (o emissor commita antes do `raise`) e NÃO grava
movimento algum — nada de negócio foi escrito antes do passo 9.

**Identidade da linha, nunca do conteúdo:** `source_movement_id = <16 hex do hash do
arquivo>:<número da linha>`. Duas linhas idênticas são dois movimentos. Arquivo
corrigido (hash diferente) na mesma competência entra: pelo ciclo R0 as linhas do
anterior viram `ausente_na_origem` e as novas ficam `presente` (nunca apaga).

**Segurança (§3.10, §4.5):** arquivo só em memória; descrição só cifrada (pela DEK
do cliente, no ciclo); nenhum conteúdo de célula nem nome de coluna em log, evento,
mensagem de erro ou `access_audit`. Parse síncrono fora do event loop.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, NoReturn
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from app.core.exceptions import (
    AppError,
    ClientClosedError,
    FileAlreadyProcessedError,
    FileHeaderMismatchError,
    FileLinesInvalidError,
    FileTotalMismatchError,
    NoInputMappingError,
    OriginCapabilityMissingError,
    SignConventionMissingError,
)
from app.core.logging import get_logger
from app.db.models import ClientFileImport
from app.db.models.client_connection import ConnectionStatus, ProviderType
from app.db.models.client_input_mapping import InputFileFormat
from app.integrations.providers.base import Capability, ProviderEntry
from app.integrations.providers.file_adapter import FileProvider
from app.modules.client_connections.capability import select_capable_connection
from app.modules.client_connections.legacy_fallback import resolve_origin_connections
from app.modules.client_file_ingestion.reader import (
    MAX_INVALID_LINES_REPORTED,
    SAMPLE_ROWS,
    MappingSpec,
    ParsedLine,
    ReadOptions,
    assert_format_matches,
    convert_lines,
    detect_format,
    read_table,
    sample_text,
)
from app.modules.client_file_ingestion.repository import ClientFileImportRepository
from app.modules.client_movements.competence import competence_bounds
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.core.config import Settings
    from app.db.models import Client, User
    from app.db.models.client_input_mapping import ClientInputMapping
    from app.modules.client_file_categories.registry import FileCategoryRegistry
    from app.modules.client_file_ingestion.reader import ParsedTable
    from app.modules.client_input_mappings.service import ClientInputMappingService
    from app.modules.client_movements.service import ClientMovementsSyncService
    from app.modules.usage_events.schemas import FileRejectionReason

log = get_logger(__name__)

#: Delimitador e codificação da INSPEÇÃO quando o cliente ainda não tem mapeamento —
#: o que o Excel brasileiro exporta. Declarados aqui, nunca farejados.
DEFAULT_INSPECT_DELIMITER = ";"
DEFAULT_INSPECT_ENCODING = "utf-8-sig"

#: Quantos caracteres do hash entram na identidade da linha.
_HASH_PREFIX_CHARS = 16


@dataclass(frozen=True, slots=True)
class InspectResult:
    file_format: InputFileFormat
    columns: list[str]
    sample: list[list[str]]
    has_mapping: bool


@dataclass(frozen=True, slots=True)
class ProcessResult:
    import_id: UUID
    competence: date
    rows: int
    columns_recognized: int
    categories_created: int
    absent: int
    mapping_id: UUID
    processed_at: datetime


class FileIngestionService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings,
        mappings: ClientInputMappingService,
        registry: FileCategoryRegistry,
        movements: ClientMovementsSyncService,
        imports: ClientFileImportRepository | None = None,
        usage_events: UsageEventService | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._mappings = mappings
        self._registry = registry
        self._movements = movements
        self._imports = imports or ClientFileImportRepository(db)
        self._usage_events = usage_events or UsageEventService(UsageEventRepository(db))

    # ------------------------------------------------------------------ inspeção

    async def inspect(
        self,
        client: Client,
        content: bytes,
        *,
        csv_delimiter: str | None,
        encoding: str | None,
    ) -> InspectResult:
        """Formato, colunas e amostra — nada persistido, nada logado.

        Cliente ENCERRADO é 409 aqui também (R3): a rota lê com `AccessibleClientDep`
        (o histórico de arquivos continua legível), mas inspecionar é o primeiro
        passo de um envio, e encerrado não envia mais arquivo.
        """
        if client.closed_at is not None:
            raise ClientClosedError(f"Cliente {client.id} encerrado; inspeção recusada.")
        await self._require_file_connection(client)
        mapping = await self._mappings.get_row(client)
        detected = detect_format(content)
        options = self._read_options(detected, mapping, csv_delimiter, encoding)
        table = await run_in_threadpool(read_table, content, options, limit=SAMPLE_ROWS)
        return InspectResult(
            file_format=detected,
            columns=table.columns,
            sample=[sample_text(values) for _, values in table.rows],
            has_mapping=mapping is not None,
        )

    # ------------------------------------------------------------------ processamento

    async def process(
        self,
        client: Client,
        *,
        actor: CurrentUser,
        content: bytes,
        competence: date,
        declared_total: Decimal | None,
    ) -> ProcessResult:
        """O arquivo inteiro vira base de movimentos da competência — ou nada."""
        if client.closed_at is not None:
            raise ClientClosedError(f"Cliente {client.id} encerrado; envio recusado.")
        await self._require_file_connection(client)

        mapping = await self._mappings.get_row(client)
        if mapping is None:
            columns = await self._columns_without_mapping(client, content)
            await self._refuse(
                client,
                NoInputMappingError(
                    f"Cliente {client.id} sem mapeamento de entrada.",
                    details={"foundColumns": columns},
                ),
                motivo="sem_mapeamento",
                mapping_id=None,
                colunas=len(columns),
            )
        spec = MappingSpec.from_row(mapping)
        if spec.sign_convention is None:
            await self._refuse(
                client,
                SignConventionMissingError(f"Cliente {client.id}: mapeamento sem convenção."),
                motivo="sinal_nao_declarado",
                mapping_id=mapping.id,
            )

        try:
            detected = detect_format(content)
            assert_format_matches(detected, spec.file_format)
        except AppError as exc:
            await self._refuse(client, exc, motivo="formato_nao_suportado", mapping_id=mapping.id)

        file_hash = hashlib.sha256(content).hexdigest()
        if await self._imports.exists(client.id, competence=competence, file_hash=file_hash):
            await self._refuse(
                client,
                FileAlreadyProcessedError(
                    f"Cliente {client.id}: hash já processado em {competence}."
                ),
                motivo="arquivo_ja_processado",
                mapping_id=mapping.id,
            )

        options = ReadOptions(
            file_format=spec.file_format,
            csv_delimiter=spec.csv_delimiter or DEFAULT_INSPECT_DELIMITER,
            encoding=spec.encoding or DEFAULT_INSPECT_ENCODING,
        )
        try:
            table = await run_in_threadpool(
                read_table, content, options, on_header=self._header_checker(spec)
            )
        except FileHeaderMismatchError as exc:
            # A métrica quer quantas colunas do mapeamento o arquivo TINHA; o
            # cabeçalho lido só existe como contagem (nada de texto de célula).
            found_count = exc.details.get("foundColumnCount", 0)
            missing = exc.details.get("missingColumns", [])
            await self._refuse(
                client,
                exc,
                motivo="cabecalho_divergente",
                mapping_id=mapping.id,
                colunas=max(found_count - len(missing), 0) if found_count else 0,
            )
        except AppError as exc:
            await self._refuse(client, exc, motivo="arquivo_invalido", mapping_id=mapping.id)

        columns_recognized = len(spec.mapped_columns)
        start, end = competence_bounds(competence)
        lines, problems = convert_lines(table, spec, start=start, end=end)
        if problems:
            await self._refuse(
                client,
                FileLinesInvalidError(
                    f"Cliente {client.id}: {len(problems)} linha(s) inválida(s).",
                    details={
                        "lines": [
                            {"line": p.line, "reason": p.reason}
                            for p in problems[:MAX_INVALID_LINES_REPORTED]
                        ],
                        "total": len(problems),
                    },
                ),
                motivo="linhas_invalidas",
                mapping_id=mapping.id,
                linhas=len(table.rows),
                colunas=columns_recognized,
            )

        computed_total = sum((line.amount for line in lines), Decimal("0.00"))
        if declared_total is not None and declared_total != computed_total:
            await self._refuse(
                client,
                FileTotalMismatchError(
                    f"Cliente {client.id}: total informado difere do calculado.",
                    details={
                        "declaredTotal": str(declared_total),
                        "computedTotal": str(computed_total),
                    },
                ),
                motivo="total_divergente",
                mapping_id=mapping.id,
                linhas=len(lines),
                colunas=columns_recognized,
            )

        result = await self._write(
            client,
            actor=actor,
            mapping=mapping,
            spec=spec,
            lines=lines,
            file_hash=file_hash,
            competence=competence,
            start=start,
            end=end,
            columns_recognized=columns_recognized,
        )
        # Depois do commit, fail-soft: a métrica nunca derruba o que já foi gravado.
        await self._usage_events.emit_arquivo_processado(
            client_id=client.id,
            mapeamento_id=mapping.id,
            linhas=result.rows,
            colunas_reconhecidas=columns_recognized,
            rejeitado=False,
            motivo="nenhum",
        )
        return result

    async def list_imports(
        self, client: Client, *, competence: date | None
    ) -> list[tuple[ClientFileImport, User]]:
        return await self._imports.list_for_client(client.id, competence=competence)

    # ------------------------------------------------------------------ internals

    async def _write(
        self,
        client: Client,
        *,
        actor: CurrentUser,
        mapping: ClientInputMapping,
        spec: MappingSpec,
        lines: list[ParsedLine],
        file_hash: str,
        competence: date,
        start: date,
        end: date,
        columns_recognized: int,
    ) -> ProcessResult:
        """UMA transação, sob o lock do cliente: registro, categorias, movimentos, commit."""
        await self._imports.lock_client(client.id)

        record = ClientFileImport(
            client_id=client.id,
            competence=competence,
            file_hash=file_hash,
            mapping_id=mapping.id,
            rows=len(lines),
            created_by=UUID(actor.id),
        )
        if not await self._imports.add(record):
            # Corrida entre a leitura amigável e o INSERT: a UNIQUE decidiu.
            await self._refuse(
                client,
                FileAlreadyProcessedError(
                    f"Cliente {client.id}: hash já processado em {competence} (corrida)."
                ),
                motivo="arquivo_ja_processado",
                mapping_id=mapping.id,
                linhas=len(lines),
                colunas=columns_recognized,
            )

        # Categorias de origem (14.4): só quando o arquivo trouxe rótulo — sem coluna
        # de categoria (ou todas vazias) o registry nem é consultado. "Criadas" = os
        # rótulos DESTE arquivo que o cliente ainda não tinha (igualdade byte a byte).
        labels = {line.category_label for line in lines if line.category_label}
        codes: dict[str, str] = {}
        categories_created = 0
        if labels:
            known = set((await self._registry.resolve_names(client)).names.values())
            codes = await self._registry.resolve_codes(client, labels)
            categories_created = len(labels - known)

        prefix = file_hash[:_HASH_PREFIX_CHARS]
        entries: list[ProviderEntry] = []
        descriptions: dict[str, str] = {}
        accounts: dict[str, str | None] = {}
        for line in lines:
            external_id = f"{prefix}:{line.line}"
            entries.append(
                ProviderEntry(
                    external_id=external_id,
                    entry_date=line.entry_date,
                    amount=line.amount,
                    description=line.description,
                    category_code=codes.get(line.category_label) if line.category_label else None,
                    supplier_code=None,
                    document_number=line.document,
                )
            )
            descriptions[external_id] = line.description
            accounts[external_id] = line.account

        # Pelo ADAPTADOR (14.1): é o que faz o provedor ser "mais um adaptador" e não
        # um caminho paralelo — a base recebe `ProviderEntry`, como do Omie.
        provider = FileProvider(entries)
        listed = await provider.list_entries(account_external_id="*", start=start, end=end)
        outcome = await self._movements.persist_entries(
            client,
            competence,
            source_type=ProviderType.ARQUIVO.value,
            entries=[(accounts[e.external_id], e) for e in listed],
            accounts=None,
            descriptions=descriptions,
            emit=False,
        )
        await self._db.refresh(record)
        # Barreira: o arquivo é o fato; a métrica vem DEPOIS do commit.
        await self._db.commit()
        log.info(
            "client_file_processed",
            client_id=str(client.id),
            competence=competence.isoformat(),
            rows=len(lines),
            categories_created=categories_created,
            absent=outcome.ausentes,
        )
        return ProcessResult(
            import_id=record.id,
            competence=competence,
            rows=len(lines),
            columns_recognized=columns_recognized,
            categories_created=categories_created,
            absent=outcome.ausentes,
            mapping_id=mapping.id,
            processed_at=record.processed_at,
        )

    async def _require_file_connection(self, client: Client) -> None:
        """Conexão `arquivo` ATIVA, pela taxonomia dos três 409 da S9.

        `select_capable_connection` decide `SEM_CONEXAO`/`ORIGEM_COM_ERRO`; se a
        conexão capaz não for `arquivo` e nenhuma `arquivo` ativa existir, é
        `CAPACIDADE_AUSENTE` — o cliente tem origem, mas não é por arquivo.
        """
        connections = await resolve_origin_connections(self._db, client, settings=self._settings)
        chosen = select_capable_connection(connections, Capability.LISTAR_LANCAMENTOS)
        if chosen.provider_type == ProviderType.ARQUIVO.value:
            return
        if any(
            c.provider_type == ProviderType.ARQUIVO.value
            and c.status == ConnectionStatus.ATIVA.value
            for c in connections
        ):
            return
        raise OriginCapabilityMissingError(
            f"Cliente {client.id}: nenhuma conexão `arquivo` ativa.",
            user_message=(
                "Este cliente não tem uma origem por arquivo. Conecte uma origem do tipo "
                "arquivo para enviar planilhas."
            ),
        )

    async def _columns_without_mapping(self, client: Client, content: bytes) -> list[str]:
        """As colunas do cabeçalho para a tela conduzir a criação do mapeamento.

        Sem mapeamento não há delimitador/codificação declarados: usa os padrões da
        inspeção. Se o arquivo nem abrir, a recusa é a dele (formato/inválido), com
        o motivo correspondente.
        """
        try:
            detected = detect_format(content)
            options = self._read_options(detected, None, None, None)
            table = await run_in_threadpool(read_table, content, options, limit=0)
        except AppError as exc:
            motivo: FileRejectionReason = (
                "formato_nao_suportado"
                if exc.code.value == "FORMATO_NAO_SUPORTADO"
                else "arquivo_invalido"
            )
            await self._refuse(client, exc, motivo=motivo, mapping_id=None)
        return table.columns

    @staticmethod
    def _read_options(
        detected: InputFileFormat,
        mapping: ClientInputMapping | None,
        csv_delimiter: str | None,
        encoding: str | None,
    ) -> ReadOptions:
        """Delimitador e codificação: do mapeamento, senão do pedido, senão os padrões."""
        from_mapping = mapping is not None and mapping.file_format == detected.value
        return ReadOptions(
            file_format=detected,
            csv_delimiter=(
                (mapping.csv_delimiter if from_mapping and mapping else None)
                or csv_delimiter
                or DEFAULT_INSPECT_DELIMITER
            ),
            encoding=(
                (mapping.encoding if from_mapping and mapping else None)
                or encoding
                or DEFAULT_INSPECT_ENCODING
            ),
        )

    @staticmethod
    def _header_checker(spec: MappingSpec) -> Callable[[list[str]], None]:
        """Toda coluna mapeada precisa existir no cabeçalho — ANTES da primeira linha.

        ⚠️ `details` nomeia só as colunas DO MAPEAMENTO (configuração do escritório).
        O cabeçalho lido sai como CONTAGEM: num arquivo enviado sem cabeçalho a
        linha 1 é dado, e devolvê-la crua ecoava a descrição do lançamento, que é
        do cliente final e nasce cifrada (§4.1/§4.5).
        """

        def _check(columns: list[str]) -> None:
            missing = [name for name in spec.mapped_columns if name not in columns]
            if missing:
                raise FileHeaderMismatchError(
                    f"{len(missing)} coluna(s) do mapeamento ausente(s) no cabeçalho.",
                    details={"missingColumns": missing, "foundColumnCount": len(columns)},
                )

        return _check

    async def _refuse(
        self,
        client: Client,
        exc: AppError,
        *,
        motivo: FileRejectionReason,
        mapping_id: UUID | None,
        linhas: int = 0,
        colunas: int = 0,
    ) -> NoReturn:
        """Emite `arquivo_processado{rejeitado=true}` pelo caminho DURÁVEL e levanta.

        O emissor (14.2) commita antes de devolver — a linha sobrevive ao rollback
        que o `get_db_session` aplica à exceção. Nada de negócio foi escrito antes
        de qualquer recusa (a ordem do `process` garante), então o commit persiste só
        a métrica. O texto da exceção nunca carrega célula (mensagens fixas).
        """
        await self._usage_events.emit_arquivo_processado(
            client_id=client.id,
            mapeamento_id=mapping_id,
            linhas=linhas,
            colunas_reconhecidas=colunas,
            rejeitado=True,
            motivo=motivo,
        )
        raise exc


def table_rows(table: ParsedTable) -> int:
    """Quantas linhas de dados a tabela trouxe (helper para testes e log)."""
    return len(table.rows)
