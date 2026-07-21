"""Testes unitários do ReviewService — helpers puros (sem DB).

Foco: hardening item 3 (S11) — contador in-memory de falhas de decrypt
e correlação por `session_id` no log estruturado. Integração HTTP completa
está em `tests/integration/test_review_endpoints.py`.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
import structlog

from app.core.crypto import ClientCipher
from app.core.crypto_service import AAD_FILE_ENTRY_DESCRIPTION, AAD_FILE_ENTRY_USER_NOTE
from app.modules.reconciliations.review.service import ReviewService

# 32 bytes (256 bits) em hex — chave válida para AES-256, valor irrelevante:
# nada vai ser de fato criptografado nestes testes; só queremos que `decrypt`
# falhe em payloads inválidos.
_FAKE_HEX_KEY = "0" * 64
_PK = uuid4()


def _make_service() -> ReviewService:
    """Service com repo e cache mockados — helpers de decrypt não tocam neles."""
    settings = MagicMock()
    settings.SEARCH_BLIND_INDEX_KEY.get_secret_value.return_value = _FAKE_HEX_KEY
    return ReviewService(
        MagicMock(),
        cache=MagicMock(),
        settings=settings,
    )


def _cipher() -> ClientCipher:
    """Cipher sem DEK — payloads bare inválidos caem no path legado e falham,
    exatamente o que estes testes querem exercitar."""
    return ClientCipher(client_id="c", dek=None, key_id="k1", legacy_hex_key=_FAKE_HEX_KEY)


# ----------------------------------------------------------------------
# Counter increment
# ----------------------------------------------------------------------


def test_decrypt_optional_failure_increments_counter_and_returns_placeholder() -> None:
    service = _make_service()
    service._current_session_id = uuid4()

    # Hex válidos mas tamanhos incoerentes → CryptoError dentro de decrypt.
    result = service._decrypt_optional(_cipher(), "dead", "beef", AAD_FILE_ENTRY_DESCRIPTION, _PK)

    assert result == "[indecifrável]"
    assert service._decrypt_failure_count == 1


def test_decrypt_pair_failure_increments_counter_and_returns_none() -> None:
    service = _make_service()
    service._current_session_id = uuid4()

    result = service._decrypt_pair(_cipher(), "dead", "beef", AAD_FILE_ENTRY_USER_NOTE, _PK)

    assert result is None
    assert service._decrypt_failure_count == 1


def test_multiple_failures_accumulate_in_counter() -> None:
    """Cada falha conta — útil em debug local antes de chegar S17."""
    service = _make_service()
    service._current_session_id = uuid4()

    service._decrypt_optional(_cipher(), "zz", "zz", AAD_FILE_ENTRY_DESCRIPTION, _PK)
    service._decrypt_pair(_cipher(), "zz", "zz", AAD_FILE_ENTRY_USER_NOTE, _PK)
    service._decrypt_optional(_cipher(), "zz", "zz", AAD_FILE_ENTRY_DESCRIPTION, _PK)

    assert service._decrypt_failure_count == 3


# ----------------------------------------------------------------------
# Null/empty inputs NÃO contam como falha (decrypt nem é chamado)
# ----------------------------------------------------------------------


def test_decrypt_optional_with_none_inputs_does_not_count_as_failure() -> None:
    service = _make_service()
    assert service._decrypt_optional(_cipher(), None, None, AAD_FILE_ENTRY_DESCRIPTION, _PK) == ""
    assert service._decrypt_optional(_cipher(), "x", None, AAD_FILE_ENTRY_DESCRIPTION, _PK) == ""
    assert service._decrypt_optional(_cipher(), None, "x", AAD_FILE_ENTRY_DESCRIPTION, _PK) == ""
    assert service._decrypt_failure_count == 0


def test_decrypt_pair_with_none_inputs_does_not_count_as_failure() -> None:
    service = _make_service()
    assert service._decrypt_pair(_cipher(), None, None, AAD_FILE_ENTRY_USER_NOTE, _PK) is None
    assert service._decrypt_pair(_cipher(), "x", None, AAD_FILE_ENTRY_USER_NOTE, _PK) is None
    assert service._decrypt_failure_count == 0


# ----------------------------------------------------------------------
# Log estruturado leva o session_id quando disponível
# ----------------------------------------------------------------------


def test_decrypt_failure_emits_structured_log_with_session_id() -> None:
    """O warning `review_decrypt_failed` carrega `field` e `session_id`."""
    service = _make_service()
    sid = uuid4()
    service._current_session_id = sid

    with structlog.testing.capture_logs() as captured:
        service._decrypt_optional(_cipher(), "bad", "bad", AAD_FILE_ENTRY_DESCRIPTION, _PK)
        service._decrypt_pair(_cipher(), "bad", "bad", AAD_FILE_ENTRY_USER_NOTE, _PK)

    events = [c for c in captured if c.get("event") == "review_decrypt_failed"]
    assert len(events) == 2

    assert events[0]["field"] == "description"
    assert events[0]["session_id"] == str(sid)
    assert events[1]["field"] == "user_note_or_context"
    assert events[1]["session_id"] == str(sid)


def test_decrypt_failure_log_session_id_is_none_when_not_set() -> None:
    """Helper exercido fora do fluxo público: session_id fica None — não quebra."""
    service = _make_service()
    # service._current_session_id permanece None (default do __init__)

    with structlog.testing.capture_logs() as captured:
        service._decrypt_optional(_cipher(), "bad", "bad", AAD_FILE_ENTRY_DESCRIPTION, _PK)

    events = [c for c in captured if c.get("event") == "review_decrypt_failed"]
    assert len(events) == 1
    assert events[0]["session_id"] is None


# ----------------------------------------------------------------------
# Sanity: serviço novo nasce com contador zerado
# ----------------------------------------------------------------------


@pytest.mark.parametrize("_run", range(2))  # checa entre instâncias separadas
def test_counter_starts_at_zero(_run: int) -> None:
    service = _make_service()
    assert service._decrypt_failure_count == 0
    assert service._current_session_id is None
