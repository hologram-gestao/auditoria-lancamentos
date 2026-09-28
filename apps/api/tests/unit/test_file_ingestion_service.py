"""O serviço de ingestão do arquivo, sem banco (BACK 14.3 — R1/R2/R3).

O banco é substituído por dublês que registram a ORDEM das chamadas — é isso que
prova, sem Postgres, o que a integração não consegue isolar:

  - a ORDEM das recusas (conexão → mapeamento → sinal → formato → hash → cabeçalho →
    linhas → total) e que cada uma emite exatamente UM `arquivo_processado{rejeitado=
    true, motivo}` e NÃO escreve nada (nem registro, nem categoria, nem movimento);
  - o cabeçalho é conferido com ZERO células processadas;
  - o caminho feliz grava na ordem `lock → registro → categorias → ciclo R0 → commit`
    e só DEPOIS emite o aceito; a identidade da linha é `<16 hex do hash>:<linha>`
    (duas linhas idênticas = dois movimentos); a base recebe `source_type='arquivo'`,
    sem recorte de conta e sem o evento de sincronização;
  - a corrida na UNIQUE do registro vira `ARQUIVO_JA_PROCESSADO` sem tocar o ciclo;
  - nenhum conteúdo de célula nem nome de coluna em log, evento ou mensagem de erro.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from openpyxl import Workbook

from app.core.config import get_settings
from app.core.exceptions import (
    ClientClosedError,
    FileAlreadyProcessedError,
    FileFormatNotSupportedError,
    FileHeaderMismatchError,
    FileInvalidError,
    FileLinesInvalidError,
    FileTotalMismatchError,
    NoInputMappingError,
    NoOriginConnectionError,
    OriginCapabilityMissingError,
    SignConventionMissingError,
)
from app.db.models import Client
from app.modules.client_file_categories.registry import ResolvedFileCategoryNames
from app.modules.client_file_ingestion import reader
from app.modules.client_file_ingestion import service as service_module
from app.modules.client_file_ingestion.reader import MAX_INVALID_LINES_REPORTED, SAMPLE_ROWS
from app.modules.client_file_ingestion.service import (
    DEFAULT_INSPECT_DELIMITER,
    DEFAULT_INSPECT_ENCODING,
    FileIngestionService,
)
from app.modules.client_movements.service import MovementSyncResult

SEGREDO = "PAGTO ACME LTDA SEGREDO DA CELULA"
JUN = date(2026, 6, 1)
HEADER = ["Data", "Histórico", "Valor", "Categoria", "Documento"]
ROWS: list[list[Any]] = [
    ["05/06/2026", SEGREDO, "-1.500,00", "Aluguel", "NF 1"],
    ["10/06/2026", "Energia", "-320,45", "Energia", "NF 2"],
    ["15/06/2026", "Venda", "980,00", "Receita", ""],
]
TOTAL = Decimal("-840.45")


# ---------------------------------------------------------------------------
# Construtores
# ---------------------------------------------------------------------------


def _csv(rows: list[list[Any]], header: list[str] | None = None) -> bytes:
    lines = [";".join(str(c) for c in (header or HEADER))]
    lines.extend(";".join("" if c is None else str(c) for c in row) for row in rows)
    return ("\n".join(lines) + "\n").encode("utf-8")


def _xlsx(rows: list[list[Any]], header: list[str] | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.append(header or HEADER)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _mapping(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "id": uuid4(),
        "file_format": "csv",
        "csv_delimiter": ";",
        "encoding": "utf-8",
        "date_column": "Data",
        "description_column": "Histórico",
        "amount_column": "Valor",
        "category_column": "Categoria",
        "category_mode": "coluna_categoria",
        "account_column": None,
        "document_column": "Documento",
        "date_format": "dd/mm/yyyy",
        "decimal_separator": ",",
        "sign_convention": "valor_com_sinal",
        "nature_column": None,
        "debit_value": None,
        "credit_value": None,
        "debit_column": None,
        "credit_column": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _client(*, closed: bool = False) -> Client:
    client = Client(id=uuid4(), name="Cliente sem sistema", active=True, created_by=uuid4())
    client.closed_at = datetime.now(UTC) if closed else None
    return client


def _actor() -> SimpleNamespace:
    return SimpleNamespace(id=str(uuid4()))


# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------


@dataclass
class _Ledger:
    calls: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    persists: list[dict[str, Any]] = field(default_factory=list)
    resolved_labels: list[set[str]] = field(default_factory=list)


class _Db:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    async def commit(self) -> None:
        self._ledger.calls.append("commit")

    async def refresh(self, record: Any) -> None:
        self._ledger.calls.append("refresh")


class _Mappings:
    def __init__(self, row: Any) -> None:
        self._row = row

    async def get_row(self, client: Any) -> Any:
        return self._row


class _Registry:
    def __init__(self, ledger: _Ledger, *, existing: dict[str, str] | None = None) -> None:
        self._ledger = ledger
        self._existing = dict(existing or {})

    async def resolve_names(self, client: Any) -> ResolvedFileCategoryNames:
        self._ledger.calls.append("resolve_names")
        return ResolvedFileCategoryNames(
            names={code: label for label, code in self._existing.items()}
        )

    async def resolve_codes(self, client: Any, labels: set[str]) -> dict[str, str]:
        self._ledger.calls.append("resolve_codes")
        self._ledger.resolved_labels.append(set(labels))
        out: dict[str, str] = {}
        for label in sorted(labels):
            out[label] = self._existing.setdefault(label, f"arq-{uuid4().hex[:12]}")
        return out


class _Movements:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    async def persist_entries(self, client: Any, competence: date, **kwargs: Any) -> Any:
        self._ledger.calls.append("persist")
        self._ledger.persists.append({"competence": competence, **kwargs})
        entries = kwargs["entries"]
        return MovementSyncResult(
            competence=competence,
            synced_at=datetime.now(UTC),
            movimentos=len(entries),
            sem_categoria=sum(1 for _, e in entries if e.category_code is None),
            contas=0,
            ausentes=kwargs.get("_ausentes", 0),
        )


class _Imports:
    def __init__(self, ledger: _Ledger, *, exists: bool = False, add_ok: bool = True) -> None:
        self._ledger = ledger
        self._exists = exists
        self._add_ok = add_ok
        self.exists_args: list[dict[str, Any]] = []
        self.records: list[Any] = []

    async def lock_client(self, client_id: UUID) -> None:
        self._ledger.calls.append("lock")

    async def exists(self, client_id: UUID, *, competence: date, file_hash: str) -> bool:
        self.exists_args.append({"competence": competence, "file_hash": file_hash})
        return self._exists

    async def add(self, record: Any) -> bool:
        self._ledger.calls.append("add")
        if not self._add_ok:
            return False
        record.id = uuid4()
        record.processed_at = datetime.now(UTC)
        self.records.append(record)
        return True

    async def list_for_client(
        self, client_id: UUID, *, competence: date | None = None
    ) -> list[Any]:
        return list(self.records)


class _Events:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    async def emit_arquivo_processado(self, **kwargs: Any) -> bool:
        self._ledger.calls.append("emit")
        self._ledger.events.append(kwargs)
        return True


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mapping: Any = None,
    exists: bool = False,
    add_ok: bool = True,
    existing_labels: dict[str, str] | None = None,
    connection: bool = True,
) -> tuple[FileIngestionService, _Ledger, _Imports]:
    ledger = _Ledger()
    if connection:

        async def _ok(self: Any, client: Any) -> None:
            ledger.calls.append("connection")

        monkeypatch.setattr(FileIngestionService, "_require_file_connection", _ok)
    imports = _Imports(ledger, exists=exists, add_ok=add_ok)
    service = FileIngestionService(
        _Db(ledger),  # type: ignore[arg-type]
        settings=get_settings(),
        mappings=_Mappings(mapping),  # type: ignore[arg-type]
        registry=_Registry(ledger, existing=existing_labels),  # type: ignore[arg-type]
        movements=_Movements(ledger),  # type: ignore[arg-type]
        imports=imports,  # type: ignore[arg-type]
        usage_events=_Events(ledger),  # type: ignore[arg-type]
    )
    return service, ledger, imports


async def _process(
    service: FileIngestionService,
    content: bytes,
    *,
    client: Client | None = None,
    declared_total: Decimal | None = None,
) -> Any:
    return await service.process(
        client or _client(),
        actor=_actor(),  # type: ignore[arg-type]
        content=content,
        competence=JUN,
        declared_total=declared_total,
    )


def _assert_only_refused(ledger: _Ledger, motivo: str, **props: Any) -> None:
    """Exatamente UM evento de recusa, e nada escrito (nem lock)."""
    (event,) = ledger.events
    assert event["rejeitado"] is True
    assert event["motivo"] == motivo
    for key, value in props.items():
        assert event[key] == value, key
    assert "lock" not in ledger.calls
    assert "add" not in ledger.calls
    assert "resolve_codes" not in ledger.calls
    assert "persist" not in ledger.calls
    assert "commit" not in ledger.calls


# ---------------------------------------------------------------------------
# Recusas, na ordem
# ---------------------------------------------------------------------------


class TestRecusasNaOrdem:
    async def test_cliente_encerrado_e_409_antes_de_tudo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        with pytest.raises(ClientClosedError) as exc:
            await _process(service, _csv(ROWS), client=_client(closed=True))
        assert exc.value.status_code == 409
        assert ledger.calls == []
        assert ledger.events == []

    async def test_sem_mapeamento_e_409_com_as_colunas_encontradas(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=None)
        with pytest.raises(NoInputMappingError) as exc:
            await _process(service, _csv(ROWS))
        assert exc.value.status_code == 409
        assert exc.value.code.value == "SEM_MAPEAMENTO"
        assert exc.value.details == {"foundColumns": HEADER}
        assert SEGREDO not in str(exc.value)
        _assert_only_refused(
            ledger, "sem_mapeamento", mapeamento_id=None, linhas=0, colunas_reconhecidas=5
        )

    async def test_sem_mapeamento_com_arquivo_que_nem_abre_e_a_recusa_do_arquivo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=None)
        with pytest.raises(FileFormatNotSupportedError):
            await _process(service, b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n")
        _assert_only_refused(ledger, "formato_nao_suportado", mapeamento_id=None)

    async def test_sinal_nao_declarado_e_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mapping = _mapping(sign_convention=None)
        service, ledger, _ = _wire(monkeypatch, mapping=mapping)
        with pytest.raises(SignConventionMissingError) as exc:
            await _process(service, _csv(ROWS))
        assert exc.value.code.value == "SINAL_NAO_DECLARADO"
        _assert_only_refused(ledger, "sinal_nao_declarado", mapeamento_id=mapping.id)

    async def test_formato_diferente_do_mapeamento_e_422(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping(file_format="csv"))
        with pytest.raises(FileFormatNotSupportedError) as exc:
            await _process(service, _xlsx(ROWS))
        assert exc.value.status_code == 422
        assert "XLSX" in exc.value.user_message
        _assert_only_refused(ledger, "formato_nao_suportado")

    async def test_pdf_e_422_com_motivo_acionavel(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        with pytest.raises(FileFormatNotSupportedError) as exc:
            await _process(service, b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n")
        assert "PDF" in exc.value.user_message
        _assert_only_refused(ledger, "formato_nao_suportado")

    async def test_hash_ja_processado_e_409_pelo_hash_do_conteudo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, imports = _wire(monkeypatch, mapping=_mapping(), exists=True)
        content = _csv(ROWS)
        with pytest.raises(FileAlreadyProcessedError) as exc:
            await _process(service, content)
        assert exc.value.code.value == "ARQUIVO_JA_PROCESSADO"
        assert imports.exists_args == [
            {"competence": JUN, "file_hash": hashlib.sha256(content).hexdigest()}
        ]
        _assert_only_refused(ledger, "arquivo_ja_processado")

    async def test_cabecalho_divergente_e_422_antes_da_primeira_linha(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O exemplo do PRD: «Histórico» virou «Descrição»."""
        processadas = {"celulas": 0}
        original = reader._cell

        def _spy(value: Any) -> Any:
            processadas["celulas"] += 1
            return original(value)

        monkeypatch.setattr(reader, "_cell", _spy)
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        header = ["Data", "Descrição", "Valor", "Categoria", "Documento"]
        with pytest.raises(FileHeaderMismatchError) as exc:
            await _process(service, _csv(ROWS, header))
        assert exc.value.status_code == 422
        assert exc.value.code.value == "CABECALHO_DIVERGENTE"
        assert exc.value.details == {"missingColumns": ["Histórico"], "foundColumns": header}
        assert processadas["celulas"] == 0
        assert SEGREDO not in str(exc.value)
        _assert_only_refused(ledger, "cabecalho_divergente", linhas=0, colunas_reconhecidas=4)

    async def test_linhas_invalidas_e_422_com_numero_e_motivo_nunca_a_celula(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        rows: list[list[Any]] = [
            ["05/06/2026", SEGREDO, "abc", "A", ""],
            ["99/99/2026", "x", "1,00", "A", ""],
            ["05/06/2026", "y", "", "A", ""],
            ["05/06/2026", "ok", "1,00", "A", ""],
        ]
        with pytest.raises(FileLinesInvalidError) as exc:
            await _process(service, _csv(rows))
        assert exc.value.status_code == 422
        assert exc.value.code.value == "LINHAS_INVALIDAS"
        assert exc.value.details == {
            "lines": [
                {"line": 2, "reason": "valor_nao_numerico"},
                {"line": 3, "reason": "data_invalida"},
                {"line": 4, "reason": "campo_obrigatorio_vazio"},
            ],
            "total": 3,
        }
        assert SEGREDO not in repr(exc.value.details)
        assert SEGREDO not in str(exc.value)
        _assert_only_refused(ledger, "linhas_invalidas", linhas=4, colunas_reconhecidas=5)

    async def test_linhas_relatadas_sao_limitadas_e_o_total_vai_a_parte(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, _, _ = _wire(monkeypatch, mapping=_mapping())
        rows = [["05/06/2026", "x", "abc", "A", ""] for _ in range(MAX_INVALID_LINES_REPORTED + 10)]
        with pytest.raises(FileLinesInvalidError) as exc:
            await _process(service, _csv(rows))
        assert len(exc.value.details["lines"]) == MAX_INVALID_LINES_REPORTED
        assert exc.value.details["total"] == MAX_INVALID_LINES_REPORTED + 10

    async def test_total_divergente_e_422_com_os_dois_totais_exatos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        with pytest.raises(FileTotalMismatchError) as exc:
            await _process(service, _csv(ROWS), declared_total=Decimal("-840.46"))
        assert exc.value.status_code == 422
        assert exc.value.code.value == "TOTAL_DIVERGENTE"
        assert exc.value.details == {"declaredTotal": "-840.46", "computedTotal": "-840.45"}
        _assert_only_refused(ledger, "total_divergente", linhas=3, colunas_reconhecidas=5)

    async def test_total_informado_igual_passa(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        result = await _process(service, _csv(ROWS), declared_total=TOTAL)
        assert result.rows == 3
        assert "persist" in ledger.calls

    async def test_arquivo_que_nao_abre_e_422_com_mensagem_fixa(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mapping = _mapping(file_format="xlsx", csv_delimiter=None, encoding=None)
        service, ledger, _ = _wire(monkeypatch, mapping=mapping)
        with pytest.raises(FileInvalidError) as exc:
            await _process(service, b"PK\x03\x04" + b"\xff" * 64)
        assert exc.value.status_code == 422
        assert exc.value.code.value == "ARQUIVO_INVALIDO"
        assert exc.value.user_message == FileInvalidError.default_user_message
        assert exc.value.__cause__ is None
        _assert_only_refused(ledger, "arquivo_invalido", mapeamento_id=mapping.id)

    async def test_a_conexao_e_conferida_antes_do_mapeamento(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=None)
        with pytest.raises(NoInputMappingError):
            await _process(service, _csv(ROWS))
        assert ledger.calls[0] == "connection"


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------


class TestGravacao:
    async def test_ordem_lock_registro_categorias_ciclo_commit_e_so_depois_o_evento(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        mapping = _mapping()
        service, ledger, imports = _wire(monkeypatch, mapping=mapping)
        content = _csv(ROWS)
        client = _client()

        with caplog.at_level(logging.INFO):
            result = await _process(service, content, client=client)

        assert ledger.calls == [
            "connection",
            "lock",
            "add",
            "resolve_names",
            "resolve_codes",
            "persist",
            "refresh",
            "commit",
            "emit",
        ]
        # O registro do arquivo: hash do conteúdo, mapeamento aplicado, autor, linhas.
        (record,) = imports.records
        assert record.client_id == client.id
        assert record.competence == JUN
        assert record.file_hash == hashlib.sha256(content).hexdigest()
        assert record.mapping_id == mapping.id
        assert record.rows == 3

        # O ciclo R0 recebe `source_type='arquivo'`, sem recorte de conta e sem o
        # evento de sincronização — a ingestão tem o evento dela.
        (persist,) = ledger.persists
        assert persist["source_type"] == "arquivo"
        assert persist["accounts"] is None
        assert persist["emit"] is False
        assert persist["competence"] == JUN
        prefix = record.file_hash[:16]
        ids = [entry.external_id for _, entry in persist["entries"]]
        assert ids == [f"{prefix}:2", f"{prefix}:3", f"{prefix}:4"]
        assert all(account is None for account, _ in persist["entries"])
        entries = {e.external_id: e for _, e in persist["entries"]}
        assert entries[f"{prefix}:2"].amount == Decimal("-1500.00")
        assert entries[f"{prefix}:2"].entry_date == date(2026, 6, 5)
        assert entries[f"{prefix}:2"].document_number == "NF 1"
        assert entries[f"{prefix}:4"].document_number is None
        assert entries[f"{prefix}:2"].supplier_code is None
        assert all(
            e.category_code is not None and e.category_code.startswith("arq-")
            for e in entries.values()
        )
        # A descrição vai à parte, por identidade da linha — para ser cifrada pela pk.
        assert persist["descriptions"] == {
            f"{prefix}:2": SEGREDO,
            f"{prefix}:3": "Energia",
            f"{prefix}:4": "Venda",
        }
        assert ledger.resolved_labels == [{"Aluguel", "Energia", "Receita"}]

        # O aceito sai DEPOIS do commit, com as contagens do resultado.
        (event,) = ledger.events
        assert event == {
            "client_id": client.id,
            "mapeamento_id": mapping.id,
            "linhas": 3,
            "colunas_reconhecidas": 5,
            "rejeitado": False,
            "motivo": "nenhum",
        }
        assert (result.rows, result.columns_recognized, result.categories_created) == (3, 5, 3)
        assert result.absent == 0
        assert result.import_id == record.id
        assert result.mapping_id == mapping.id

        # Nada da célula nem do cabeçalho em log.
        assert SEGREDO not in caplog.text
        for column in HEADER:
            assert column not in caplog.text

    async def test_duas_linhas_identicas_sao_dois_movimentos(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        rows = [ROWS[0], ROWS[0]]
        result = await _process(service, _csv(rows))
        assert result.rows == 2
        ids = [entry.external_id for _, entry in ledger.persists[0]["entries"]]
        assert len(set(ids)) == 2

    async def test_celula_de_categoria_vazia_e_categoria_nula(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        rows: list[list[Any]] = [
            ["05/06/2026", "x", "1,00", "", ""],
            ["06/06/2026", "y", "1,00", "A", ""],
        ]
        await _process(service, _csv(rows))
        entries = [e for _, e in ledger.persists[0]["entries"]]
        assert entries[0].category_code is None
        assert entries[1].category_code is not None
        assert ledger.resolved_labels == [{"A"}]

    async def test_sem_coluna_de_categoria_nao_toca_o_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping(category_column=None))
        await _process(service, _csv(ROWS))
        assert "resolve_codes" not in ledger.calls
        assert "resolve_names" not in ledger.calls
        assert all(e.category_code is None for _, e in ledger.persists[0]["entries"])

    async def test_categorias_criadas_conta_so_as_novas_deste_arquivo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O cliente já tinha 3 categorias (1 delas neste arquivo): criadas = 2, não 3 - 3."""
        service, _, _ = _wire(
            monkeypatch,
            mapping=_mapping(),
            existing_labels={
                "Aluguel": "arq-000000000001",
                "Outra de julho": "arq-000000000002",
                "Mais uma": "arq-000000000003",
            },
        )
        result = await _process(service, _csv(ROWS))
        assert result.categories_created == 2

    async def test_coluna_de_conta_vai_para_a_linha(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mapping = _mapping(account_column="Conta")
        service, ledger, _ = _wire(monkeypatch, mapping=mapping)
        header = [*HEADER, "Conta"]
        rows = [[*ROWS[0], "12345"], [*ROWS[1], ""]]
        await _process(service, _csv(rows, header))
        accounts = [account for account, _ in ledger.persists[0]["entries"]]
        assert accounts == ["12345", None]

    async def test_corrida_na_unique_do_registro_e_409_sem_tocar_o_ciclo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Duas abas: a leitura amigável passou nas duas; a UNIQUE decidiu na segunda."""
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping(), add_ok=False)
        with pytest.raises(FileAlreadyProcessedError):
            await _process(service, _csv(ROWS))
        assert ledger.calls == ["connection", "lock", "add", "emit"]
        (event,) = ledger.events
        assert event["motivo"] == "arquivo_ja_processado"
        assert event["rejeitado"] is True
        assert "persist" not in ledger.calls
        assert "commit" not in ledger.calls

    async def test_xlsx_pelo_mapeamento_xlsx(self, monkeypatch: pytest.MonkeyPatch) -> None:
        mapping = _mapping(file_format="xlsx", csv_delimiter=None, encoding=None)
        service, ledger, _ = _wire(monkeypatch, mapping=mapping)
        rows: list[list[Any]] = [
            [datetime(2026, 6, 5), SEGREDO, -1500.0, "Aluguel", "NF 1"],
            [datetime(2026, 6, 10), "Energia", -320.45, "Energia", 2],
        ]
        result = await _process(service, _xlsx(rows))
        assert result.rows == 2
        entries = [e for _, e in ledger.persists[0]["entries"]]
        assert entries[0].amount == Decimal("-1500.00")
        assert entries[1].amount == Decimal("-320.45")
        assert entries[1].document_number == "2"

    async def test_lista_de_arquivos_processados(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, _, imports = _wire(monkeypatch, mapping=_mapping())
        client = _client()
        await _process(service, _csv(ROWS), client=client)
        assert await service.list_imports(client, competence=None) == imports.records


# ---------------------------------------------------------------------------
# Inspeção
# ---------------------------------------------------------------------------


class TestInspecao:
    async def test_cliente_encerrado_e_409(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        with pytest.raises(ClientClosedError):
            await service.inspect(
                _client(closed=True), _csv(ROWS), csv_delimiter=None, encoding=None
            )
        assert ledger.calls == []

    async def test_formato_colunas_e_amostra_sem_persistir_nem_emitir(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        service, ledger, _ = _wire(monkeypatch, mapping=_mapping())
        rows = [ROWS[0]] * (SAMPLE_ROWS + 3)
        with caplog.at_level(logging.INFO):
            result = await service.inspect(_client(), _csv(rows), csv_delimiter=None, encoding=None)
        assert result.file_format.value == "csv"
        assert result.columns == HEADER
        assert len(result.sample) == SAMPLE_ROWS
        assert result.sample[0] == ["05/06/2026", SEGREDO, "-1.500,00", "Aluguel", "NF 1"]
        assert result.has_mapping is True
        assert ledger.calls == ["connection"]
        assert ledger.events == []
        assert SEGREDO not in caplog.text

    async def test_sem_mapeamento_le_com_os_padroes_ou_com_o_pedido(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, _, _ = _wire(monkeypatch, mapping=None)
        assert (DEFAULT_INSPECT_DELIMITER, DEFAULT_INSPECT_ENCODING) == (";", "utf-8-sig")
        com_bom = b"\xef\xbb\xbf" + _csv(ROWS)
        result = await service.inspect(_client(), com_bom, csv_delimiter=None, encoding=None)
        assert result.columns == HEADER  # o BOM não vira parte do nome da coluna
        assert result.has_mapping is False

        virgula = _csv(ROWS).replace(b";", b",")
        result = await service.inspect(_client(), virgula, csv_delimiter=",", encoding="utf-8")
        assert result.columns == HEADER

    async def test_com_mapeamento_do_mesmo_formato_o_delimitador_e_o_do_mapeamento(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, _, _ = _wire(monkeypatch, mapping=_mapping(csv_delimiter="|"))
        pipe = _csv(ROWS).replace(b";", b"|")
        # O pedido diz `,`; o mapeamento diz `|` — o mapeamento vence.
        result = await service.inspect(_client(), pipe, csv_delimiter=",", encoding=None)
        assert result.columns == HEADER

    async def test_xlsx_e_inspecionado_mesmo_com_mapeamento_csv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A inspeção mostra o que veio; é o PROCESSAMENTO que exige o formato do mapeamento."""
        service, _, _ = _wire(monkeypatch, mapping=_mapping(file_format="csv"))
        result = await service.inspect(_client(), _xlsx(ROWS), csv_delimiter=None, encoding=None)
        assert result.file_format.value == "xlsx"
        assert result.columns == HEADER

    async def test_pdf_e_422(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, _, _ = _wire(monkeypatch, mapping=_mapping())
        with pytest.raises(FileFormatNotSupportedError):
            await service.inspect(
                _client(),
                b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n",
                csv_delimiter=None,
                encoding=None,
            )


# ---------------------------------------------------------------------------
# Conexão `arquivo`
# ---------------------------------------------------------------------------


def _conn(provider_type: str, status: str = "ativa") -> SimpleNamespace:
    return SimpleNamespace(provider_type=provider_type, status=status, accounts_synced_at=None)


class TestConexaoArquivo:
    async def _service_with(
        self, monkeypatch: pytest.MonkeyPatch, connections: list[SimpleNamespace]
    ) -> FileIngestionService:
        async def _resolve(*_a: Any, **_k: Any) -> list[SimpleNamespace]:
            return connections

        monkeypatch.setattr(service_module, "resolve_origin_connections", _resolve)
        service, _, _ = _wire(monkeypatch, mapping=_mapping(), connection=False)
        return service

    async def test_sem_conexao_e_409_sem_conexao(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = await self._service_with(monkeypatch, [])
        with pytest.raises(NoOriginConnectionError) as exc:
            await _process(service, _csv(ROWS))
        assert exc.value.status_code == 409

    async def test_so_omie_e_409_capacidade_ausente_com_instrucao(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = await self._service_with(monkeypatch, [_conn("omie")])
        with pytest.raises(OriginCapabilityMissingError) as exc:
            await _process(service, _csv(ROWS))
        assert exc.value.code.value == "CAPACIDADE_AUSENTE"
        assert "arquivo" in exc.value.user_message

    async def test_arquivo_ativa_passa_mesmo_ao_lado_do_omie(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service = await self._service_with(monkeypatch, [_conn("omie"), _conn("arquivo")])
        result = await _process(service, _csv(ROWS))
        assert result.rows == 3

    async def test_arquivo_em_erro_nao_passa(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service = await self._service_with(monkeypatch, [_conn("arquivo", status="erro")])
        with pytest.raises(Exception) as exc:  # noqa: PT011 - qualquer 409 da taxonomia
            await _process(service, _csv(ROWS))
        assert getattr(exc.value, "status_code", None) == 409
