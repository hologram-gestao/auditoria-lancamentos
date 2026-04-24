"""Testes da hierarquia de exceções e do serializer de resposta."""

from __future__ import annotations

import pytest

from app.core.exceptions import (
    AppError,
    DuplicateFileError,
    ErrorCode,
    ForbiddenError,
    NotFoundError,
    OmieAuthError,
    OmieFaultError,
    OmieTimeoutError,
    ParseError,
    RateLimitedError,
    TokenExpiredError,
    UnauthorizedError,
    ValidationAppError,
    to_error_response,
)


class TestAppErrorBase:
    def test_default_values(self) -> None:
        exc = AppError()
        assert exc.code == ErrorCode.INTERNAL_ERROR
        assert exc.status_code == 500
        assert "inesperado" in exc.user_message.lower()

    def test_custom_message_and_user_message(self) -> None:
        exc = AppError("dev message", user_message="msg para usuário")
        assert exc.message == "dev message"
        assert exc.user_message == "msg para usuário"

    def test_metadata_default_empty(self) -> None:
        exc = AppError()
        assert exc.metadata == {}

    def test_metadata_passed_through(self) -> None:
        exc = AppError(metadata={"file_hash": "abc123"})
        assert exc.metadata == {"file_hash": "abc123"}


class TestSubclassDefaults:
    @pytest.mark.parametrize(
        ("exc_class", "expected_code", "expected_status"),
        [
            (ValidationAppError, ErrorCode.VALIDATION_ERROR, 400),
            (UnauthorizedError, ErrorCode.UNAUTHORIZED, 401),
            (TokenExpiredError, ErrorCode.TOKEN_EXPIRED, 401),
            (ForbiddenError, ErrorCode.FORBIDDEN, 403),
            (NotFoundError, ErrorCode.NOT_FOUND, 404),
            (DuplicateFileError, ErrorCode.DUPLICATE_FILE, 409),
            (RateLimitedError, ErrorCode.RATE_LIMITED, 429),
            (ParseError, ErrorCode.PARSE_ERROR, 422),
            (OmieAuthError, ErrorCode.OMIE_AUTH_ERROR, 502),
            (OmieFaultError, ErrorCode.OMIE_FAULT, 502),
            (OmieTimeoutError, ErrorCode.OMIE_TIMEOUT, 504),
        ],
    )
    def test_subclass_has_correct_code_and_status(
        self, exc_class: type[AppError], expected_code: ErrorCode, expected_status: int
    ) -> None:
        exc = exc_class()
        assert exc.code == expected_code
        assert exc.status_code == expected_status

    def test_subclass_default_user_message_is_pt_br(self) -> None:
        """Mensagens padrão devem estar em PT-BR para o usuário final."""
        for cls in [
            DuplicateFileError,
            UnauthorizedError,
            ForbiddenError,
            OmieAuthError,
            ParseError,
        ]:
            assert cls().user_message  # não vazio
            # heurística simples: sem palavras-chave em inglês
            msg = cls().user_message.lower()
            assert "error" not in msg, f"{cls.__name__}: msg parece em inglês"


class TestToErrorResponse:
    def test_format_matches_api_contract(self) -> None:
        exc = DuplicateFileError("hash collision detected")
        result = to_error_response(exc)
        assert result == {
            "error": {
                "code": "DUPLICATE_FILE",
                "message": "hash collision detected",
                "userMessage": exc.user_message,
            }
        }

    def test_metadata_is_not_in_response(self) -> None:
        """Metadata vai para logs, não para o cliente."""
        exc = AppError("dev", metadata={"sensitive": "data"})
        result = to_error_response(exc)
        assert "metadata" not in result["error"]
        assert "sensitive" not in str(result)

    def test_custom_user_message_used(self) -> None:
        exc = ForbiddenError(user_message="Apenas admin pode fazer isto.")
        result = to_error_response(exc)
        assert result["error"]["userMessage"] == "Apenas admin pode fazer isto."


class TestErrorCodeEnum:
    def test_all_codes_are_uppercase_snake(self) -> None:
        for code in ErrorCode:
            assert code.value == code.value.upper()
            assert " " not in code.value

    def test_code_value_equals_name(self) -> None:
        """Convenção: nome do enum == valor (facilita debug)."""
        for code in ErrorCode:
            assert code.value == code.name
