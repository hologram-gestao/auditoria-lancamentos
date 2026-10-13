"""O helper que monta `omie_posting` da linha da revisão (86e3n70q0, bloco C).

Sem banco: um `AsyncSession` falso guarda o SELECT para o teste ler. O que se
trava aqui é a FORMA da leitura — uma query para o conjunto inteiro (nunca N+1)
e o `client_id` no `WHERE` (§3.15) — e o mapeamento linha → estado. Que a
leitura devolve o certo contra o Postgres é `test_review_omie_posting_state.py`
da integração.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.db.models import ReconciliationOmiePosting
from app.modules.reconciliations.review.omie_posting_state import (
    load_omie_posting_states,
    posting_state_from_row,
)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _FakeResult:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: list[Any]) -> None:
        self.statements: list[Any] = []
        self._rows = rows

    async def execute(self, stmt: Any) -> _FakeResult:
        self.statements.append(stmt)
        return _FakeResult(self._rows)


def _row(**overrides: Any) -> ReconciliationOmiePosting:
    values: dict[str, Any] = {
        "file_entry_id": uuid4(),
        "kind": "provisao",
        "status": "pending",
        "step": "titulo",
        "attempts": 2,
        "supplier_code": 700_000_001,
        "payable_id": None,
        "payment_id": None,
        "omie_lancamento_id": None,
        "error_code": None,
        "error_message": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)  # type: ignore[return-value]


async def test_uma_query_com_client_id_no_where() -> None:
    client_id = uuid4()
    ids = [uuid4(), uuid4(), uuid4()]
    session = _FakeSession([_row(file_entry_id=ids[0])])

    states = await load_omie_posting_states(
        session,  # type: ignore[arg-type]
        client_id=client_id,
        file_entry_ids=ids,
    )

    assert len(session.statements) == 1, "uma query para a página inteira, nunca N+1"
    compiled = session.statements[0].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    sql = str(compiled)
    assert "reconciliation_omie_postings.client_id = " in sql
    assert f"'{client_id}'" in sql
    assert "reconciliation_omie_postings.file_entry_id IN" in sql
    for entry_id in ids:
        assert f"'{entry_id}'" in sql
    assert list(states) == [ids[0]], "linha sem intenção fica fora do mapa"


async def test_sem_ids_nao_consulta() -> None:
    session = _FakeSession([])
    states = await load_omie_posting_states(
        session,  # type: ignore[arg-type]
        client_id=uuid4(),
        file_entry_ids=[],
    )
    assert states == {}
    assert session.statements == []


@pytest.mark.parametrize(
    "row",
    [
        _row(kind="direto", status="confirmed", step=None, omie_lancamento_id=9, attempts=1),
        _row(
            status="failed",
            step="baixa",
            payable_id=55,
            error_code="OMIE_FAULT",
            error_message="Ocorreu um erro: 1234 - conta bloqueada",
        ),
    ],
    ids=["direto-confirmado", "provisao-parada"],
)
def test_mapeia_todos_os_campos(row: ReconciliationOmiePosting) -> None:
    state = posting_state_from_row(row)
    assert state.model_dump() == {
        "kind": row.kind,
        "status": row.status,
        "step": row.step,
        "attempts": row.attempts,
        "supplier_code": row.supplier_code,
        "payable_id": row.payable_id,
        "payment_id": row.payment_id,
        "omie_lancamento_id": row.omie_lancamento_id,
        "error_code": row.error_code,
        "error_message": row.error_message,
    }
