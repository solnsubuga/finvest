"""Build the one FastAPI application. Modules add their routers here."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api import health
from app.api.errors import UnexpectedErrorMiddleware, install_error_handlers
from app.api.hardening import BodyLimitMiddleware, SecurityHeadersMiddleware
from app.api.request_id import RequestIdMiddleware
from app.api.request_log import RequestLogMiddleware
from app.infrastructure.database import Database
from app.infrastructure.email.webhooks import ResendHookSettings, WebhookVerifier
from app.infrastructure.sealing import Sealer
from app.infrastructure.settings import Settings
from app.infrastructure.storage import FileStorage, StorageSettings
from app.modules.approvals import api as approvals_api
from app.modules.audit import api as audit_api
from app.modules.autoinvest import api as autoinvest_api
from app.modules.console import api as console_api
from app.modules.documents import api as documents_api
from app.modules.goals import api as goals_api
from app.modules.identity import api as identity_api
from app.modules.identity import codes_api, passkeys_api, phone_changes_api, team_api
from app.modules.identity.authenticators import AuthenticatorService
from app.modules.identity.codes import CodeService
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.passkeys import PasskeyService
from app.modules.identity.phone_changes import PhoneChangeService
from app.modules.identity.service import IdentityService
from app.modules.insights import api as insights_api
from app.modules.investments import api as investments_api
from app.modules.kyc import api as kyc_api
from app.modules.kyc.domain.provider import IdentityProvider
from app.modules.kyc.settings import KycSettings
from app.modules.migration import api as migration_api
from app.modules.notifications import api as notifications_api
from app.modules.notifications import hooks as notification_hooks
from app.modules.notifications import suppressions_api
from app.modules.payments import api as payments_api
from app.modules.payments import routes as payment_routes
from app.modules.payments.domain.provider import CollectionsProvider
from app.modules.payments.settings import PaymentsSettings
from app.modules.payments.wiring import collections_provider
from app.modules.payouts import api as payouts_api
from app.modules.payouts import staff_api as payouts_staff_api
from app.modules.support import api as support_api
from app.shared.clock import Clock, SystemClock
from app.shared.errors import ErrorEnvelope


def create_app(
    settings: Settings,
    database: Database | None = None,
    clock: Clock | None = None,
    hasher: PasswordHasher | None = None,
    *,
    collections: CollectionsProvider | None = None,
    storage: FileStorage | None = None,
    email_hooks: WebhookVerifier | None = None,
    identity_sealer: Sealer | None = None,
    identity_provider: IdentityProvider | None = None,
) -> FastAPI:
    owned = database is None
    database = database or Database(settings)
    clock = clock or SystemClock()
    collections = collections or collections_provider(PaymentsSettings(), clock)
    storage = storage or StorageSettings().storage()
    email_hooks = email_hooks or ResendHookSettings().verifier()
    identity_checks = KycSettings()
    if identity_sealer is None and identity_checks.identity_data_key is not None:
        identity_sealer = identity_checks.sealer()
    identity_provider = identity_provider or identity_checks.provider()

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
    app.state.clock = clock
    app.state.storage = storage
    hasher = hasher or PasswordHasher(
        memory_kib=settings.password_memory_kib, time_cost=settings.password_time_cost
    )
    identity = IdentityService(database, clock, settings, hasher)
    app.state.identity = identity
    passkeys = PasskeyService(database, clock, settings, identity, hasher)
    app.state.passkeys = passkeys
    app.state.authenticators = AuthenticatorService(database, clock, settings, identity, passkeys)
    app.state.codes = CodeService(database, clock, identity, passkeys, hasher)
    app.state.phone_changes = PhoneChangeService(database, clock)
    install_error_handlers(app)
    # The last middleware added is the outermost, so the request ID exists before anything runs.
    origins = sorted({*settings.client_allowed_origins, *settings.staff_allowed_origins})
    if origins:
        # Browsers only. The exact origins, with cookies, and nothing else.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
            expose_headers=["X-Request-ID", "Retry-After"],
            max_age=600,
        )
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(UnexpectedErrorMiddleware)
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    # Attempt limits count by caller address. X-Forwarded-For is believed only from our own
    # proxy; with none configured, only from this machine.
    app.add_middleware(
        ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxies or ["127.0.0.1"]
    )
    app.include_router(health.router)
    for router in (
        *identity_api.routers,
        *passkeys_api.routers,
        *codes_api.routers,
        *phone_changes_api.routers,
        *team_api.routers,
        *notifications_api.routers,
        *suppressions_api.routers,
        *support_api.routers,
        *console_api.routers,
        *documents_api.routers,
        *audit_api.routers,
        *payouts_api.routers,
        *approvals_api.routers,
        *payouts_staff_api.routers,
    ):
        app.include_router(router)
    app.include_router(payments_api.create_router(collections))
    for router in (*goals_api.routers, *autoinvest_api.routers):
        app.include_router(router)
    app.include_router(notification_hooks.create_router(email_hooks))
    app.include_router(payment_routes.create_client_router(collections))
    app.include_router(payment_routes.staff_router)
    app.include_router(migration_api.router)
    app.include_router(insights_api.router)
    for router in investments_api.routers:
        app.include_router(router)
    for router in kyc_api.create_routers(identity_sealer, identity_provider, storage):
        app.include_router(router)
    return app
