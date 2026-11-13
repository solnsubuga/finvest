"""Build the one FastAPI application. Modules add their routers here."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.api.errors import UnexpectedErrorMiddleware, install_error_handlers
from app.api.request_id import RequestIdMiddleware
from app.infrastructure.database import Database
from app.infrastructure.settings import Settings
from app.shared.clock import Clock, SystemClock
from app.shared.errors import ErrorEnvelope


def create_app(
    settings: Settings, database: Database | None = None, clock: Clock | None = None
) -> FastAPI:
    owned = database is None
    database = database or Database(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        if owned:
            await database.close()

    app = FastAPI(
        title="Finvest API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.api_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.api_docs else None,
        responses={422: {"model": ErrorEnvelope}},
    )
    app.state.settings = settings
    app.state.database = database
    app.state.clock = clock or SystemClock()
    install_error_handlers(app)
    # The last middleware added is the outermost, so the request ID exists before anything runs.
    app.add_middleware(UnexpectedErrorMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.include_router(health.router)
    return app
