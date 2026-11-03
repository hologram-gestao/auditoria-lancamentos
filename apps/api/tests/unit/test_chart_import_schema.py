"""O registro das importações do plano contábil tem DUAS fontes: modelo e migration (86e3ydjze).

O autogenerate do Alembic **não** compara CHECK constraint: nada avisaria se um predicado
mudasse num lado só. E as leis que o schema carrega:

1. **nenhum nome, nenhuma célula** — a tabela não tem coluna de texto livre além do
   `layout` (vocabulário fechado) e do JSONB de `{linha, motivo}`;
2. **purga no encerramento e ordem da exclusão** — `client_accounting_chart_imports` está
   em `close_client_purge` e sai ANTES dos usuários em `delete_client_cascade`
   (`imported_by` é RESTRICT).
"""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, String

from app.db.models import ClientAccountingChartImport
from app.db.models.client_accounting_chart_import import (
    CHART_IMPORT_COUNTS_CHECK,
    CHART_IMPORT_COUNTS_CK_LABEL,
    CHART_IMPORT_EXCLUDED_CHECK,
    CHART_IMPORT_EXCLUDED_CK_LABEL,
    CHART_IMPORT_LATEST_INDEX,
    MAX_CHART_IMPORT_LAYOUT_CHARS,
)
from app.modules.client_accounting_chart.sheet import ChartLayout
from app.modules.clients.repository import ClientRepository

_MIGRATION = "e3a1c7d5b9f2_chart_imports_log.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load_migration() -> ModuleType:
    path = _VERSIONS / _MIGRATION
    spec = importlib.util.spec_from_file_location("_migration_e3a1c7d5b9f2", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checks() -> dict[str, str]:
    return {
        str(c.name): str(c.sqltext)
        for c in ClientAccountingChartImport.__table__.constraints
        if isinstance(c, CheckConstraint)
    }


def test_os_checks_do_modelo_e_da_migration_sao_os_mesmos() -> None:
    migration = _load_migration()
    assert migration._COUNTS_CK == CHART_IMPORT_COUNTS_CHECK
    assert migration._COUNTS_CK_LABEL == CHART_IMPORT_COUNTS_CK_LABEL
    assert migration._EXCLUDED_CK == CHART_IMPORT_EXCLUDED_CHECK
    assert migration._EXCLUDED_CK_LABEL == CHART_IMPORT_EXCLUDED_CK_LABEL
    assert migration._LATEST_INDEX == CHART_IMPORT_LATEST_INDEX
    assert migration._LAYOUT_LENGTH == MAX_CHART_IMPORT_LAYOUT_CHARS
    checks = _checks()
    assert checks[f"ck_client_accounting_chart_imports_{CHART_IMPORT_COUNTS_CK_LABEL}"] == (
        CHART_IMPORT_COUNTS_CHECK
    )
    assert checks[f"ck_client_accounting_chart_imports_{CHART_IMPORT_EXCLUDED_CK_LABEL}"] == (
        CHART_IMPORT_EXCLUDED_CHECK
    )


def test_o_layout_mais_longo_cabe_na_coluna() -> None:
    column = ClientAccountingChartImport.__table__.c.layout
    assert isinstance(column.type, String)
    assert column.type.length == MAX_CHART_IMPORT_LAYOUT_CHARS
    assert max(len(v) for v in ChartLayout.__value__.__args__) <= MAX_CHART_IMPORT_LAYOUT_CHARS


def test_nenhuma_coluna_de_texto_livre() -> None:
    texts = {
        c.name for c in ClientAccountingChartImport.__table__.columns if isinstance(c.type, String)
    }
    assert texts == {"layout"}


def test_purga_no_encerramento_e_antes_dos_usuarios_na_exclusao() -> None:
    purge = inspect.getsource(ClientRepository.close_client_purge)
    assert "ClientAccountingChartImport" in purge
    cascade = inspect.getsource(ClientRepository.delete_client_cascade)
    assert cascade.index("ClientAccountingChartImport") < cascade.index("delete(User)")
