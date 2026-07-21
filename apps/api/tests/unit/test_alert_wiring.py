"""Wiring do alerting nas fontes de alerta (Sprint 3, BACK 03.6).

Prova que os pontos que DEVEM alertar chamam o alerting:
    - sessão em `error` (`job._safe_mark_error`) → SESSION_ERROR (mesmo não 5xx).
    - falha de decifragem na review (`_record_decrypt_failure`) → DECRYPT_FAILED
      (uma vez por request, ligada ao counter existente).
    - falha de decifragem no export incrementa o counter que dispara o alerta.
"""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.core.alerting import AlertCode, AlertDeliveryResult
from app.core.config import Settings
from app.core.crypto import ClientCipher
from app.core.crypto_service import AAD_FILE_ENTRY_DESCRIPTION

_FAKE_HEX_KEY = "0" * 64


def _bad_cipher() -> ClientCipher:
    return ClientCipher(client_id="c", dek=None, key_id="k1", legacy_hex_key=_FAKE_HEX_KEY)


class TestSessionErrorAlert:
    async def test_safe_mark_error_sends_session_error_alert(self, monkeypatch: object) -> None:
        from app.modules.reconciliations.processing import job

        captured = []

        async def _fake_send(alert: object, settings: object) -> AlertDeliveryResult:
            captured.append(alert)
            return AlertDeliveryResult(webhook=None, email=None)

        monkeypatch.setattr(job, "send_alert", _fake_send)  # type: ignore[attr-defined]

        def _boom_factory() -> object:
            # DB indisponível neste teste — a marcação falha (logada), mas o
            # alerta DEVE disparar mesmo assim (fonte de falha).
            raise RuntimeError("sem DB neste unit test")

        await job._safe_mark_error(
            uuid4(), _boom_factory, "Erro generico ao processar", settings=Settings()
        )

        assert len(captured) == 1
        assert captured[0].code == AlertCode.SESSION_ERROR
        assert captured[0].message == "Erro generico ao processar"


class TestReviewDecryptAlert:
    def _service(self) -> object:
        from app.modules.reconciliations.review.service import ReviewService

        settings = MagicMock()
        settings.SEARCH_BLIND_INDEX_KEY.get_secret_value.return_value = _FAKE_HEX_KEY
        return ReviewService(MagicMock(), cache=MagicMock(), settings=settings)

    def test_decrypt_failure_dispatches_alert_once(self, monkeypatch: object) -> None:
        from app.modules.reconciliations.review import service as svc

        calls = []
        monkeypatch.setattr(  # type: ignore[attr-defined]
            svc, "dispatch_alert_nowait", lambda alert, settings: calls.append(alert)
        )
        service = self._service()

        service._record_decrypt_failure(field="description")
        service._record_decrypt_failure(field="user_note_or_context")

        # Uma vez (na 1ª falha) — não inunda o canal.
        assert len(calls) == 1
        assert calls[0].code == AlertCode.DECRYPT_FAILED


class TestExportDecryptCounter:
    def _service(self) -> object:
        from app.modules.reconciliations.export.service import ExportService

        return ExportService(MagicMock(), cache=MagicMock(), settings=MagicMock())

    def test_decrypt_failure_increments_counter(self) -> None:
        service = self._service()
        pk = uuid4()
        # payload bare inválido → CryptoError → counter++ (dispara o alerta no
        # fim do build_payload).
        out = service._decrypt_optional(
            _bad_cipher(), "zz", "zz", AAD_FILE_ENTRY_DESCRIPTION, pk, field="description"
        )
        assert out is None
        assert service._decrypt_failures == 1
        required = service._decrypt_required(
            _bad_cipher(), "zz", "zz", AAD_FILE_ENTRY_DESCRIPTION, pk, field="description"
        )
        assert required == "[indecifrável]"
        assert service._decrypt_failures == 2
