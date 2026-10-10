"""Inclusão e edição MANUAL de conta do plano contábil, sem banco (86e3nb816).

O repositório é falso de propósito: aqui se prova a REGRA (a mesma da planilha, o
409 do código, a recusa da conta em uso só com contagens, a cifra com a pk no AAD,
a `sort_key` derivada em toda gravação, o evento só com IDs e campos). O SQL real
— `client_id` no WHERE, a trava, o `FOR UPDATE` — é provado na integração
(`tests/integration/test_accounting_chart_manual_accounts.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, get_args
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
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
    AccountingAccountCodeExistsError,
    AccountingAccountInUseError,
    AccountingAccountInvalidError,
    AccountingAccountNotFoundError,
    ClientClosedError,
    ErrorCode,
)
from app.db.models import AccountingAccountType, ClientAccountingAccount, UserRole, UserScope
from app.modules.client_accounting_chart.schemas import AccountingAccountUpdateRequest
from app.modules.client_accounting_chart.service import (
    AccountChange,
    AccountingChartService,
    AccountPatch,
)
from app.modules.client_accounting_chart.sheet import (
    REASON_FIELD,
    ChartLineReason,
    validate_account,
)
from app.modules.client_accounting_chart.sort_key import chart_sort_key
from app.modules.usage_events.schemas import AccountChangeField, PlanoContabilContaEditadaProps

pytestmark = pytest.mark.unit

_SECRET_NAME = "Alugueis a receber - Inquilino Sigiloso"


class _FakeClient:
    def __init__(self, *, closed: bool = False) -> None:
        self.id = uuid4()
        self.dek_wrapped: bytes | None = None
        self.closed_at = datetime(2026, 9, 1, tzinfo=UTC) if closed else None


class _FakeDb:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _obj: object) -> None:
        return None


class _FakeRepo:
    def __init__(self) -> None:
        self.accounts: dict[UUID, ClientAccountingAccount] = {}
        self.locked = False
        self.inserts: list[dict[str, Any]] = []
        self.updates: list[tuple[UUID, UUID, dict[str, Any]]] = []
        self.usage = (0, 0)
        self.usage_calls = 0
        self.lookups: list[tuple[UUID, UUID]] = []

    async def lock_client_chart(self, client_id: UUID) -> None:
        self.locked = True

    async def get_by_codes(
        self, client_id: UUID, codes: list[str]
    ) -> list[ClientAccountingAccount]:
        return [a for a in self.accounts.values() if a.client_id == client_id and a.code in codes]

    async def get(self, client_id: UUID, account_id: UUID) -> ClientAccountingAccount | None:
        self.lookups.append((client_id, account_id))
        account = self.accounts.get(account_id)
        return account if account is not None and account.client_id == client_id else None

    async def get_for_update(
        self, client_id: UUID, account_id: UUID
    ) -> ClientAccountingAccount | None:
        return await self.get(client_id, account_id)

    async def count_usage(self, client_id: UUID, account_id: UUID) -> tuple[int, int]:
        self.usage_calls += 1
        return self.usage

    async def insert_accounts(self, rows: list[dict[str, Any]]) -> None:
        self.inserts.extend(rows)
        for row in rows:
            self.accounts[row["id"]] = ClientAccountingAccount(**row)

    async def update_account(
        self, client_id: UUID, account_id: UUID, values: dict[str, Any]
    ) -> None:
        self.updates.append((client_id, account_id, values))
        account = self.accounts[account_id]
        for key, value in values.items():
            setattr(account, key, value)


class _Events:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def emit_plano_contabil_conta_editada(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


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


def _service(
    repo: _FakeRepo, events: _Events | None = None, db: _FakeDb | None = None
) -> AccountingChartService:
    return AccountingChartService(
        db or _FakeDb(),  # type: ignore[arg-type]
        settings=get_settings(),
        repository=repo,  # type: ignore[arg-type]
        usage_events=events or _Events(),  # type: ignore[arg-type]
    )


async def _client() -> _FakeClient:
    client = _FakeClient()
    _cipher, wrapped = await new_client_dek(client.id, settings=get_settings())
    client.dek_wrapped = wrapped
    return client


async def _seed(
    repo: _FakeRepo,
    client: _FakeClient,
    *,
    code: str = "649",
    name: str = "Banco conta movimento",
    account_type: str = "analitica",
    active: bool = True,
    classification: str | None = "1.1.1.02.001",
) -> ClientAccountingAccount:
    cipher = await load_client_cipher(client, settings=get_settings())  # type: ignore[arg-type]
    account_id = uuid4()
    envelope, iv = cipher.encrypt(name, field_locator(AAD_ACCOUNTING_ACCOUNT_NAME, account_id))
    account = ClientAccountingAccount(
        id=account_id,
        client_id=client.id,
        code=code,
        classification=classification,
        sort_key=chart_sort_key(classification, code),
        name_encrypted=envelope,
        name_iv=iv,
        account_type=account_type,
        active=active,
    )
    repo.accounts[account_id] = account
    return account


async def _decrypt(client: _FakeClient, account: ClientAccountingAccount) -> str:
    cipher = await load_client_cipher(client, settings=get_settings())  # type: ignore[arg-type]
    return cipher.decrypt(
        account.name_encrypted,
        account.name_iv,
        field_locator(AAD_ACCOUNTING_ACCOUNT_NAME, account.id),
    )


class TestMesmaRegraDaPlanilha:
    def test_todo_motivo_de_linha_aponta_um_campo(self) -> None:
        # Motivo novo na planilha sem campo aqui daria KeyError na porta manual.
        assert set(REASON_FIELD) == set(get_args(ChartLineReason.__value__))

    @pytest.mark.parametrize(
        ("code", "name", "classification", "reason"),
        [
            ("64;9", "Banco", None, "codigo_invalido"),
            (".649", "Banco", None, "codigo_invalido"),
            ("649", "   ", None, "nome_vazio"),
            ("649", "x" * 201, None, "nome_longo"),
            ("649", "Banco", "1" * 41, "classificacao_longa"),
        ],
    )
    def test_recusa_o_que_a_planilha_recusa(
        self, code: str, name: str, classification: str | None, reason: str
    ) -> None:
        row, got = validate_account(
            code=code,
            name=name,
            account_type=AccountingAccountType.ANALITICA,
            classification=classification,
        )
        assert row is None
        assert got == reason

    def test_apara_espacos_e_classificacao_vazia_vira_nula(self) -> None:
        row, reason = validate_account(
            code=" 649 ",
            name="  Banco  ",
            account_type=AccountingAccountType.SINTETICA,
            classification="  ",
        )
        assert reason is None
        assert row is not None
        assert (row.code, row.name, row.classification) == ("649", "Banco", None)


class TestCriar:
    async def test_grava_cifrado_ativa_e_com_sort_key(self) -> None:
        repo, events, db = _FakeRepo(), _Events(), _FakeDb()
        client = await _client()
        with capture_logs() as logs:
            account, names = await _service(repo, events, db).create_account(
                client,  # type: ignore[arg-type]
                actor=_actor(),
                code="663",
                name=_SECRET_NAME,
                account_type=AccountingAccountType.ANALITICA,
                classification="1.1.2.01.005",
            )
        assert repo.locked
        assert db.commits == 1
        (row,) = repo.inserts
        assert row["client_id"] == client.id
        assert row["active"] is True
        assert row["sort_key"] == chart_sort_key("1.1.2.01.005", "663")
        assert _SECRET_NAME not in row["name_encrypted"]
        # O AAD usa a pk da PRÓPRIA linha: decifra com ela.
        assert await _decrypt(client, account) == _SECRET_NAME
        assert names.names[account.id] == _SECRET_NAME
        assert events.calls == [
            {"client_id": client.id, "account_id": account.id, "operacao": "criada", "campos": []}
        ]
        assert _SECRET_NAME not in repr(logs)
        assert "663" not in repr(logs)

    async def test_codigo_existente_e_409_com_codigo_e_sem_gravar(self) -> None:
        repo, events = _FakeRepo(), _Events()
        client = await _client()
        existing = await _seed(repo, client, code="649", active=False)
        with pytest.raises(AccountingAccountCodeExistsError) as excinfo:
            await _service(repo, events).create_account(
                client,  # type: ignore[arg-type]
                actor=_actor(),
                code="649",
                name="Outra",
                account_type=AccountingAccountType.ANALITICA,
                classification=None,
            )
        exc = excinfo.value
        assert exc.status_code == 409
        assert exc.code is ErrorCode.CONTA_CONTABIL_CODIGO_EXISTENTE
        assert exc.details == {"code": "649", "accountId": str(existing.id)}
        assert repo.inserts == []
        assert events.calls == []

    async def test_campo_invalido_e_422_no_campo(self) -> None:
        repo = _FakeRepo()
        client = await _client()
        with pytest.raises(AccountingAccountInvalidError) as excinfo:
            await _service(repo).create_account(
                client,  # type: ignore[arg-type]
                actor=_actor(),
                code="6 49",
                name="Banco",
                account_type=AccountingAccountType.ANALITICA,
                classification=None,
            )
        assert excinfo.value.status_code == 422
        assert excinfo.value.details == {"field": "code", "reason": "codigo_invalido"}
        assert not repo.locked

    async def test_cliente_encerrado_e_409_antes_de_tudo(self) -> None:
        repo = _FakeRepo()
        with pytest.raises(ClientClosedError):
            await _service(repo).create_account(
                _FakeClient(closed=True),  # type: ignore[arg-type]
                actor=_actor(),
                code="649",
                name="Banco",
                account_type=AccountingAccountType.ANALITICA,
                classification=None,
            )
        assert not repo.locked


class TestEditar:
    async def test_conta_de_outro_cliente_e_404(self) -> None:
        repo = _FakeRepo()
        other = await _client()
        alheia = await _seed(repo, other)
        client = await _client()
        with pytest.raises(AccountingAccountNotFoundError):
            await _service(repo).update_account(
                client,  # type: ignore[arg-type]
                alheia.id,
                actor=_actor(),
                patch=AccountPatch(name="Sequestrada"),
            )
        assert repo.lookups == [(client.id, alheia.id)]
        assert repo.updates == []

    async def test_sem_mudanca_nao_grava_nem_emite(self) -> None:
        repo, events, db = _FakeRepo(), _Events(), _FakeDb()
        client = await _client()
        account = await _seed(repo, client)
        await _service(repo, events, db).update_account(
            client,  # type: ignore[arg-type]
            account.id,
            actor=_actor(),
            patch=AccountPatch(name="Banco conta movimento", active=True),
        )
        assert repo.updates == []
        assert db.commits == 0
        assert events.calls == []

    async def test_nome_e_classificacao_mudam_e_a_ordem_acompanha(self) -> None:
        repo, events = _FakeRepo(), _Events()
        client = await _client()
        account = await _seed(repo, client, classification="1.1.1.02.001")
        old_envelope = account.name_encrypted
        _, names = await _service(repo, events).update_account(
            client,  # type: ignore[arg-type]
            account.id,
            actor=_actor(),
            patch=AccountPatch(name=_SECRET_NAME, classification=None, classification_set=True),
        )
        ((_cid, _aid, values),) = repo.updates
        assert values["classification"] is None
        assert values["sort_key"] == chart_sort_key(None, "649")
        assert values["name_encrypted"] != old_envelope
        assert await _decrypt(client, account) == _SECRET_NAME
        assert names.names[account.id] == _SECRET_NAME
        (call,) = events.calls
        assert call["operacao"] == "editada"
        assert call["campos"] == ["nome", "classificacao"]

    async def test_classificacao_ausente_fica_como_esta(self) -> None:
        repo = _FakeRepo()
        client = await _client()
        account = await _seed(repo, client, classification="1.1.1.02.001")
        await _service(repo).update_account(
            client,  # type: ignore[arg-type]
            account.id,
            actor=_actor(),
            patch=AccountPatch(active=False),
        )
        ((_c, _a, values),) = repo.updates
        assert values["classification"] == "1.1.1.02.001"
        assert "name_encrypted" not in values

    @pytest.mark.parametrize(
        ("patch", "reason"),
        [
            (AccountPatch(account_type=AccountingAccountType.SINTETICA), "sintetica"),
            (AccountPatch(active=False), "inativa"),
        ],
    )
    async def test_conta_em_uso_nao_sai_do_lancavel(self, patch: AccountPatch, reason: str) -> None:
        repo, events = _FakeRepo(), _Events()
        repo.usage = (3, 1)
        client = await _client()
        account = await _seed(repo, client, name=_SECRET_NAME)
        with pytest.raises(AccountingAccountInUseError) as excinfo:
            await _service(repo, events).update_account(
                client,  # type: ignore[arg-type]
                account.id,
                actor=_actor(),
                patch=patch,
            )
        exc = excinfo.value
        assert exc.status_code == 422
        assert exc.code is ErrorCode.CONTA_CONTABIL_EM_USO
        # Só CONTAGENS: nem categoria, nem conta de origem, nem nome.
        assert exc.details == {"reason": reason, "decisionCount": 3, "bindingCount": 1}
        assert "3 decisões do de-para e 1 conta do banco" in exc.user_message
        assert _SECRET_NAME not in exc.user_message
        assert repo.updates == []
        assert events.calls == []

    async def test_conta_sem_uso_pode_virar_sintetica(self) -> None:
        repo = _FakeRepo()
        client = await _client()
        account = await _seed(repo, client)
        await _service(repo).update_account(
            client,  # type: ignore[arg-type]
            account.id,
            actor=_actor(),
            patch=AccountPatch(account_type=AccountingAccountType.SINTETICA),
        )
        assert repo.usage_calls == 1
        assert account.account_type == "sintetica"

    async def test_conta_ja_inativa_nao_e_recontada(self) -> None:
        # A importação pode ter inativado a conta em uso: renomeá-la não recusa.
        repo = _FakeRepo()
        repo.usage = (2, 0)
        client = await _client()
        account = await _seed(repo, client, active=False)
        await _service(repo).update_account(
            client,  # type: ignore[arg-type]
            account.id,
            actor=_actor(),
            patch=AccountPatch(name="Novo nome"),
        )
        assert repo.usage_calls == 0
        assert len(repo.updates) == 1

    async def test_reativar_nao_consulta_o_uso(self) -> None:
        repo = _FakeRepo()
        repo.usage = (5, 5)
        client = await _client()
        account = await _seed(repo, client, active=False)
        await _service(repo).update_account(
            client,  # type: ignore[arg-type]
            account.id,
            actor=_actor(),
            patch=AccountPatch(active=True),
        )
        assert repo.usage_calls == 0
        assert account.active is True


class TestContratoDoPedido:
    def test_classification_null_explicito_limpa_e_ausente_mantem(self) -> None:
        limpa = AccountingAccountUpdateRequest.model_validate({"classification": None}).to_patch()
        mantem = AccountingAccountUpdateRequest.model_validate({"name": "x"}).to_patch()
        assert limpa.classification_set is True
        assert mantem.classification_set is False

    def test_codigo_nao_se_edita(self) -> None:
        with pytest.raises(ValidationError):
            AccountingAccountUpdateRequest.model_validate({"code": "650"})


class TestEvento:
    def test_campos_espelham_o_servico(self) -> None:
        assert set(get_args(AccountChangeField)) == set(get_args(AccountChange.__value__))

    def test_props_so_com_ids_e_vocabulario_fechado(self) -> None:
        with pytest.raises(ValidationError):
            PlanoContabilContaEditadaProps(
                client_id=uuid4(),
                account_id=uuid4(),
                operacao="editada",
                campos=["nome", "codigo"],  # type: ignore[list-item]
            )
        with pytest.raises(ValidationError):
            PlanoContabilContaEditadaProps.model_validate(
                {
                    "client_id": str(uuid4()),
                    "account_id": str(uuid4()),
                    "operacao": "criada",
                    "campos": [],
                    "nome": _SECRET_NAME,
                }
            )
