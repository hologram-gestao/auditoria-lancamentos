"""CHECKs do lançamento como Contas a Pagar: modelo x migration (86e3n70q0, bloco B).

O autogenerate do Alembic não compara CHECK constraint (skill `migration`, passo 2):
se o predicado do modelo mudar e o da migration ficar para trás, nada avisa até o
INSERT ser recusado em produção. Cada predicado é copiado na migration e este
arquivo compara as duas fontes.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint

from app.db.models import (
    Client,
    OmiePostingKind,
    OmiePostingStatus,
    OmiePostingStep,
    ReconciliationOmiePosting,
)
from app.db.models.client import OMIE_DEFAULT_SUPPLIER_CK, OMIE_DEFAULT_SUPPLIER_CK_LABEL
from app.db.models.reconciliation_omie_posting import (
    POSTING_KIND_CK,
    POSTING_KIND_CK_LABEL,
    POSTING_KIND_LENGTH,
    POSTING_STATUS_CK,
    POSTING_STATUS_CK_LABEL,
    POSTING_STEP_LENGTH,
    POSTING_STEPS_CK,
    POSTING_STEPS_CK_LABEL,
)

_CLIENT_MIGRATION = "c4e8a1f7b3d2_client_payables_config.py"
_POSTINGS_MIGRATION = "d9f3b6a2e815_omie_posting_payables_state_machine.py"


def _load_migration(filename: str) -> ModuleType:
    """Carrega a migration pelo CAMINHO — `alembic/versions/` não é pacote importável."""
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / filename
    spec = importlib.util.spec_from_file_location(f"_migration_{filename[:12]}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checks(model: type[object]) -> dict[str, str]:
    table = model.__table__  # type: ignore[attr-defined]
    return {
        str(c.name): str(c.sqltext) for c in table.constraints if isinstance(c, CheckConstraint)
    }


class TestConfiguracaoDoCliente:
    def test_check_do_fornecedor_padrao_bate_com_a_migration(self) -> None:
        module = _load_migration(_CLIENT_MIGRATION)
        assert module._CK == OMIE_DEFAULT_SUPPLIER_CK
        assert module._CK_LABEL == OMIE_DEFAULT_SUPPLIER_CK_LABEL

    def test_o_modelo_declara_o_check(self) -> None:
        checks = _checks(Client)
        assert any(
            name.endswith(OMIE_DEFAULT_SUPPLIER_CK_LABEL) and sql == OMIE_DEFAULT_SUPPLIER_CK
            for name, sql in checks.items()
        )


class TestMaquinaDeEstados:
    def test_checks_das_postings_batem_com_a_migration(self) -> None:
        module = _load_migration(_POSTINGS_MIGRATION)
        assert module._STATUS_CK == POSTING_STATUS_CK
        assert module._STATUS_CK_LABEL == POSTING_STATUS_CK_LABEL
        assert module._KIND_CK == POSTING_KIND_CK
        assert module._KIND_CK_LABEL == POSTING_KIND_CK_LABEL
        assert module._STEPS_CK == POSTING_STEPS_CK
        assert module._STEPS_CK_LABEL == POSTING_STEPS_CK_LABEL
        assert module._KIND_LENGTH == POSTING_KIND_LENGTH
        assert module._STEP_LENGTH == POSTING_STEP_LENGTH
        assert OmiePostingKind.DIRETO.value == module._KIND_DEFAULT

    def test_o_modelo_declara_os_tres_checks(self) -> None:
        checks = _checks(ReconciliationOmiePosting)
        for label, sql in (
            (POSTING_STATUS_CK_LABEL, POSTING_STATUS_CK),
            (POSTING_KIND_CK_LABEL, POSTING_KIND_CK),
            (POSTING_STEPS_CK_LABEL, POSTING_STEPS_CK),
        ):
            assert any(n.endswith(label) and s == sql for n, s in checks.items()), label

    def test_os_checks_cobrem_todos_os_valores_dos_enums(self) -> None:
        """Valor novo num enum sem o CHECK acompanhar = INSERT recusado em produção."""
        for status in OmiePostingStatus:
            assert f"'{status.value}'" in POSTING_STATUS_CK
        for kind in OmiePostingKind:
            assert f"'{kind.value}'" in POSTING_KIND_CK
            assert len(kind.value) <= POSTING_KIND_LENGTH
        for step in OmiePostingStep:
            assert f"'{step.value}'" in POSTING_KIND_CK
            assert len(step.value) <= POSTING_STEP_LENGTH

    def test_o_contrato_espelha_os_enums_do_modelo(self) -> None:
        """`kind` e `step` da resposta são `Literal` do schema: não podem divergir."""
        from typing import get_args

        from app.modules.reconciliations.omie_posting.schemas import (
            PostingKindValue,
            PostingStepValue,
        )

        assert set(get_args(PostingKindValue)) == {k.value for k in OmiePostingKind}
        assert set(get_args(PostingStepValue)) == {s.value for s in OmiePostingStep}
