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

from app.db.models import Client
from app.db.models.client import OMIE_DEFAULT_SUPPLIER_CK, OMIE_DEFAULT_SUPPLIER_CK_LABEL

_CLIENT_MIGRATION = "c4e8a1f7b3d2_client_payables_config.py"


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
