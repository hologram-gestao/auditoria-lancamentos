"""O modo de lançamento do cartão tem TRÊS fontes: o modelo e duas migrations (86e3n70p0).

O autogenerate do Alembic não compara CHECK constraint, então nada avisaria se um
valor novo entrasse em `CardPostingDateMode` e o CHECK do banco ficasse para trás
(o INSERT do valor novo seria recusado em produção). Mesmo padrão de
`test_organization_schema.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint

from app.db.models import (
    CARD_DUE_DATE_COHERENT_CHECK,
    CARD_DUE_DATE_COHERENT_CK_LABEL,
    CARD_POSTING_DATE_MODE_CK_LABEL,
    CARD_POSTING_DATE_MODE_LENGTH,
    CardPostingDateMode,
    Client,
    ReconciliationSession,
    card_posting_date_mode_check,
)

_CLIENT_MIGRATION = "8745bf30d8c8_card_posting_date_mode_client.py"
_SESSION_MIGRATION = "d881eabdceb7_card_invoice_due_date_session.py"


def _load_migration(filename: str) -> ModuleType:
    """Carrega a migration pelo CAMINHO — `alembic/versions/` não é pacote importável."""
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"_migration_{filename[:12]}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_of(model: type[object]) -> str:
    table = model.__table__  # type: ignore[attr-defined]
    checks = [
        c
        for c in table.constraints
        if isinstance(c, CheckConstraint) and str(c.name).endswith(CARD_POSTING_DATE_MODE_CK_LABEL)
    ]
    assert len(checks) == 1
    return str(checks[0].sqltext)


class TestModeloEMigrationsBatem:
    def test_check_do_cliente_bate_com_a_migration(self) -> None:
        module = _load_migration(_CLIENT_MIGRATION)
        assert card_posting_date_mode_check() == module._CK
        assert module._CK_LABEL == CARD_POSTING_DATE_MODE_CK_LABEL
        assert module._LENGTH == CARD_POSTING_DATE_MODE_LENGTH
        assert CardPostingDateMode.PURCHASE_DATE.value == module._DEFAULT

    def test_check_da_sessao_bate_com_a_migration(self) -> None:
        module = _load_migration(_SESSION_MIGRATION)
        assert card_posting_date_mode_check(nullable=True) == module._CK
        assert module._CK_LABEL == CARD_POSTING_DATE_MODE_CK_LABEL
        assert module._LENGTH == CARD_POSTING_DATE_MODE_LENGTH

    def test_check_de_coerencia_do_vencimento_bate_com_a_migration(self) -> None:
        module = _load_migration(_SESSION_MIGRATION)
        assert CARD_DUE_DATE_COHERENT_CHECK == module._COHERENT_CK
        assert CARD_DUE_DATE_COHERENT_CK_LABEL == module._COHERENT_CK_LABEL
        checks = [
            c
            for c in ReconciliationSession.__table__.constraints
            if isinstance(c, CheckConstraint)
            and str(c.name).endswith(CARD_DUE_DATE_COHERENT_CK_LABEL)
        ]
        assert [str(c.sqltext) for c in checks] == [CARD_DUE_DATE_COHERENT_CHECK]

    def test_a_sessao_vem_depois_do_cliente(self) -> None:
        assert _load_migration(_SESSION_MIGRATION).down_revision == (
            _load_migration(_CLIENT_MIGRATION).revision
        )


class TestModeloDeclaraAsGarantias:
    def test_cliente_declara_o_check_e_o_default(self) -> None:
        assert _check_of(Client) == card_posting_date_mode_check()
        column = Client.__table__.c.card_posting_date_mode
        assert column.nullable is False
        assert column.type.length == CARD_POSTING_DATE_MODE_LENGTH
        assert column.server_default is not None
        assert CardPostingDateMode.PURCHASE_DATE.value in str(column.server_default.arg)

    def test_sessao_declara_o_check_nulavel(self) -> None:
        assert _check_of(ReconciliationSession) == card_posting_date_mode_check(nullable=True)
        table = ReconciliationSession.__table__
        assert table.c.card_posting_date_mode.nullable is True
        assert table.c.invoice_due_date.nullable is True

    def test_o_check_lista_todos_os_valores_da_enum(self) -> None:
        for member in CardPostingDateMode:
            assert f"'{member.value}'" in card_posting_date_mode_check()
