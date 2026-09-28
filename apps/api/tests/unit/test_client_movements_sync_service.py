"""A ingestão da base de movimentos sem banco (BACK 12.1 — R0).

O banco é substituído por dublês que registram a ORDEM das chamadas — é isso que
prova, sem Postgres e sem rede, as garantias que um teste de integração não prova
(ADR-019-QA: a fixture `client_with_db` não tem o `rollback()` da produção):

  - a falha da origem carimba, COMMITA e só então re-levanta;
  - os 409 de configuração não carimbam nada;
  - duas sincronizações do mesmo cliente disparadas juntas nunca têm dois
    `list_entries` em voo ao mesmo tempo (o lock injetado é observado);
  - a fixture REAL do extrato, passando pelo adaptador e pelo `OmieClient` de
    verdade (respx no transporte), vira 80 movimentos e deixa as 32 linhas de
    saldo de fora — sem nenhum texto livre da origem.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import respx

from app.core.config import get_settings
from app.core.crypto_service import AAD_MOVEMENT_DESCRIPTION, field_locator, load_client_cipher
from app.core.exceptions import (
    ClientClosedError,
    FileOriginSyncNotApplicableError,
    MovementAccountsUnknownError,
    NoOriginConnectionError,
    OmieFaultError,
)
from app.db.models import Client
from app.integrations.omie.client_locks import OriginClientLocks
from app.integrations.providers.base import ProviderEntry
from app.integrations.providers.omie_adapter import OmieProvider, omie_credentials_payload
from app.modules.client_movements import service as service_module
from app.modules.client_movements.competence import competence_bounds, competence_of
from app.modules.client_movements.repository import MovementCycleOutcome
from app.modules.client_movements.service import (
    ClientMovementsSyncService,
    movement_row,
)

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "omie" / "listar_extrato.response.json"
)
OMIE_EXTRATO_URL = "https://app.omie.com.br/api/v1/financas/extrato/"
JUNHO = date(2026, 6, 1)


@pytest.fixture(autouse=True)
def _no_inter_account_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pausa anti-rate-limit não prova nada aqui — a serialização é do lock."""
    monkeypatch.setattr(service_module, "INTER_ACCOUNT_DELAY_SECONDS", 0)


# ---------------------------------------------------------------------------
# Dublês
# ---------------------------------------------------------------------------


@dataclass
class _Ledger:
    """A ordem das escritas, compartilhada entre o banco e o repositório falsos."""

    calls: list[str] = field(default_factory=list)
    cycles: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    #: S14: as descrições cifradas que o ciclo gravaria por pk (`{b_id, b_ct, b_iv}`).
    descriptions: list[dict[str, Any]] = field(default_factory=list)
    #: Quantas vezes o cache de contas foi renovado (fora de `calls` de propósito:
    #: os testes de ordem das escritas não são sobre a leitura das contas).
    refreshes: int = 0


class _Events:
    """Emissor falso: registra o que a ingestão mandaria para o sink."""

    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    async def emit_movimentos_sincronizados(self, **kwargs: Any) -> bool:
        self._ledger.calls.append("emit")
        self._ledger.events.append(kwargs)
        return True


class _Db:
    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    async def commit(self) -> None:
        self._ledger.calls.append("commit")

    async def rollback(self) -> None:
        self._ledger.calls.append("rollback")


class _AccountsCache:
    """Cache de contas falso: só registra que foi consultado ANTES da origem."""

    def __init__(self, ledger: _Ledger) -> None:
        self._ledger = ledger

    async def get_or_sync(self, client: object, connection: object) -> tuple[list[Any], None]:
        self._ledger.refreshes += 1
        return [], None


class _Repo:
    def __init__(self, ledger: _Ledger, *, fail_cycle: bool = False) -> None:
        self._ledger = ledger
        self._fail_cycle = fail_cycle

    async def reconcile_cycle(
        self,
        client_id: object,
        *,
        source_type: str,
        competence: date,
        rows: list[dict[str, Any]],
        synced_at: datetime,
        accounts_read: list[str] | None,
    ) -> MovementCycleOutcome:
        self._ledger.calls.append("reconcile_cycle")
        if self._fail_cycle:
            raise RuntimeError("banco caiu no meio do ciclo")
        self._ledger.cycles.append(
            {
                "source_type": source_type,
                "competence": competence,
                "rows": list(rows),
                # `None` (S14, origem arquivo) = sem recorte de conta; fica `None`.
                "accounts_read": list(accounts_read) if accounts_read is not None else None,
            }
        )
        return MovementCycleOutcome(upserted=len(rows), absent=0)

    async def ids_by_source(
        self, client_id: object, *, source_type: str, source_ids: list[str]
    ) -> dict[str, UUID]:
        self._ledger.calls.append("ids_by_source")
        return {sid: uuid4() for sid in source_ids}

    async def set_descriptions(self, updates: list[dict[str, Any]]) -> None:
        self._ledger.calls.append("set_descriptions")
        self._ledger.descriptions.extend(updates)

    async def mark_sync_succeeded(
        self, client_id: object, competence: date, *, at: datetime
    ) -> None:
        self._ledger.calls.append("mark_ok")

    async def mark_sync_failed(self, client_id: object, competence: date, *, at: datetime) -> None:
        self._ledger.calls.append("mark_failed")


class _Clients:
    def __init__(self, accounts: list[int]) -> None:
        self._accounts = accounts

    async def get_accounts_cache(self, client_id: object) -> list[SimpleNamespace]:
        return [SimpleNamespace(omie_conta_id=acc) for acc in self._accounts]


class _Provider:
    """Provedor falso que MEDE a sobreposição de `list_entries`."""

    provider_type = "omie"

    def __init__(
        self,
        entries_by_account: dict[str, list[ProviderEntry]] | None = None,
        *,
        fail_on: str | None = None,
        probe: dict[str, int] | None = None,
    ) -> None:
        self._entries = entries_by_account or {}
        self._fail_on = fail_on
        self.probe = probe if probe is not None else {"em_voo": 0, "pico": 0, "chamadas": 0}
        self.closed = False

    async def list_entries(
        self, *, account_external_id: str, start: date, end: date
    ) -> list[ProviderEntry]:
        self.probe["em_voo"] += 1
        self.probe["chamadas"] += 1
        self.probe["pico"] = max(self.probe["pico"], self.probe["em_voo"])
        try:
            await asyncio.sleep(0)
            if account_external_id == self._fail_on:
                raise OmieFaultError("instabilidade")
            return list(self._entries.get(account_external_id, []))
        finally:
            self.probe["em_voo"] -= 1

    async def aclose(self) -> None:
        self.closed = True


def _entry(
    external_id: str,
    *,
    day: date = date(2026, 6, 10),
    amount: str = "-100.00",
    category: str | None = "2.04.94",
    supplier: str | None = "2624256082",
    description: str = "PAGTO FORNECEDOR ACME LTDA",
) -> ProviderEntry:
    return ProviderEntry(
        external_id=external_id,
        entry_date=day,
        amount=Decimal(amount),
        description=description,
        status="Conciliado",
        category_code=category,
        supplier_code=supplier,
    )


def _client(*, closed: bool = False) -> Client:
    client = Client(id=uuid4(), name="Cliente base", active=True, created_by=uuid4())
    client.closed_at = datetime.now(UTC) if closed else None
    return client


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    provider: Any,
    *,
    accounts: list[int],
    accounts_synced_at: datetime | None = datetime(2026, 6, 1, tzinfo=UTC),
    locks: OriginClientLocks | None = None,
    connection_error: Exception | None = None,
    repo_fails: bool = False,
) -> tuple[ClientMovementsSyncService, _Ledger]:
    ledger = _Ledger()

    async def _resolve(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        if connection_error is not None:
            raise connection_error
        return SimpleNamespace(
            accounts_synced_at=accounts_synced_at, provider_type=provider.provider_type
        )

    async def _build(*_args: Any, **_kwargs: Any) -> Any:
        return provider

    monkeypatch.setattr(service_module, "resolve_capable_connection", _resolve)
    monkeypatch.setattr(service_module, "build_origin_provider", _build)
    service = ClientMovementsSyncService(
        _Db(ledger),  # type: ignore[arg-type]
        repository=_Repo(ledger, fail_cycle=repo_fails),  # type: ignore[arg-type]
        clients=_Clients(accounts),  # type: ignore[arg-type]
        settings=get_settings(),
        locks=locks or OriginClientLocks(),
        usage_events=_Events(ledger),  # type: ignore[arg-type]
        accounts_cache=_AccountsCache(ledger),  # type: ignore[arg-type]
    )
    return service, ledger


# ---------------------------------------------------------------------------
# Competência
# ---------------------------------------------------------------------------


class TestCompetencia:
    def test_periodo_e_do_primeiro_ao_ultimo_dia_sem_janela_expandida(self) -> None:
        """NÃO é a janela de ±3 dias da §5.3 — aquela é do matcher."""
        assert competence_bounds(date(2026, 6, 1)) == (date(2026, 6, 1), date(2026, 6, 30))
        assert competence_bounds(date(2026, 2, 1)) == (date(2026, 2, 1), date(2026, 2, 28))
        assert competence_bounds(date(2028, 2, 1)) == (date(2028, 2, 1), date(2028, 2, 29))
        assert competence_bounds(date(2026, 12, 1)) == (date(2026, 12, 1), date(2026, 12, 31))

    def test_competencia_que_nao_e_dia_1_e_erro_de_programacao(self) -> None:
        with pytest.raises(ValueError, match="dia 1"):
            competence_bounds(date(2026, 6, 15))

    def test_competencia_de_uma_data_e_o_dia_1(self) -> None:
        assert competence_of(date(2026, 6, 30)) == date(2026, 6, 1)


# ---------------------------------------------------------------------------
# Linha gravada
# ---------------------------------------------------------------------------


class TestLinhaSoCodigos:
    def test_descricao_e_situacao_da_origem_nao_viram_coluna(self) -> None:
        row = movement_row(_entry("1"), source_type="omie", account="777")
        assert "description" not in row
        assert "status" not in row
        assert not any("ACME" in str(value) for value in row.values())

    def test_campos_da_linha(self) -> None:
        row = movement_row(_entry("1", day=date(2026, 6, 30)), source_type="omie", account="777")
        assert row == {
            "source_type": "omie",
            "source_movement_id": "1",
            "competence": date(2026, 6, 1),
            "movement_date": date(2026, 6, 30),
            "amount": Decimal("-100.00"),
            "category_code": "2.04.94",
            "supplier_code": "2624256082",
            "source_account_id": "777",
            # S14: o Omie não preenche documento; a linha do arquivo pode.
            "document": None,
        }

    def test_documento_do_arquivo_vai_em_claro_e_vazio_vira_nulo(self) -> None:
        """Identificador, não nome (ADR-082-BE) — mesmo tratamento de `supplier_code`."""
        entry = ProviderEntry(
            external_id="abc:2",
            entry_date=date(2026, 6, 5),
            amount=Decimal("-1.00"),
            description="x",
            document_number="NF 77",
        )
        assert movement_row(entry, source_type="arquivo", account=None)["document"] == "NF 77"
        vazio = entry.model_copy(update={"document_number": "  "})
        assert movement_row(vazio, source_type="arquivo", account=None)["document"] is None

    @pytest.mark.parametrize("vazio", [None, "", "   "])
    def test_categoria_vazia_vira_nulo(self, vazio: str | None) -> None:
        """'' e ausência são o MESMO estado: "sem categoria de origem" (R3)."""
        row = movement_row(_entry("1", category=vazio), source_type="omie", account="1")
        assert row["category_code"] is None


# ---------------------------------------------------------------------------
# Ciclo feliz
# ---------------------------------------------------------------------------


class TestSincronizacaoIntegra:
    async def test_todas_as_contas_em_serie_e_contagens(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = _Provider(
            {
                "111": [_entry("1"), _entry("2", category=None)],
                "222": [_entry("3", amount="250.00")],
            }
        )
        service, ledger = _wire(monkeypatch, provider, accounts=[111, 222])

        result = await service.sync(_client(), JUNHO)

        assert (result.movimentos, result.sem_categoria, result.contas) == (3, 1, 2)
        assert result.competence == JUNHO
        assert ledger.calls == ["reconcile_cycle", "mark_ok", "emit"]
        ciclo = ledger.cycles[0]
        assert ciclo["source_type"] == "omie"
        assert ciclo["competence"] == JUNHO
        por_id = {row["source_movement_id"]: row for row in ciclo["rows"]}
        assert por_id["1"]["source_account_id"] == "111"
        assert por_id["3"]["source_account_id"] == "222"
        assert por_id["2"]["category_code"] is None
        assert provider.probe["chamadas"] == 2
        assert provider.closed is True
        # O evento sai UMA vez, com as contagens do resultado — sem recontar.
        (evento,) = ledger.events
        assert evento["competencia"] == JUNHO
        assert (evento["movimentos"], evento["sem_categoria"], evento["contas"]) == (3, 1, 2)

    async def test_identificador_repetido_no_lote_fica_uma_vez(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ADR-066-BE: o mesmo id duas vezes no MESMO `ON CONFLICT` é erro do Postgres."""
        provider = _Provider(
            {"111": [_entry("1", amount="-1.00")], "222": [_entry("1", amount="-2.00")]}
        )
        service, ledger = _wire(monkeypatch, provider, accounts=[111, 222])

        result = await service.sync(_client(), JUNHO)

        assert result.movimentos == 1
        (row,) = ledger.cycles[0]["rows"]
        assert row["amount"] == Decimal("-2.00"), "a ÚLTIMA ocorrência vence"

    async def test_origem_vazia_com_contas_conhecidas_e_competencia_vazia_de_verdade(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger = _wire(monkeypatch, _Provider(), accounts=[111])
        result = await service.sync(_client(), JUNHO)
        assert result.movimentos == 0
        assert ledger.calls == ["reconcile_cycle", "mark_ok", "emit"]

    async def test_contas_sincronizadas_e_nenhuma_existente_e_base_vazia(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache vazio COM contas já sincronizadas: a origem disse que não há conta."""
        provider = _Provider()
        service, ledger = _wire(monkeypatch, provider, accounts=[])
        result = await service.sync(_client(), JUNHO)
        assert (result.movimentos, result.contas) == (0, 0)
        assert provider.probe["chamadas"] == 0
        assert ledger.calls == ["reconcile_cycle", "mark_ok", "emit"]


# ---------------------------------------------------------------------------
# Falha e configuração
# ---------------------------------------------------------------------------


class TestFalhaCarimbaComCommit:
    async def test_falha_numa_conta_carimba_commita_e_re_levanta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A barreira de durabilidade: `mark_failed` → `commit` → `raise`.

        Sem o `commit()`, o `raise` sobe até `get_db_session`, que faz
        `rollback()` — o carimbo de falha morreria com a exceção. E como a
        segunda conta falhou DEPOIS da primeira ter respondido, o teste também
        prova que nada foi gravado: não houve `reconcile_cycle` nem `mark_ok`.
        """
        provider = _Provider({"111": [_entry("1")]}, fail_on="222")
        service, ledger = _wire(monkeypatch, provider, accounts=[111, 222])

        with pytest.raises(OmieFaultError):
            await service.sync(_client(), JUNHO)

        assert ledger.calls == ["mark_failed", "commit"]
        assert "mark_ok" not in ledger.calls, "a falha NUNCA toca o carimbo de sucesso"
        assert provider.closed is True

    async def test_os_409_da_s9_nao_carimbam(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, ledger = _wire(
            monkeypatch,
            _Provider(),
            accounts=[111],
            connection_error=NoOriginConnectionError("sem conexão"),
        )
        with pytest.raises(NoOriginConnectionError) as exc:
            await service.sync(_client(), JUNHO)
        assert exc.value.status_code == 409
        assert ledger.calls == []

    async def test_contas_nunca_sincronizadas_e_409_sem_carimbo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cache vazio E nunca sincronizado: zero movimentos seria base vazia mentindo."""
        provider = _Provider()
        service, ledger = _wire(monkeypatch, provider, accounts=[], accounts_synced_at=None)
        with pytest.raises(MovementAccountsUnknownError) as exc:
            await service.sync(_client(), JUNHO)
        assert exc.value.status_code == 409
        assert ledger.calls == []
        assert provider.probe["chamadas"] == 0

    async def test_cliente_encerrado_e_409_antes_de_tocar_a_origem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        provider = _Provider({"111": [_entry("1")]})
        service, ledger = _wire(monkeypatch, provider, accounts=[111])
        with pytest.raises(ClientClosedError) as exc:
            await service.sync(_client(closed=True), JUNHO)
        assert exc.value.status_code == 409
        assert ledger.calls == []
        assert provider.probe["chamadas"] == 0

    async def test_origem_por_arquivo_e_409_antes_de_tocar_o_ciclo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S14 (BACK 14.3): o adaptador `arquivo` vindo da CONEXÃO é vazio — sincronizar
        por aqui leria zero linhas e marcaria a base inteira como ausente. 409 tipado,
        nada carimbado, nenhuma leitura, nenhum evento."""
        provider = _Provider({"111": [_entry("1")]})
        provider.provider_type = "arquivo"
        service, ledger = _wire(monkeypatch, provider, accounts=[111])
        with pytest.raises(FileOriginSyncNotApplicableError) as exc:
            await service.sync(_client(), JUNHO)
        assert exc.value.status_code == 409
        assert exc.value.code.value == "ORIGEM_POR_ARQUIVO"
        assert "arquivo" in exc.value.user_message
        assert ledger.calls == []
        assert ledger.refreshes == 0
        assert provider.probe["chamadas"] == 0


# ---------------------------------------------------------------------------
# A porta pública do ciclo para a ingestão por arquivo (S14, BACK 14.3)
# ---------------------------------------------------------------------------


class TestPersistEntriesDoArquivo:
    async def test_grava_pelo_mesmo_ciclo_sem_recorte_de_conta_e_sem_o_evento_de_sync(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger = _wire(monkeypatch, _Provider(), accounts=[])
        entries = [(None, _entry("ab:2", supplier=None)), ("12345", _entry("ab:3", category=None))]

        result = await service.persist_entries(
            _client(), JUNHO, source_type="arquivo", entries=entries, accounts=None, emit=False
        )

        assert ledger.calls == ["reconcile_cycle", "mark_ok"]
        assert "emit" not in ledger.calls, "`movimentos_sincronizados` é da sincronização"
        (cycle,) = ledger.cycles
        assert cycle["source_type"] == "arquivo"
        assert cycle["accounts_read"] is None
        por_id = {row["source_movement_id"]: row for row in cycle["rows"]}
        assert por_id["ab:2"]["source_account_id"] is None
        assert por_id["ab:3"]["source_account_id"] == "12345"
        assert (result.movimentos, result.sem_categoria, result.contas) == (2, 1, 0)

    async def test_descricoes_sao_cifradas_com_a_dek_do_cliente_e_o_aad_da_pk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Depois do upsert (a pk entra no AAD), IV novo por linha, nunca o texto em log."""
        service, ledger = _wire(monkeypatch, _Provider(), accounts=[])
        client = _client()
        entries = [(None, _entry("ab:2")), (None, _entry("ab:3"))]
        descriptions = {"ab:2": "PAGTO ACME SEGREDO", "ab:3": "PAGTO ACME SEGREDO"}

        await service.persist_entries(
            client,
            JUNHO,
            source_type="arquivo",
            entries=entries,
            accounts=None,
            descriptions=descriptions,
            emit=False,
        )

        assert ledger.calls == ["reconcile_cycle", "ids_by_source", "set_descriptions", "mark_ok"]
        assert client.dek_wrapped is not None, "a DEK é provisionada para cifrar"
        assert len(ledger.descriptions) == 2
        ivs = {u["b_iv"] for u in ledger.descriptions}
        assert len(ivs) == 2, "IV novo por linha"
        cipher = await load_client_cipher(client, settings=get_settings())
        for update in ledger.descriptions:
            assert "SEGREDO" not in update["b_ct"]
            plain = cipher.decrypt(
                update["b_ct"],
                update["b_iv"],
                field_locator(AAD_MOVEMENT_DESCRIPTION, update["b_id"]),
            )
            assert plain == "PAGTO ACME SEGREDO"

    async def test_sem_descricoes_nao_toca_a_cripto(self, monkeypatch: pytest.MonkeyPatch) -> None:
        service, ledger = _wire(monkeypatch, _Provider(), accounts=[])
        client = _client()
        await service.persist_entries(
            client,
            JUNHO,
            source_type="arquivo",
            entries=[],
            accounts=None,
            descriptions={},
            emit=False,
        )
        assert "ids_by_source" not in ledger.calls
        assert client.dek_wrapped is None

    async def test_falha_de_banco_faz_rollback_carimba_e_re_levanta_tambem_aqui(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        service, ledger = _wire(monkeypatch, _Provider(), accounts=[], repo_fails=True)
        with pytest.raises(RuntimeError):
            await service.persist_entries(
                _client(),
                JUNHO,
                source_type="arquivo",
                entries=[(None, _entry("ab:2"))],
                accounts=None,
            )
        assert ledger.calls == ["reconcile_cycle", "rollback", "mark_failed", "commit"]


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------


class TestLockPorCliente:
    async def test_duas_sincronizacoes_juntas_nunca_tem_duas_chamadas_em_voo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A Omie processa UMA requisição por método por credencial (`8020`/`1880`).

        Duas sincronizações do MESMO cliente (junho e julho, disparadas juntas)
        compartilham o lock injetado; o pico de `list_entries` em voo tem de ser 1.
        """
        probe = {"em_voo": 0, "pico": 0, "chamadas": 0}
        locks = OriginClientLocks()
        client = _client()
        contas = {str(acc): [_entry(f"{acc}-1")] for acc in (111, 222, 333)}
        service_a, _ = _wire(
            monkeypatch, _Provider(contas, probe=probe), accounts=[111, 222, 333], locks=locks
        )
        service_b, _ = _wire(
            monkeypatch, _Provider(contas, probe=probe), accounts=[111, 222, 333], locks=locks
        )

        await asyncio.gather(
            service_a.sync(client, JUNHO), service_b.sync(client, date(2026, 7, 1))
        )

        assert probe["chamadas"] == 6
        assert probe["pico"] == 1

    async def test_sem_o_lock_compartilhado_as_chamadas_se_sobrepoem(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """O teste acima não é vacuamente verde: com locks DIFERENTES há sobreposição."""
        probe = {"em_voo": 0, "pico": 0, "chamadas": 0}
        client = _client()
        contas = {str(acc): [_entry(f"{acc}-1")] for acc in (111, 222, 333)}
        service_a, _ = _wire(
            monkeypatch,
            _Provider(contas, probe=probe),
            accounts=[111, 222, 333],
            locks=OriginClientLocks(),
        )
        service_b, _ = _wire(
            monkeypatch,
            _Provider(contas, probe=probe),
            accounts=[111, 222, 333],
            locks=OriginClientLocks(),
        )

        await asyncio.gather(
            service_a.sync(client, JUNHO), service_b.sync(client, date(2026, 7, 1))
        )

        assert probe["pico"] == 2

    def test_sem_locks_injetado_o_servico_usa_o_registro_do_processo(self) -> None:
        from app.integrations.omie.client_locks import origin_client_locks

        service = ClientMovementsSyncService(
            None,  # type: ignore[arg-type]
            repository=None,  # type: ignore[arg-type]
            clients=None,  # type: ignore[arg-type]
            settings=None,  # type: ignore[arg-type]
        )
        assert service._locks is origin_client_locks


# ---------------------------------------------------------------------------
# Fixture real
# ---------------------------------------------------------------------------


class TestFixtureRealDoExtrato:
    @respx.mock
    async def test_80_movimentos_e_as_32_linhas_de_saldo_fora(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A captura real (21/08/2026, cartão Inter, julho/2026): 112 linhas no
        `listaMovimentos`, 32 delas de SALDO. O caminho é o de produção — adaptador
        + `OmieClient` + filtro de saldo — só o transporte é o respx."""
        payload = json.loads(_FIXTURE.read_text(encoding="utf-8"))
        brutos = payload["listaMovimentos"]
        saldo = [raw for raw in brutos if raw.get("nCodLancamento") is None]
        assert (len(brutos), len(saldo)) == (112, 32), "a fixture mudou — recontar"
        respx.post(OMIE_EXTRATO_URL).mock(return_value=httpx.Response(200, json=payload))

        provider = OmieProvider(
            omie_credentials_payload("movements-app-key", "movements-app-secret"), get_settings()
        )
        service, ledger = _wire(monkeypatch, provider, accounts=[int(payload["nCodCC"])])

        result = await service.sync(_client(), date(2026, 7, 1))

        assert result.movimentos == 80
        assert result.sem_categoria == 0
        assert result.contas == 1
        rows = ledger.cycles[0]["rows"]
        assert len(rows) == 80
        ids_reais = {str(raw["nCodLancamento"]) for raw in brutos if raw.get("nCodLancamento")}
        assert {row["source_movement_id"] for row in rows} == ids_reais
        assert {row["competence"] for row in rows} == {date(2026, 7, 1)}

        # §4.5 por VALOR: nenhum texto livre ou nome da origem chegou à linha.
        textos_da_origem = {
            str(raw[key]).strip().upper()
            for raw in brutos
            for key in ("cObservacoes", "cRazCliente", "cDesCliente", "cDesCategoria")
            if raw.get(key) and len(str(raw[key]).strip()) > 3
        }
        gravado = {str(value).upper() for row in rows for value in row.values()}
        assert not (textos_da_origem & gravado)

        # Valor COM SINAL: cartão, natureza P → negativo, R → positivo.
        por_id = {row["source_movement_id"]: row for row in rows}
        for raw in brutos:
            if raw.get("nCodLancamento") is None:
                continue
            valor = por_id[str(raw["nCodLancamento"])]["amount"]
            assert isinstance(valor, Decimal)
            if raw["cNatureza"] == "P":
                assert valor <= 0
            elif raw["cNatureza"] == "R":
                assert valor >= 0


# ---------------------------------------------------------------------------
# Follow-up 86e3f0ux7: base honesta (contas renovadas, ausência recortada, falha de banco)
# ---------------------------------------------------------------------------


class TestBaseHonesta:
    async def test_cache_de_contas_e_renovado_antes_da_leitura_e_as_contas_lidas_vao_ao_ciclo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Item 3: o `get_or_sync` (com o TTL dele) roda ANTES da origem, e a
        marcação de ausência recebe exatamente as contas que foram lidas."""
        provider = _Provider({"111": [_entry("1")], "222": [_entry("2")]})
        service, ledger = _wire(monkeypatch, provider, accounts=[111, 222])
        result = await service.sync(_client(), JUNHO)
        assert ledger.refreshes == 1
        assert ledger.cycles[0]["accounts_read"] == ["111", "222"]
        assert result.contas == 2

    async def test_falha_de_banco_no_ciclo_faz_rollback_carimba_a_falha_e_re_levanta(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Item 4: até aqui só a falha da ORIGEM carimbava `sync_failed_at`; uma
        exceção do Postgres no meio do ciclo deixava a competência sem carimbo
        nenhum. A ordem importa: ROLLBACK (a transação está inutilizável e a base
        anterior fica inteira), carimbo, COMMIT como barreira, e o erro segue."""
        provider = _Provider({"111": [_entry("1")]})
        service, ledger = _wire(monkeypatch, provider, accounts=[111], repo_fails=True)
        with pytest.raises(RuntimeError):
            await service.sync(_client(), JUNHO)
        assert ledger.calls == ["reconcile_cycle", "rollback", "mark_failed", "commit"]
        assert "mark_ok" not in ledger.calls
        assert "emit" not in ledger.calls, "métrica só no fim de uma sincronização íntegra"
