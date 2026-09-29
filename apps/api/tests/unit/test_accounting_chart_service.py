"""Serviço do plano contábil sem banco (BACK 16.1): validador único, reimportação por
código e nada de nome/código em log.

O repositório é falso de propósito: aqui se prova a REGRA (o que vira INSERT, o que
vira UPDATE, o que é inativado, a cifra com a pk no AAD). O SQL real — Core em lote,
`client_id` no WHERE, a trava — é provado na integração contra Postgres
(`tests/integration/test_accounting_chart_endpoints.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from structlog.testing import capture_logs

from app.core.authz import CurrentUser
from app.core.config import get_settings
from app.core.crypto_service import (
    AAD_ACCOUNTING_ACCOUNT_NAME,
    field_locator,
    load_client_cipher,
    new_client_dek,
)
from app.core.exceptions import (
    AccountingAccountNotFoundError,
    AccountingAccountNotPostableError,
    ClientClosedError,
    ErrorCode,
    FileLinesInvalidError,
)
from app.db.models import AccountingAccountType, ClientAccountingAccount, UserRole, UserScope
from app.modules.client_accounting_chart.service import (
    AccountingChartService,
    not_postable_reason,
)

pytestmark = pytest.mark.unit

_SECRET_NAME = "Alugueis a receber - Inquilino Sigiloso"
_SHEET = (
    "codigo_reduzido;nome;tipo\n"
    "649;Banco conta movimento;analitica\n"
    f"662;{_SECRET_NAME};analitica\n"
    "10;Ativo;sintetica\n"
).encode()


class _FakeClient:
    def __init__(self, *, dek_wrapped: bytes | None, closed: bool = False) -> None:
        self.id = uuid4()
        self.dek_wrapped = dek_wrapped
        self.closed_at = datetime(2026, 9, 1, tzinfo=UTC) if closed else None


class _FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _FakeRepo:
    def __init__(self, existing: list[ClientAccountingAccount] | None = None) -> None:
        self.existing = existing or []
        self.locked = False
        self.inserts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.deactivated_with: list[str] | None = None
        self.get_result: ClientAccountingAccount | None = None
        self.get_calls: list[tuple[UUID, UUID]] = []

    async def get_many(
        self, client_id: UUID, account_ids: list[UUID]
    ) -> list[ClientAccountingAccount]:
        # O validador busca SEMPRE pelo par (cliente, contas): o repositório real
        # filtra `client_id` no WHERE; aqui registramos o par pedido.
        self.get_calls.extend((client_id, a) for a in account_ids)
        if self.get_result is None:
            return []
        (only,) = account_ids
        self.get_result.id = only
        return [self.get_result]

    async def lock_client_chart(self, client_id: UUID) -> None:
        self.locked = True

    async def list_all(self, client_id: UUID) -> list[ClientAccountingAccount]:
        return self.existing

    async def insert_accounts(self, rows: list[dict[str, Any]]) -> None:
        self.inserts.extend(rows)

    async def update_accounts(self, updates: list[dict[str, Any]]) -> None:
        self.updates.extend(updates)

    async def deactivate_absent(
        self, client_id: UUID, *, present_codes: list[str], author_id: UUID
    ) -> int:
        self.deactivated_with = list(present_codes)
        # O que o banco faria: ativas cujo código não veio.
        return sum(1 for a in self.existing if a.active and a.code not in present_codes)


def _account(
    *, code: str = "649", account_type: str = "analitica", active: bool = True
) -> ClientAccountingAccount:
    return ClientAccountingAccount(
        id=uuid4(),
        client_id=uuid4(),
        code=code,
        name_encrypted="v1:k1:00",
        name_iv="0" * 24,
        account_type=account_type,
        active=active,
    )


def _actor() -> CurrentUser:
    return CurrentUser(
        id=str(uuid4()),
        email="staff@hologram.com.br",
        name="Staff",
        role=UserRole.ADMIN.value,
        scope=UserScope.SYSTEM.value,
        client_id=None,
        organization_id=uuid4(),
    )


class _NoEvents:
    """A métrica da 16.4 tem teste próprio (`test_client_mapping_completeness.py`)."""

    async def emit_plano_contabil_importado(self, **_: Any) -> bool:
        return True


def _service(repo: _FakeRepo, db: _FakeDb | None = None) -> AccountingChartService:
    return AccountingChartService(
        db or _FakeDb(),  # type: ignore[arg-type]
        settings=get_settings(),
        repository=repo,  # type: ignore[arg-type]
        usage_events=_NoEvents(),  # type: ignore[arg-type]
    )


async def _client_with_dek() -> _FakeClient:
    client = _FakeClient(dek_wrapped=None)
    _cipher, wrapped = await new_client_dek(client.id, settings=get_settings())
    client.dek_wrapped = wrapped
    return client


class TestPostabilidade:
    @pytest.mark.parametrize(
        ("account_type", "active", "expected"),
        [
            ("analitica", True, None),
            ("sintetica", True, "sintetica"),
            ("analitica", False, "inativa"),
            ("sintetica", False, "sintetica"),
        ],
    )
    def test_funcao_pura(self, account_type: str, *, active: bool, expected: str | None) -> None:
        assert not_postable_reason(_account(account_type=account_type, active=active)) == expected


class TestValidadorUnico:
    async def test_conta_de_outro_cliente_ou_inexistente_e_404(self) -> None:
        repo = _FakeRepo()
        client_id, account_id = uuid4(), uuid4()
        with pytest.raises(AccountingAccountNotFoundError) as excinfo:
            await _service(repo).require_postable_account(client_id, account_id)
        assert excinfo.value.status_code == 404
        # A busca é SEMPRE pelo par (cliente, conta): a de outro cliente nem é lida.
        assert repo.get_calls == [(client_id, account_id)]
        assert excinfo.value.details == {}

    @pytest.mark.parametrize(
        ("account_type", "active", "reason"),
        [("sintetica", True, "sintetica"), ("analitica", False, "inativa")],
    )
    async def test_sintetica_ou_inativa_e_422_tipado(
        self, account_type: str, *, active: bool, reason: str
    ) -> None:
        repo = _FakeRepo()
        repo.get_result = _account(account_type=account_type, active=active)
        with pytest.raises(AccountingAccountNotPostableError) as excinfo:
            await _service(repo).require_postable_account(uuid4(), repo.get_result.id)
        exc = excinfo.value
        assert exc.status_code == 422
        assert exc.code is ErrorCode.CONTA_CONTABIL_NAO_LANCAVEL
        assert exc.details == {"accountId": str(repo.get_result.id), "reason": reason}

    async def test_analitica_e_ativa_passa(self) -> None:
        repo = _FakeRepo()
        repo.get_result = _account()
        assert await _service(repo).require_postable_account(uuid4(), uuid4()) is repo.get_result


class TestImportacao:
    async def test_primeira_importacao_insere_tudo_cifrado_com_a_pk_no_aad(self) -> None:
        client = await _client_with_dek()
        repo, db = _FakeRepo(), _FakeDb()
        result = await _service(repo, db).import_sheet(
            client,  # type: ignore[arg-type]
            actor=_actor(),
            content=_SHEET,
        )
        assert (result.accounts, result.new, result.inactivated) == (3, 3, 0)
        assert repo.locked
        assert db.commits == 1
        assert repo.updates == []
        cipher = await load_client_cipher(client, settings=get_settings())
        by_code = {row["code"]: row for row in repo.inserts}
        assert by_code["10"]["account_type"] == AccountingAccountType.SINTETICA.value
        row = by_code["662"]
        assert _SECRET_NAME not in repr(row)
        assert (
            cipher.decrypt(
                row["name_encrypted"],
                row["name_iv"],
                field_locator(AAD_ACCOUNTING_ACCOUNT_NAME, row["id"]),
            )
            == _SECRET_NAME
        )

    async def test_reimportacao_por_codigo_atualiza_insere_e_inativa(self) -> None:
        client = await _client_with_dek()
        banco = _account(code="649")
        inquilino = _account(code="662", active=False)  # volta a ativa
        sumiu = _account(code="999")
        ja_inativa = _account(code="998", active=False)
        repo = _FakeRepo([banco, inquilino, sumiu, ja_inativa])
        result = await _service(repo).import_sheet(
            client,  # type: ignore[arg-type]
            actor=_actor(),
            content=_SHEET,
        )
        assert [row["code"] for row in repo.inserts] == ["10"]
        assert {u["b_id"] for u in repo.updates} == {banco.id, inquilino.id}
        assert all(u["b_client"] == client.id for u in repo.updates)
        assert repo.deactivated_with == ["649", "662", "10"]
        # Só a que era ATIVA e sumiu conta como inativada nesta importação.
        assert (result.accounts, result.new, result.inactivated) == (3, 1, 1)

    async def test_planilha_invalida_nao_trava_nem_grava(self) -> None:
        client = await _client_with_dek()
        repo, db = _FakeRepo(), _FakeDb()
        repetido = b"codigo_reduzido;nome;tipo\n649;A;analitica\n649;B;analitica\n"
        with pytest.raises(FileLinesInvalidError):
            await _service(repo, db).import_sheet(
                client,  # type: ignore[arg-type]
                actor=_actor(),
                content=repetido,
            )
        assert not repo.locked
        assert repo.inserts == repo.updates == []
        assert repo.deactivated_with is None
        assert db.commits == 0

    async def test_cliente_encerrado_e_409_antes_de_tudo(self) -> None:
        client = _FakeClient(dek_wrapped=None, closed=True)
        repo = _FakeRepo()
        with pytest.raises(ClientClosedError):
            await _service(repo).import_sheet(
                client,  # type: ignore[arg-type]
                actor=_actor(),
                content=_SHEET,
            )
        assert not repo.locked
        assert client.dek_wrapped is None, "cliente encerrado nunca ganha DEK nova"

    async def test_log_da_importacao_so_tem_ids_e_contagens(self) -> None:
        client = await _client_with_dek()
        with capture_logs() as logs:
            await _service(_FakeRepo()).import_sheet(
                client,  # type: ignore[arg-type]
                actor=_actor(),
                content=_SHEET,
            )
        (entry,) = [e for e in logs if e["event"] == "accounting_chart_imported"]
        assert entry["client_id"] == str(client.id)
        assert entry["accounts"] == 3
        dump = repr(logs)
        assert _SECRET_NAME not in dump
        assert "Banco conta movimento" not in dump
        # Código de conta também não: nenhum VALOR logado é um código da planilha
        # (comparar o repr inteiro seria frágil — um UUID pode conter "662").
        values = {str(v) for e in logs for v in e.values()}
        assert values.isdisjoint({"649", "662", "10"})


class TestDecifragem:
    async def test_nome_indecifravel_vira_marcador_e_warning_so_com_ids(self) -> None:
        client = await _client_with_dek()
        conta = _account(code="662")  # ciphertext lixo: não decifra
        with capture_logs() as logs:
            names = await _service(_FakeRepo()).decrypt_names(
                client,  # type: ignore[arg-type]
                [conta],
            )
        assert names.names[conta.id] == "[indecifrável]"
        assert conta.id in names.failed
        (entry,) = [e for e in logs if e["event"] == "accounting_account_decrypt_failed"]
        assert entry["account_id"] == str(conta.id)
        assert entry["log_level"] == "warning"
        assert "662" not in {str(v) for e in logs for v in e.values()}
