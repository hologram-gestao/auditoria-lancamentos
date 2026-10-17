"""Reimportação que só adiciona e atualiza, prévia e exportação do plano (86e3qhhrm).

O que estes testes travam, sem banco:

- `diff_chart`: a MESMA diferença para a prévia e para a importação — nova,
  alterada (nome, tipo, classificação), igual, ausente, a que voltou e a inativa que
  veio; a situação nunca é parte da diferença;
- a fórmula: o nome que começa com `= + - @` sai com apóstrofo no CSV e volta sem
  ele na leitura, e o apóstrofo que não guarda fórmula é do nome;
- o critério de aceite "exportar e reimportar o mesmo arquivo é idempotente": o CSV
  exportado, lido pelo MESMO leitor da importação, dá diferença VAZIA;
- a prévia não trava, não grava, não comita, e conta as ausentes em uso;
- a exportação recusa nome que não decifra (o marcador viraria nome de conta).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.core.config import get_settings
from app.core.crypto_service import (
    AAD_ACCOUNTING_ACCOUNT_NAME,
    field_locator,
    load_client_cipher,
    new_client_dek,
)
from app.core.exceptions import AccountingChartUndecipherableError, ClientClosedError
from app.db.models import AccountingAccountType, ClientAccountingAccount
from app.modules.client_accounting_chart.diff import diff_chart
from app.modules.client_accounting_chart.export import build_chart_csv, export_filename
from app.modules.client_accounting_chart.schemas import (
    CHART_PREVIEW_LIST_LIMIT,
    ChartImportPreviewPayload,
)
from app.modules.client_accounting_chart.service import AccountingChartService
from app.modules.client_accounting_chart.sheet import (
    ChartSheetRow,
    neutralize_formula,
    parse_chart_sheet,
    restore_formula,
)

pytestmark = pytest.mark.unit

_ABSENT_AT = datetime(2026, 10, 1, tzinfo=UTC)


def _row(
    code: str, name: str, *, kind: str = "analitica", classification: str | None = None
) -> ChartSheetRow:
    return ChartSheetRow(
        line=0,
        code=code,
        name=name,
        account_type=AccountingAccountType(kind),
        classification=classification,
    )


def _account(
    code: str,
    *,
    kind: str = "analitica",
    classification: str | None = None,
    active: bool = True,
    absent_since: datetime | None = None,
) -> ClientAccountingAccount:
    return ClientAccountingAccount(
        id=uuid4(),
        client_id=uuid4(),
        code=code,
        classification=classification,
        name_encrypted="v1:k1:00",
        name_iv="0" * 24,
        account_type=kind,
        active=active,
        absent_since=absent_since,
    )


class TestDiff:
    def test_cada_conta_cai_numa_categoria_so(self) -> None:
        igual = _account("1", classification="1")
        renomeada = _account("2")
        retipada = _account("3")
        reclassificada = _account("4", classification="1.1")
        sumiu = _account("5")
        voltou = _account("6", absent_since=_ABSENT_AT)
        inativa = _account("7", active=False)
        names = {
            igual.id: "Ativo",
            renomeada.id: "Nome antigo",
            retipada.id: "Clientes",
            reclassificada.id: "Caixa",
            sumiu.id: "Some",
            voltou.id: "Volta",
            inativa.id: "Desligada",
        }
        rows = [
            _row("1", "Ativo", classification="1"),
            _row("2", "Nome novo"),
            _row("3", "Clientes", kind="sintetica"),
            _row("4", "Caixa", classification="1.2"),
            _row("6", "Volta"),
            _row("7", "Desligada"),
            _row("8", "Nova embaixo de Clientes", classification="1.1.2.01"),
        ]
        diff = diff_chart(
            rows,
            [igual, renomeada, retipada, reclassificada, sumiu, voltou, inativa],
            names=names,
        )

        assert [r.code for r in diff.new_rows] == ["8"]
        assert {c.account.code: c.changes for c in diff.changed} == {
            "2": ("nome",),
            "3": ("tipo",),
            "4": ("classificacao",),
        }
        assert [a.code for a in diff.absent] == ["5"]
        assert [a.code for a in diff.returned] == ["6"]
        assert [a.code for a in diff.inactive_present] == ["7"]
        assert {r.code for r in diff.unchanged_rows} == {"1", "6", "7"}
        assert diff.has_changes

    def test_nome_que_nao_decifra_conta_como_alterado(self) -> None:
        account = _account("1")
        diff = diff_chart([_row("1", "Ativo")], [account], names={}, failed={account.id})
        assert [c.changes for c in diff.changed] == [("nome",)]

    def test_ausente_que_ja_tinha_o_selo_nao_e_mudanca(self) -> None:
        ficou = _account("1")
        ja_ausente = _account("2", absent_since=_ABSENT_AT)
        diff = diff_chart([_row("1", "Ativo")], [ficou, ja_ausente], names={ficou.id: "Ativo"})
        assert [a.code for a in diff.absent] == ["2"]
        assert not diff.has_changes

    def test_ausente_nova_e_mudanca(self) -> None:
        ficou, sumiu = _account("1"), _account("2")
        diff = diff_chart([_row("1", "Ativo")], [ficou, sumiu], names={ficou.id: "Ativo"})
        assert diff.has_changes

    def test_primeira_importacao_e_tudo_novo(self) -> None:
        diff = diff_chart([_row("1", "Ativo"), _row("2", "Caixa")], [], names={})
        assert [r.code for r in diff.new_rows] == ["1", "2"]
        assert diff.changed == ()
        assert diff.absent == ()
        assert diff.returned == ()


class TestFormula:
    @pytest.mark.parametrize("name", ["=SOMA(A1)", "+55 11", "- Juros", "@cmd"])
    def test_nome_que_seria_formula_ganha_e_perde_o_apostrofo(self, name: str) -> None:
        guarded = neutralize_formula(name)
        assert guarded == "'" + name
        assert restore_formula(guarded) == name

    @pytest.mark.parametrize("name", ["Caixa", "(-) Deduções", "'Aspas do nome"])
    def test_nome_comum_fica_como_esta(self, name: str) -> None:
        assert neutralize_formula(name) == name
        assert restore_formula(name) == name


class TestExportReimportIdempotente:
    async def test_csv_exportado_lido_pelo_leitor_da_importacao_nao_muda_nada(self) -> None:
        accounts = [
            _account("1", kind="sintetica", classification="1"),
            _account("2", kind="sintetica", classification="1.1"),
            _account("649", classification="1.1.1.02.001"),
            _account("662", classification="1.1.2.01.004", active=False),
            _account("700"),
        ]
        names = {
            accounts[0].id: "Ativo",
            accounts[1].id: "Clientes",
            accounts[2].id: "Banco; conta movimento",  # o separador dentro do nome
            accounts[3].id: 'Aluguéis a receber - Inquilino "D"',
            accounts[4].id: "- Juros pagos",  # começaria fórmula
        }
        content = build_chart_csv(accounts, names)

        assert content.startswith(b"\xef\xbb\xbf")  # o BOM do Excel
        assert content.splitlines()[0] == "﻿codigo_reduzido;nome;tipo;classificacao".encode()
        parsed = parse_chart_sheet(content)
        assert parsed.layout == "modelo"
        diff = diff_chart(parsed.rows, accounts, names=names)
        assert not diff.has_changes
        assert diff.changed == ()
        assert diff.new_rows == ()
        assert diff.absent == ()
        assert [r.code for r in parsed.rows] == [a.code for a in accounts]

    def test_plano_vazio_sai_so_com_o_cabecalho(self) -> None:
        assert build_chart_csv([], {}) == "﻿codigo_reduzido;nome;tipo;classificacao\r\n".encode()

    def test_nome_do_arquivo_nao_leva_o_nome_do_cliente(self) -> None:
        client_id = UUID("12345678-1234-5678-1234-567812345678")
        assert export_filename(client_id) == "plano-contabil-12345678.csv"


class _FakeClient:
    def __init__(self, *, closed: bool = False) -> None:
        self.id = uuid4()
        self.dek_wrapped: bytes | None = None
        self.closed_at = datetime(2026, 9, 1, tzinfo=UTC) if closed else None


class _Db:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:  # pragma: no cover - a prévia nunca chama
        self.commits += 1


class _Repo:
    def __init__(self, existing: list[ClientAccountingAccount], in_use: set[UUID]) -> None:
        self.existing = existing
        self.in_use = in_use
        self.locked = False
        self.writes = 0
        self.asked_in_use: list[UUID] | None = None

    async def list_all(self, client_id: UUID) -> list[ClientAccountingAccount]:
        return self.existing

    async def accounts_in_use(self, client_id: UUID, account_ids: list[UUID]) -> set[UUID]:
        self.asked_in_use = list(account_ids)
        return self.in_use & set(account_ids)

    async def lock_client_chart(self, client_id: UUID) -> None:  # pragma: no cover
        self.locked = True

    async def insert_accounts(self, rows: Any) -> None:  # pragma: no cover
        self.writes += 1

    async def update_accounts(self, rows: Any) -> None:  # pragma: no cover
        self.writes += 1

    async def mark_absent(self, client_id: UUID, **_: Any) -> int:  # pragma: no cover
        self.writes += 1
        return 0


async def _with_names(
    client: _FakeClient, accounts: list[tuple[ClientAccountingAccount, str]]
) -> list[ClientAccountingAccount]:
    _cipher, client.dek_wrapped = await new_client_dek(client.id, settings=get_settings())
    cipher = await load_client_cipher(client, settings=get_settings())
    for account, name in accounts:
        account.name_encrypted, account.name_iv = cipher.encrypt(
            name, field_locator(AAD_ACCOUNTING_ACCOUNT_NAME, account.id)
        )
    return [account for account, _ in accounts]


def _service(repo: _Repo, db: _Db) -> AccountingChartService:
    return AccountingChartService(
        db,  # type: ignore[arg-type]
        settings=get_settings(),
        repository=repo,  # type: ignore[arg-type]
        usage_events=object(),  # type: ignore[arg-type]
    )


_SHEET = b"codigo_reduzido;nome;tipo\n649;Banco;analitica\n10;Nova;analitica\n"


class TestPrevia:
    async def test_nao_trava_nao_grava_e_conta_as_ausentes_em_uso(self) -> None:
        client = _FakeClient()
        banco, em_uso, livre = _account("649"), _account("662"), _account("999")
        existing = await _with_names(
            client, [(banco, "Banco"), (em_uso, "Inquilino D"), (livre, "Sem uso")]
        )
        repo, db = _Repo(existing, in_use={em_uso.id, banco.id}), _Db()

        preview = await _service(repo, db).preview_sheet(client, content=_SHEET)  # type: ignore[arg-type]

        assert not repo.locked
        assert repo.writes == 0
        assert db.commits == 0
        # Só as AUSENTES são perguntadas; a presente em uso não conta.
        assert set(repo.asked_in_use or []) == {em_uso.id, livre.id}
        payload = ChartImportPreviewPayload.from_preview(preview)
        assert (payload.contas, payload.contas_novas, payload.contas_alteradas) == (2, 1, 0)
        assert (payload.contas_ausentes, payload.contas_ausentes_em_uso) == (2, 1)
        assert payload.tem_mudancas
        # As em uso vêm primeiro, com o nome atual decifrado.
        assert [(a.code, a.name, a.in_use) for a in payload.ausentes] == [
            ("662", "Inquilino D", True),
            ("999", "Sem uso", False),
        ]
        assert [n.code for n in payload.novas] == ["10"]
        assert payload.list_limit == CHART_PREVIEW_LIST_LIMIT

    async def test_cliente_encerrado_e_409(self) -> None:
        client = _FakeClient(closed=True)
        with pytest.raises(ClientClosedError):
            await _service(_Repo([], set()), _Db()).preview_sheet(client, content=_SHEET)  # type: ignore[arg-type]

    async def test_listas_respeitam_o_teto_e_as_contagens_sao_reais(self) -> None:
        total = CHART_PREVIEW_LIST_LIMIT + 5
        sheet = "codigo_reduzido;nome;tipo\n" + "".join(
            f"{n};Conta {n};analitica\n" for n in range(1, total + 1)
        )
        client = _FakeClient()
        preview = await _service(_Repo([], set()), _Db()).preview_sheet(
            client,  # type: ignore[arg-type]
            content=sheet.encode(),
        )
        payload = ChartImportPreviewPayload.from_preview(preview)
        assert payload.contas_novas == total
        assert len(payload.novas) == CHART_PREVIEW_LIST_LIMIT


class TestExportacao:
    async def test_nome_que_nao_decifra_recusa_a_exportacao(self) -> None:
        client = _FakeClient()
        ok = _account("1")
        await _with_names(client, [(ok, "Ativo")])
        quebrada = _account("2")  # ciphertext que a chave não abre
        repo = _Repo([ok, quebrada], set())
        with pytest.raises(AccountingChartUndecipherableError) as excinfo:
            await _service(repo, _Db()).export_csv(client)  # type: ignore[arg-type]
        assert excinfo.value.status_code == 409
        assert excinfo.value.details == {"count": 1}

    async def test_exporta_todas_as_contas_na_ordem_do_plano(self) -> None:
        client = _FakeClient()
        accounts = await _with_names(
            client, [(_account("1", kind="sintetica"), "Ativo"), (_account("2"), "Caixa")]
        )
        content, count = await _service(_Repo(accounts, set()), _Db()).export_csv(client)  # type: ignore[arg-type]
        assert count == 2
        assert content.decode("utf-8-sig").splitlines()[1:] == [
            "1;Ativo;sintetica;",
            "2;Caixa;analitica;",
        ]
