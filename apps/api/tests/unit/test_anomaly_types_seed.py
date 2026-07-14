"""Invariante do catálogo de anomalias (BACK 02.5).

"Schema sem lógica é mentira": nenhum tipo pode ficar no seed sem um detector
que o gere. Este teste é o "SELECT dos tipos x grep dos detectores batem" em
forma de código — compara os codes semeados com os codes que os detectores
REALMENTE emitem (as constantes que os detectores usam).
"""

from __future__ import annotations

from scripts.seed_dev import ANOMALY_TYPES_SEED

from app.modules.reconciliations.processing.anomalies import (
    ANOMALY_CODE_MISSING_IN_FILE,
    ANOMALY_CODE_MISSING_IN_OMIE,
)
from app.modules.reconciliations.qualification.service import _ALL_QUALIF_CODES

# Codes que ALGUM detector emite:
#   - estruturais: processing/anomalies.py (matching)
#   - qualificação: qualification/service.py (S19)
_DETECTOR_CODES: set[str] = {
    ANOMALY_CODE_MISSING_IN_OMIE,
    ANOMALY_CODE_MISSING_IN_FILE,
    *_ALL_QUALIF_CODES,
}

# Os 6 removidos na BACK 02.5 (nunca tiveram detector).
_REMOVED_ORPHAN_CODES: set[str] = {
    "wrong_account",
    "inconsistent_category",
    "category_mismatch_nature",
    "internal_transfer_as_revenue",
    "possible_duplicate",
    "classification_improvement",
}


def _seed_codes() -> set[str]:
    return {item["code"] for item in ANOMALY_TYPES_SEED}


class TestAnomalyCatalogInvariant:
    def test_every_seeded_type_has_a_detector(self) -> None:
        """Nenhum tipo órfão no seed — todo code semeado é emitido por algum
        detector, e todo detector tem seu tipo no seed."""
        assert _seed_codes() == _DETECTOR_CODES

    def test_removed_orphans_are_absent_from_seed(self) -> None:
        assert _REMOVED_ORPHAN_CODES.isdisjoint(_seed_codes())

    def test_seed_has_exactly_six_types(self) -> None:
        # 2 estruturais + 4 qualificação.
        assert len(ANOMALY_TYPES_SEED) == 6

    def test_no_duplicate_codes_in_seed(self) -> None:
        codes = [item["code"] for item in ANOMALY_TYPES_SEED]
        assert len(codes) == len(set(codes))
