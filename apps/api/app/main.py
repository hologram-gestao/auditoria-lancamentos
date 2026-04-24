"""FastAPI application factory.

S0: esqueleto mínimo (apenas /health).
S1 (atual): logging estruturado, exception handler global, correlation ID middleware.
S2: pools de DB e Redis no lifespan.
S3+: rotas de autenticação, clientes, conciliação, etc.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars

from app import __version__
from app.core.config import get_settings
from app.core.exceptions import AppError, ErrorCode, to_error_response
from app.core.logging import get_logger, setup_logging

CORRELATION_HEADER = "X-Correlation-ID"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ciclo de vida da aplicação.

    S2+: inicializa pools de DB e Redis aqui (close no shutdown).
    S5+: inicializa connection pool do httpx (Omie/Claude).
    """
    setup_logging(get_settings())
    log = get_logger(__name__)
    log.info("app_started", version=__version__)
    yield
    log.info("app_shutdown")


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Garante um correlation ID por request, propagado em logs e response header.

    Lê do header `X-Correlation-ID` (se vier do front/proxy) ou gera UUID v4.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER) or str(uuid4())
        clear_contextvars()
        bind_contextvars(
            correlation_id=correlation_id,
            method=request.method,
            path=request.url.path,
        )
        try:
            response = await call_next(request)
        finally:
            clear_contextvars()
        response.headers[CORRELATION_HEADER] = correlation_id
        return response


def _register_exception_handlers(app: FastAPI) -> None:
    """Conecta handlers para AppError, RequestValidationError e fallback genérico."""
    log = get_logger("app.exceptions")

    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        log.warning(
            "app_error",
            code=exc.code.value,
            status=exc.status_code,
            message=exc.message,
            metadata=exc.metadata,
        )
        return JSONResponse(status_code=exc.status_code, content=to_error_response(exc))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        log.info("validation_error", errors=exc.errors())
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": ErrorCode.VALIDATION_ERROR.value,
                    "message": "Request validation failed.",
                    "userMessage": "Dados inválidos. Verifique os campos enviados.",
                }
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        log.error("unexpected_error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": ErrorCode.INTERNAL_ERROR.value,
                    "message": "Internal server error.",
                    "userMessage": "Ocorreu um erro inesperado. Tente novamente.",
                }
            },
        )


def create_app() -> FastAPI:
    """Cria e configura a instância do FastAPI.

    Factory pattern permite instanciar app com config alternativa em testes.
    """
    settings = get_settings()

    app = FastAPI(
        title="Sistema de Auditoria de Lançamentos — API",
        description="Backend da plataforma interna de conciliação bancária da Hologram.",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
    )

    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Liveness probe — retorna OK se o processo está vivo."""
        return {"status": "ok", "version": __version__}

    @app.get("/health/ready", tags=["system"])
    async def ready() -> dict[str, str]:
        """Readiness probe — S2 expandirá com check de DB + Redis."""
        return {"status": "ready"}

    return app


app = create_app()
