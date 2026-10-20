"""CHECKs das parcelas de compra parcelada: modelo x migration (86e3qhhtb).

O autogenerate do Alembic não compara CHECK constraint (skill `migration`, passo 2):
cada predicado é copiado na migration e este arquivo compara as duas fontes, para o
modelo e o banco não divergirem calados.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint

from app.db.models import (
    InstallmentStep,
    ReconciliationFileEntry,
    ReconciliationOmiePostingInstallment,
)
from app.db.models.reconciliation_file_entry import (
    INSTALLMENT_PAIR_CK,
    INSTALLMENT_PAIR_CK_LABEL,
)
from app.db.models.reconciliation_omie_posting_installment import (
    INSTALLMENT_AMOUNT_CK,
    INSTALLMENT_AMOUNT_CK_LABEL,
    INSTALLMENT_CLAIM_CK,
    INSTALLMENT_CLAIM_CK_LABEL,
    INSTALLMENT_NUMBER_CK,
    INSTALLMENT_NUMBER_CK_LABEL,
    INSTALLMENT_PAYABLE_KEY_LENGTH,
    INSTALLMENT_STEP_CK,
    INSTALLMENT_STEP_CK_LABEL,
    INSTALLMENT_STEP_LENGTH,
)
from app.modules.reconciliations.installments import INSTALLMENT_MAX
from app.modules.reconciliations.omie_posting.missing_installments import (
    ANOMALY_CODE_INSTALLMENT_MISSING,
)

_ENTRY_MIGRATION = "b4e7c2a9d613_file_entry_installment_columns.py"
_INSTALLMENTS_MIGRATION = "c8d1f4e6a2b9_omie_posting_installments.py"


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


class TestColunasDaLinha:
    def test_check_do_par_bate_com_a_migration(self) -> None:
        module = _load_migration(_ENTRY_MIGRATION)
        assert module._PAIR_CK == INSTALLMENT_PAIR_CK
        assert module._PAIR_CK_LABEL == INSTALLMENT_PAIR_CK_LABEL

    def test_o_modelo_declara_o_check(self) -> None:
        checks = _checks(ReconciliationFileEntry)
        assert any(
            name.endswith(INSTALLMENT_PAIR_CK_LABEL) and sql == INSTALLMENT_PAIR_CK
            for name, sql in checks.items()
        )

    def test_o_teto_do_check_e_o_da_regra_de_leitura(self) -> None:
        assert f"installment_total <= {INSTALLMENT_MAX}" in INSTALLMENT_PAIR_CK


class TestTabelaDasParcelas:
    def test_checks_batem_com_a_migration(self) -> None:
        module = _load_migration(_INSTALLMENTS_MIGRATION)
        assert module._STEP_CK == INSTALLMENT_STEP_CK
        assert module._STEP_CK_LABEL == INSTALLMENT_STEP_CK_LABEL
        assert module._NUMBER_CK == INSTALLMENT_NUMBER_CK
        assert module._NUMBER_CK_LABEL == INSTALLMENT_NUMBER_CK_LABEL
        assert module._AMOUNT_CK == INSTALLMENT_AMOUNT_CK
        assert module._AMOUNT_CK_LABEL == INSTALLMENT_AMOUNT_CK_LABEL
        assert module._CLAIM_CK == INSTALLMENT_CLAIM_CK
        assert module._CLAIM_CK_LABEL == INSTALLMENT_CLAIM_CK_LABEL

    def test_o_modelo_declara_os_quatro_checks(self) -> None:
        sqls = set(_checks(ReconciliationOmiePostingInstallment).values())
        assert {
            INSTALLMENT_STEP_CK,
            INSTALLMENT_NUMBER_CK,
            INSTALLMENT_AMOUNT_CK,
            INSTALLMENT_CLAIM_CK,
        } <= sqls

    def test_larguras_e_passos_batem_com_a_migration(self) -> None:
        module = _load_migration(_INSTALLMENTS_MIGRATION)
        assert module._KEY_LENGTH == INSTALLMENT_PAYABLE_KEY_LENGTH
        assert module._STEP_LENGTH == INSTALLMENT_STEP_LENGTH
        assert {s.value for s in InstallmentStep} == {"titulo", "concluido"}

    def test_o_tipo_de_anomalia_semeado_e_o_que_o_codigo_procura(self) -> None:
        module = _load_migration(_INSTALLMENTS_MIGRATION)
        assert module._ANOMALY_CODE == ANOMALY_CODE_INSTALLMENT_MISSING
