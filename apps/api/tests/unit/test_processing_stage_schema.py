"""A etapa do processamento tem DUAS fontes: o modelo e a migration (86e3mpmcw).

O autogenerate do Alembic não compara CHECK constraint, então nada avisaria se uma
etapa nova entrasse em `ProcessingStage` e o CHECK do banco ficasse para trás (o
UPDATE do job seria recusado em produção). Mesmo padrão de
`test_card_posting_date_mode_schema.py`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint

from app.db.models import (
    PROCESSING_STAGE_CK_LABEL,
    PROCESSING_STAGE_LENGTH,
    ProcessingStage,
    ReconciliationSession,
    processing_stage_check,
)

_MIGRATION = "a4c8e2f6b1d3_session_processing_stage.py"


def _load_migration() -> ModuleType:
    """Carrega a migration pelo CAMINHO — `alembic/versions/` não é pacote importável."""
    path = Path(__file__).resolve().parents[2] / "alembic" / "versions" / _MIGRATION
    spec = importlib.util.spec_from_file_location("_migration_processing_stage", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_do_modelo_bate_com_a_migration() -> None:
    module = _load_migration()
    assert processing_stage_check() == module._CK
    assert module._CK_LABEL == PROCESSING_STAGE_CK_LABEL
    assert module._LENGTH == PROCESSING_STAGE_LENGTH


def test_modelo_declara_o_check_e_a_coluna_nulavel() -> None:
    table = ReconciliationSession.__table__
    checks = [
        str(c.sqltext)
        for c in table.constraints
        if isinstance(c, CheckConstraint) and str(c.name).endswith(PROCESSING_STAGE_CK_LABEL)
    ]
    assert checks == [processing_stage_check()]
    column = table.c.processing_stage
    assert column.nullable is True
    assert column.type.length == PROCESSING_STAGE_LENGTH


def test_o_check_lista_as_quatro_etapas_na_ordem_do_job() -> None:
    assert [s.value for s in ProcessingStage] == ["carregando", "omie", "cruzamento", "gravacao"]
    for member in ProcessingStage:
        assert f"'{member.value}'" in processing_stage_check()
