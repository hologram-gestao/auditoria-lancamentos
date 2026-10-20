"""O estado da intenção de lançamento na linha da revisão (86e3n70q0, bloco C).

`omie_posting` em `GET /file-entries` e no `related_file_entry` de `GET /anomalies`
é o que deixa a tela mostrar um selo persistente ("Provisionado · título nº X",
"Lançamento parado · passo baixa") e travar a forma do lançamento. O que estes
testes travam:

- linha com intenção DIRETA confirmada, PROVISÃO parada e linha SEM intenção, na
  mesma página, cada uma com o seu objeto (ou `null`);
- a aba Anomalias recebe o MESMO objeto para a linha que a anomalia aponta;
- a mensagem do provedor vem na resposta (é para a pessoa ler) e o CNPJ, que
  nunca persiste, nunca aparece.

As intenções são gravadas direto no banco, na forma que os CHECKs exigem: o que
se prova aqui é a LEITURA. Como elas nascem é `test_omie_payables_posting.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from app.db.models import (
    OmiePostingKind,
    OmiePostingStatus,
    OmiePostingStep,
    ReconciliationFileEntry,
    ReconciliationOmiePosting,
)
from app.modules.reconciliations.omie_posting.keys import derive_cod_int_lanc
from tests.integration.test_omie_posting_endpoint import (
    Scenario,
    _login,
    _seed_client,
    _seed_entry,
    _seed_missing_in_omie_anomaly,
    _seed_session,
    _seed_user,
)

if TYPE_CHECKING:
    from httpx import AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.integration

PROVIDER_MESSAGE = "Ocorreu um erro ao acessar o Omie: 1234 - conta bloqueada"


@pytest.fixture
async def scenario(db_session: AsyncSession) -> Scenario:
    admin = await _seed_user(db_session, email=f"estado-{uuid4().hex[:8]}@hologram.com.br")
    client = await _seed_client(db_session, creator=admin, name=f"Estado {uuid4().hex[:6]}")
    sess = await _seed_session(db_session, client=client, creator=admin)
    return Scenario(admin, client, sess)


async def _seed_three_lines(
    db: AsyncSession, scenario: Scenario
) -> tuple[ReconciliationFileEntry, ReconciliationFileEntry, ReconciliationFileEntry]:
    sess = scenario.session
    direct = await _seed_entry(db, sess=sess, day=10)
    stuck = await _seed_entry(db, sess=sess, day=11)
    untouched = await _seed_entry(db, sess=sess, day=12)
    db.add_all(
        [
            ReconciliationOmiePosting(
                session_id=sess.id,
                client_id=sess.client_id,
                file_entry_id=direct.id,
                cod_int_lanc=derive_cod_int_lanc(direct.id),
                status=OmiePostingStatus.CONFIRMED.value,
                kind=OmiePostingKind.DIRETO.value,
                attempts=1,
                omie_lancamento_id=4_321_001,
            ),
            ReconciliationOmiePosting(
                session_id=sess.id,
                client_id=sess.client_id,
                file_entry_id=stuck.id,
                cod_int_lanc=derive_cod_int_lanc(stuck.id),
                status=OmiePostingStatus.FAILED.value,
                kind=OmiePostingKind.PROVISAO.value,
                step=OmiePostingStep.BAIXA.value,
                attempts=0,
                supplier_code=700_000_001,
                payable_id=8_800_001,
                error_code="OMIE_FAULT",
                error_message=PROVIDER_MESSAGE,
            ),
        ]
    )
    await db.flush()
    return direct, stuck, untouched


async def test_listagem_traz_o_estado_de_cada_linha(
    client_with_db: AsyncClient, db_session: AsyncSession, scenario: Scenario
) -> None:
    direct, stuck, untouched = await _seed_three_lines(db_session, scenario)
    await _login(client_with_db, scenario.admin.email)

    resp = await client_with_db.get(f"/api/v1/reconciliations/{scenario.session.id}/file-entries")
    assert resp.status_code == 200, resp.text
    by_id = {row["id"]: row["omie_posting"] for row in resp.json()["data"]}

    assert by_id[str(direct.id)] == {
        "kind": "direto",
        "status": "confirmed",
        "step": None,
        "attempts": 1,
        "supplier_code": None,
        "payable_id": None,
        "payment_id": None,
        "omie_lancamento_id": 4_321_001,
        "error_code": None,
        "error_message": None,
        "installments": [],
    }
    assert by_id[str(stuck.id)] == {
        "kind": "provisao",
        "status": "failed",
        "step": "baixa",
        "attempts": 0,
        "supplier_code": 700_000_001,
        "payable_id": 8_800_001,
        "payment_id": None,
        "omie_lancamento_id": None,
        "error_code": "OMIE_FAULT",
        "error_message": PROVIDER_MESSAGE,
        "installments": [],
    }
    assert by_id[str(untouched.id)] is None


async def test_anomalia_traz_o_mesmo_objeto_da_linha(
    client_with_db: AsyncClient, db_session: AsyncSession, scenario: Scenario
) -> None:
    _direct, stuck, untouched = await _seed_three_lines(db_session, scenario)
    await _seed_missing_in_omie_anomaly(db_session, sess=scenario.session, entry=stuck)
    await _seed_missing_in_omie_anomaly(db_session, sess=scenario.session, entry=untouched)
    await _login(client_with_db, scenario.admin.email)

    sid = scenario.session.id
    anomalies = (await client_with_db.get(f"/api/v1/reconciliations/{sid}/anomalies")).json()
    entries = (await client_with_db.get(f"/api/v1/reconciliations/{sid}/file-entries")).json()

    listed = {row["id"]: row["omie_posting"] for row in entries["data"]}
    related = {
        a["related_file_entry"]["id"]: a["related_file_entry"]["omie_posting"]
        for a in anomalies["data"]
    }
    assert related[str(stuck.id)] == listed[str(stuck.id)]
    assert related[str(stuck.id)]["step"] == "baixa"
    assert related[str(untouched.id)] is None


async def test_patch_da_linha_devolve_o_estado(
    client_with_db: AsyncClient, db_session: AsyncSession, scenario: Scenario
) -> None:
    """O PATCH responde a linha inteira; sem o estado, o cache do front perderia o selo."""
    _direct, stuck, _untouched = await _seed_three_lines(db_session, scenario)
    await _login(client_with_db, scenario.admin.email)

    resp = await client_with_db.patch(
        f"/api/v1/reconciliations/{scenario.session.id}/file-entries/{stuck.id}",
        json={"user_note": "conferir no Omie"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["omie_posting"]["payable_id"] == 8_800_001
