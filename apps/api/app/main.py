"""FastAPI application factory.

Esqueleto mínimo da S0. A S1 expande com logging estruturado, exception
handlers globais e middlewares de segurança. A S3 adiciona auth.
"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Ciclo de vida da aplicação.

    Em S2+, inicializa pools de DB aqui (engine + session maker).
    Em S5+, inicializa connection pool do httpx (Omie/Claude).
    """
    # startup
    yield
    # shutdown


def create_app() -> FastAPI:
    """Cria e configura a instância do FastAPI.

    Factory pattern permite instanciar app com config diferente em testes.
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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
