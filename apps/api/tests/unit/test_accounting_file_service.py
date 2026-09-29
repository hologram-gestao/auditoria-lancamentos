"""Gerar, listar e baixar sem banco (BACK 13.4): ordem das escritas, recusas e divergência.

Dublês registram a ORDEM: a geração e a trilha são gravadas e COMMITADAS antes do evento e
da resposta; recusas não gravam nada; a competência sem materialização nunca materializa;
o download regenera e, se o SHA-256 não bate, alerta e recusa sem entregar bytes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.core.authz import CurrentUser
from app.core.exceptions import (
    AccountingFileDivergentError,
    AccountingFileNoMaterializationError,
    AccountingFileTextDoesNotFitError,
    NotFoundError,
)
from app.db.models import (
    AccessAudit,
    AccountingFileGeneration,
    Client,
    ClientMappingMaterialization,
    ClientMappingMaterializationItem,
    ExportLayout,
    ExportLayoutVersion,
    User,
    UserRole,
    UserScope,
)
from app.modules.accounting_files import service as service_module
from app.modules.accounting_files.repository import GenerationRow
from app.modules.accounting_files.schemas import accounting_file_name
from app.modules.accounting_files.service import (
    AccountingFileService,
    export_line,
    mime_charset,
)
from app.modules.client_mapping.materialization import MaterializedLine
from app.modules.export_layouts.definition import DOMINIO_TEMPLATE

AGO = date(2026, 8, 1)
ORG = uuid4()


def _client() -> Client:
    client = Client(id=uuid4(), name="Cliente Sigiloso", active=True, created_by=uuid4())
    client.organization_id = ORG
    client.closed_at = None
    return client


def _actor() -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="g@x.com",
        name="Gerente",
        role=UserRole.MANAGER.value,
        scope=UserScope.SYSTEM.value,
        client_id=None,
        organization_id=ORG,
    )


def _item(
    smid: str, amount: str, history: str | None = "RECEBIMENTO ALUGUEL", **over: Any
) -> MaterializedLine:
    base: dict[str, Any] = {
        "id": uuid4(),
        "materialization_id": uuid4(),
        "client_id": uuid4(),
        "source_type": "arquivo",
        "source_movement_id": smid,
        "source_account_id": None,
        "movement_date": date(2026, 8, 5),
        "amount": Decimal(amount),
        "category_code": "C1",
        "situation": "alvo",
        "target_code": None,
        "decision_effective_from": AGO,
        "accounting_account_code": "662",
        "decision_id": uuid4(),
        "bank_account_code": "649",
        "history_present": True,
    }
    return MaterializedLine(item=ClientMappingMaterializationItem(**(base | over)), history=history)


def _materialization(client: Client, *, version: int = 1, **over: Any) -> Any:
    m = ClientMappingMaterialization(
        id=uuid4(),
        client_id=client.id,
        destination_type="conta_contabil",
        competence=AGO,
        version=version,
        partial_coverage_confirmed=False,
        not_mapped_amount=Decimal("0.00"),
        undecided_amount=Decimal("0.00"),
        uncategorized_amount=Decimal("0.00"),
    )
    for key, value in over.items():
        setattr(m, key, value)
    return m


class _Ledger:
    def __init__(self) -> None:
        self.calls: list[str] = []


class _Db:
    def __init__(self, ledger: _Ledger) -> None:
        self.ledger = ledger
        self.audits: list[AccessAudit] = []

    def add(self, obj: Any) -> None:
        if isinstance(obj, AccessAudit):
            self.ledger.calls.append(f"audit:{obj.action}")
            self.audits.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.ledger.calls.append("db_commit")


class _Repo:
    def __init__(self, ledger: _Ledger, materializations: list[Any]) -> None:
        self.ledger = ledger
        self.materializations = materializations
        self.generations: dict[UUID, AccountingFileGeneration] = {}

    async def latest_accounting_materialization(self, client_id: UUID, competence: date) -> Any:
        mine = [
            m
            for m in self.materializations
            if m.client_id == client_id
            and m.competence == competence
            and m.destination_type == "conta_contabil"
        ]
        return max(mine, key=lambda m: m.version) if mine else None

    async def get_materialization(self, client_id: UUID, materialization_id: UUID) -> Any:
        return next(
            (
                m
                for m in self.materializations
                if m.id == materialization_id and m.client_id == client_id
            ),
            None,
        )

    async def insert_generation(self, generation: AccountingFileGeneration) -> None:
        generation.id = uuid4()
        generation.created_at = datetime.now(UTC)
        self.generations[generation.id] = generation
        self.ledger.calls.append("insert")

    async def get_generation(self, client_id: UUID, generation_id: UUID) -> Any:
        g = self.generations.get(generation_id)
        return g if g is not None and g.client_id == client_id else None

    async def get_row(self, client_id: UUID, generation_id: UUID) -> GenerationRow | None:
        g = await self.get_generation(client_id, generation_id)
        if g is None:
            return None
        m = await self.get_materialization(client_id, g.materialization_id)
        author = User(
            name="Gerente",
            email="g@x.com",
            role=UserRole.MANAGER.value,
            scope=UserScope.SYSTEM.value,
            password_hash="x",
            active=True,
        )
        return GenerationRow(
            generation=g, author=author, materialization_version=m.version, layout_name="Domínio"
        )

    async def commit(self) -> None:
        self.ledger.calls.append("commit")


class _Layouts:
    def __init__(self, organization_id: UUID) -> None:
        self.layout = ExportLayout(
            id=uuid4(), organization_id=organization_id, name="Domínio", target_system="Domínio"
        )
        self.version = ExportLayoutVersion(
            layout_id=self.layout.id, version=1, definition=DOMINIO_TEMPLATE.definition.to_json()
        )

    async def get_layout_for_organization(self, organization_id: UUID, layout_id: UUID) -> Any:
        ok = layout_id == self.layout.id and organization_id == self.layout.organization_id
        return self.layout if ok else None

    async def latest_version(self, layout_id: UUID) -> int:
        return 1

    async def get_version(self, layout_id: UUID, version: int) -> Any:
        return self.version if (layout_id, version) == (self.layout.id, 1) else None


class _Apply:
    def __init__(self, lines: list[MaterializedLine]) -> None:
        self.lines = lines

    async def materialized_lines(self, client: Any, materialization_id: UUID) -> Any:
        return self.lines


class _Events:
    def __init__(self, ledger: _Ledger, *, fail: bool = False) -> None:
        self.ledger = ledger
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    async def emit_arquivo_contabil_gerado(self, **kwargs: Any) -> bool:
        self.ledger.calls.append("emit")
        if self.fail:
            msg = "sink fora"
            raise RuntimeError(msg)
        self.calls.append(kwargs)
        return True


@pytest.fixture
def world() -> dict[str, Any]:
    ledger = _Ledger()
    client = _client()
    materialization = _materialization(client)
    lines = [_item("L1", "5466.87"), _item("L2", "-0.13")]
    events = _Events(ledger)
    repo = _Repo(ledger, [materialization])
    layouts = _Layouts(ORG)
    service = AccountingFileService(
        _Db(ledger),  # type: ignore[arg-type]
        repository=repo,  # type: ignore[arg-type]
        layouts=layouts,  # type: ignore[arg-type]
        apply=_Apply(lines),  # type: ignore[arg-type]
        settings=MagicMock(),
        usage_events=events,  # type: ignore[arg-type]
    )
    return {
        "ledger": ledger,
        "client": client,
        "materialization": materialization,
        "service": service,
        "repo": repo,
        "layouts": layouts,
        "events": events,
        "lines": lines,
    }


class TestGerar:
    async def test_grava_trilha_commita_e_so_depois_emite(self, world: dict[str, Any]) -> None:
        service: AccountingFileService = world["service"]
        item = await service.generate(
            world["client"], actor=_actor(), layout_id=world["layouts"].layout.id, competence=AGO
        )
        assert world["ledger"].calls == ["insert", "audit:export", "commit", "emit", "commit"]
        assert item.lines == 2
        assert item.total_amount == Decimal("5467.00")
        assert item.file_name == "lancamentos_2026-08_v1.csv"
        (event,) = world["events"].calls
        assert event["materializacao_id"] == world["materialization"].id
        assert event["layout_versao"] == 1
        assert event["linhas"] == 2
        assert event["valor_total"] == Decimal("5467.00")
        assert event["destino"] == "conta_contabil"

    async def test_falha_da_metrica_nao_derruba_a_geracao(self, world: dict[str, Any]) -> None:
        world["events"].fail = True
        service: AccountingFileService = world["service"]
        item = await service.generate(
            world["client"], actor=_actor(), layout_id=world["layouts"].layout.id, competence=AGO
        )
        assert item.lines == 2
        assert world["ledger"].calls[:3] == ["insert", "audit:export", "commit"]

    async def test_sem_materializacao_e_409_e_nada_gravado(self, world: dict[str, Any]) -> None:
        service: AccountingFileService = world["service"]
        with pytest.raises(AccountingFileNoMaterializationError):
            await service.generate(
                world["client"],
                actor=_actor(),
                layout_id=world["layouts"].layout.id,
                competence=date(2026, 9, 1),
            )
        assert world["ledger"].calls == []

    async def test_materializacao_explicita_de_outra_competencia_ou_cliente_e_404(
        self, world: dict[str, Any]
    ) -> None:
        service: AccountingFileService = world["service"]
        for competence, mid in (
            (date(2026, 9, 1), world["materialization"].id),
            (AGO, uuid4()),
        ):
            with pytest.raises(NotFoundError):
                await service.generate(
                    world["client"],
                    actor=_actor(),
                    layout_id=world["layouts"].layout.id,
                    competence=competence,
                    materialization_id=mid,
                )
        assert world["ledger"].calls == []

    async def test_layout_de_outra_organizacao_e_404(self, world: dict[str, Any]) -> None:
        world["layouts"].layout.organization_id = uuid4()
        service: AccountingFileService = world["service"]
        with pytest.raises(NotFoundError):
            await service.generate(
                world["client"],
                actor=_actor(),
                layout_id=world["layouts"].layout.id,
                competence=AGO,
            )
        assert world["ledger"].calls == []

    async def test_recusa_do_gerador_nao_grava(self, world: dict[str, Any]) -> None:
        world["lines"].append(_item("L3", "-1.00", history="ALUGUEL; LOJA", category_code="C9"))
        service: AccountingFileService = world["service"]
        with pytest.raises(AccountingFileTextDoesNotFitError):
            await service.generate(
                world["client"],
                actor=_actor(),
                layout_id=world["layouts"].layout.id,
                competence=AGO,
            )
        assert world["ledger"].calls == []


class TestBaixar:
    async def _generated(self, world: dict[str, Any]) -> UUID:
        service: AccountingFileService = world["service"]
        item = await service.generate(
            world["client"], actor=_actor(), layout_id=world["layouts"].layout.id, competence=AGO
        )
        world["ledger"].calls.clear()
        return item.id

    async def test_regenera_confere_e_entrega(self, world: dict[str, Any]) -> None:
        gid = await self._generated(world)
        service: AccountingFileService = world["service"]
        downloaded = await service.download(world["client"], actor=_actor(), generation_id=gid)
        assert downloaded.content == (
            b"05/08/2026;649;662;R$ 5.466,87;RECEBIMENTO ALUGUEL\r\n"
            b"05/08/2026;662;649;R$ 0,13;RECEBIMENTO ALUGUEL\r\n"
        )
        assert downloaded.media_type == "text/csv; charset=iso-8859-1"
        assert downloaded.file_name == "lancamentos_2026-08_v1.csv"
        assert world["ledger"].calls == ["audit:export", "commit"]

    async def test_sha_divergente_alerta_e_recusa_sem_bytes(
        self, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gid = await self._generated(world)
        world["repo"].generations[gid].sha256 = "f" * 64
        alerts: list[Any] = []

        async def _capture(alert: Any, settings: Any) -> None:
            alerts.append(alert)

        monkeypatch.setattr(service_module, "send_alert", _capture)
        service: AccountingFileService = world["service"]
        with pytest.raises(AccountingFileDivergentError):
            await service.download(world["client"], actor=_actor(), generation_id=gid)
        (alert,) = alerts
        assert alert.code.value == "accounting_file_divergent"
        assert alert.client_id == str(world["client"].id)
        assert "Cliente Sigiloso" not in alert.message
        assert "RECEBIMENTO" not in alert.message
        assert world["ledger"].calls == [], "nenhuma trilha de export sem entrega"

    async def test_regeneracao_recusada_e_divergencia(
        self, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gid = await self._generated(world)
        world["lines"][0] = _item("L1", "5466.87", history=None)
        alerts: list[Any] = []

        async def _capture(alert: Any, settings: Any) -> None:
            alerts.append(alert)

        monkeypatch.setattr(service_module, "send_alert", _capture)
        service: AccountingFileService = world["service"]
        with pytest.raises(AccountingFileDivergentError):
            await service.download(world["client"], actor=_actor(), generation_id=gid)
        assert len(alerts) == 1

    async def test_geracao_de_outro_cliente_e_404(self, world: dict[str, Any]) -> None:
        gid = await self._generated(world)
        service: AccountingFileService = world["service"]
        with pytest.raises(NotFoundError):
            await service.download(_client(), actor=_actor(), generation_id=gid)


class TestAuxiliares:
    @pytest.mark.parametrize(
        ("encoding", "charset"),
        [("latin-1", "iso-8859-1"), ("utf-8", "utf-8"), ("cp1252", "windows-1252")],
    )
    def test_charset(self, encoding: str, charset: str) -> None:
        assert mime_charset(encoding) == charset

    def test_nome_do_arquivo_sem_cliente(self) -> None:
        assert accounting_file_name(date(2026, 8, 1), 3) == "lancamentos_2026-08_v3.csv"

    def test_linha_do_gerador_vem_do_snapshot_e_da_vigencia(self) -> None:
        line = _item("L9", "-2.50", history="HIST")
        converted = export_line(line)
        assert converted.item_id == line.item.id
        assert converted.amount == Decimal("-2.50")
        assert converted.history == "HIST"
        assert converted.bank_account_code == "649"
