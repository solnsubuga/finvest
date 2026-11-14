"""Build the one FastAPI application. Modules add their routers here."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.api import health
from app.api.errors import UnexpectedErrorMiddleware, install_error_handlers
from app.api.hardening import (
    BodyLimitMiddleware,
    MaintenanceMiddleware,
    SecurityHeadersMiddleware,
)
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
from app.modules.client_data import api as client_data_api
from app.modules.closures import api as closures_api
from app.modules.console import api as console_api
from app.modules.dealing import allocations_api as dealing_allocations_api
from app.modules.dealing import api as dealing_api
from app.modules.dealing import buffer_api as dealing_buffer_api
from app.modules.documents import api as documents_api
from app.modules.finance import api as finance_api
from app.modules.goals import api as goals_api
from app.modules.identity import api as identity_api
from app.modules.identity import (
    codes_api,
    email_checks_api,
    freeze_api,
    own_changes_api,
    passkeys_api,
    people_api,
    phone_changes_api,
    pins_api,
    recovery_api,
    sessions_api,
    team_api,
)
from app.modules.identity.authenticators import AuthenticatorService
from app.modules.identity.codes import CodeService
from app.modules.identity.devices import DeviceMiddleware
from app.modules.identity.email_checks import EmailCheckService
from app.modules.identity.hashing import PasswordHasher
from app.modules.identity.own_changes import OwnChangeService
from app.modules.identity.passkeys import PasskeyService
from app.modules.identity.phone_changes import PhoneChangeService
from app.modules.identity.pins import PinService
from app.modules.identity.recovery import RecoveryService
from app.modules.identity.service import IdentityService
from app.modules.insights import api as insights_api
from app.modules.investments import api as investments_api
from app.modules.investments import transfers_api as investment_transfers_api
from app.modules.kyc import api as kyc_api
from app.modules.kyc import claims_api as kyc_claims_api
from app.modules.kyc import face_confirmations_api
from app.modules.kyc import profile_api as kyc_profile_api
from app.modules.kyc.domain.face_confirmations import FaceMatcher
from app.modules.kyc.domain.liveness import LivenessProvider
from app.modules.kyc.domain.provider import IdentityProvider
from app.modules.kyc.settings import KycSettings
from app.modules.migration import api as migration_api
from app.modules.migration import claims_api as migration_claims_api
from app.modules.notifications import api as notifications_api
from app.modules.notifications import hooks as notification_hooks
from app.modules.notifications import suppressions_api
from app.modules.notifications.settings import EmailHashSettings
from app.modules.organisations import api as organisations_api
from app.modules.overview import api as overview_api
from app.modules.payments import api as payments_api
from app.modules.payments import (
    bank_account_routes,
    reassignment_routes,
    receivable_routes,
    reversal_routes,
)
from app.modules.payments import routes as payment_routes
from app.modules.payments.domain.provider import CollectionsProvider
from app.modules.payments.settings import PaymentsSettings
from app.modules.payments.wiring import collections_provider
from app.modules.payouts import api as payouts_api
from app.modules.payouts import limits_api as withdrawal_limits_api
from app.modules.payouts import staff_api as payouts_staff_api
from app.modules.payouts import (
    staff_bank_api,
    staff_bulk_api,
    staff_held_api,
    staff_payouts_api,
    staff_returns_api,
)
from app.modules.payouts.wiring import PayoutNameSettings
from app.modules.platform_settings import api as platform_settings_api
from app.modules.reconciliation import api as reconciliation_api
from app.modules.reconciliation import transfers_api as pot_transfers_api
from app.modules.reconciliation.domain.wallet import WalletBalances
from app.modules.reconciliation.wiring import wallet_balances
from app.modules.reports import api as reports_api
from app.modules.review_queue import api as review_queue_api
from app.modules.screening import api as screening_api
from app.modules.screening.settings import ScreeningSettings
from app.modules.support import api as support_api
from app.modules.tax import api as tax_api
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
    identity_liveness: LivenessProvider | None = None,
    face_matcher: FaceMatcher | None = None,
    email_hash: EmailHashSettings | None = None,
    iotec_wallet: WalletBalances | None = None,
    payout_names: bool | None = None,
) -> FastAPI:
    owned = database is None
    database = database or Database(settings)
    clock = clock or SystemClock()
    collections = collections or collections_provider(PaymentsSettings(), clock)
    iotec_wallet = iotec_wallet or wallet_balances(PaymentsSettings(), clock)
    storage = storage or StorageSettings().storage()
    email_hooks = email_hooks or ResendHookSettings().verifier()
    # Read at start-up, so production refuses to start without the key.
    email_hash_key = (email_hash or EmailHashSettings()).key()
    identity_checks = KycSettings()
    if identity_sealer is None and identity_checks.identity_data_key is not None:
        identity_sealer = identity_checks.sealer()
    if identity_provider is None:
        identity_provider = identity_checks.provider(storage)
        # A provider passed in (a test's) comes without liveness unless one is passed too.
        identity_liveness = identity_liveness or identity_checks.liveness()
        face_matcher = face_matcher or identity_checks.face_matcher(storage)

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
    app.state.operations_cache = health.OperationsCache()
    app.state.email_hash_key = email_hash_key
    app.state.storage = storage
    app.state.iotec_wallet = iotec_wallet
    # The worker reads payout names only when a provider is set. Without one the API asks for
    # none, so a number does not use up its lookups on requests nobody would send.
    if payout_names is None:
        payout_names = PayoutNameSettings().payout_name_provider != "none"
    app.state.payout_names_on = payout_names
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
    # The migration switch keeps the two sheets sealed until the carry-over is done.
    app.state.migration_sealer = identity_sealer
    # A migrated client hands their identity step in through the claim routes, in one transaction.
    app.state.identity_provider = identity_provider
    app.state.pins = PinService(database, clock, hasher)
    app.state.recovery = RecoveryService(database, clock)
    app.state.email_checks = EmailCheckService(
        database,
        clock,
        settings.client_app(),
    )
    app.state.own_changes = OwnChangeService(
        database, clock, hasher, app.state.codes, app.state.email_checks
    )
    install_error_handlers(app)
    # The last middleware added is the outermost, so the request ID exists before anything runs.
    # Inside CORS, so a browser can read the maintenance answer and show its screen.
    app.add_middleware(MaintenanceMiddleware, on=settings.client_maintenance)
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
    app.add_middleware(DeviceMiddleware)
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
        *email_checks_api.routers,
        *pins_api.routers,
        *recovery_api.routers,
        *sessions_api.routers,
        *team_api.routers,
        *people_api.routers,
        *notifications_api.routers,
        *suppressions_api.routers,
        *support_api.routers,
        *console_api.routers,
        *documents_api.routers,
        *audit_api.routers,
        *payouts_api.routers,
        *approvals_api.routers,
        *review_queue_api.routers,
        *overview_api.routers,
        *reports_api.routers,
        *payouts_staff_api.routers,
        *staff_payouts_api.routers,
        *staff_held_api.routers,
        *staff_bank_api.routers,
        *staff_bulk_api.routers,
        *staff_returns_api.routers,
        *reconciliation_api.routers,
        *pot_transfers_api.routers,
        *closures_api.routers,
        freeze_api.router,
        own_changes_api.router,
    ):
        app.include_router(router)
    app.include_router(payments_api.create_router(collections))
    for router in (*goals_api.routers, *autoinvest_api.routers):
        app.include_router(router)
    app.include_router(notification_hooks.create_router(email_hooks))
    app.include_router(payment_routes.create_client_router(collections))
    app.include_router(payment_routes.staff_router)
    for router in (
        *bank_account_routes.routers,
        *platform_settings_api.routers,
        *withdrawal_limits_api.routers,
    ):
        app.include_router(router)
    for router in reassignment_routes.routers:
        app.include_router(router)
    for router in reversal_routes.routers:
        app.include_router(router)
    for router in receivable_routes.routers:
        app.include_router(router)
    app.include_router(migration_api.router)
    app.include_router(migration_claims_api.router)
    app.include_router(insights_api.router)
    for router in (*investments_api.routers, *investment_transfers_api.routers):
        app.include_router(router)
    for router in kyc_api.create_routers(
        identity_sealer, identity_provider, storage, identity_liveness
    ):
        app.include_router(router)
    # A live selfie confirms a password or number change only with both providers and the key.
    app.state.face_confirmations_on = (
        identity_liveness is not None and face_matcher is not None and identity_sealer is not None
    )
    app.include_router(
        face_confirmations_api.create_router(identity_sealer, identity_liveness, face_matcher)
    )
    for router in organisations_api.create_routers(storage):
        app.include_router(router)
    for router in kyc_claims_api.create_routers(storage):
        app.include_router(router)
    for router in kyc_profile_api.create_routers(identity_sealer):
        app.include_router(router)
    app.include_router(dealing_api.router)
    app.include_router(dealing_buffer_api.router)
    app.include_router(dealing_allocations_api.router)
    app.include_router(tax_api.create_router(identity_sealer))
    app.include_router(finance_api.create_router())
    app.include_router(client_data_api.create_router(identity_sealer))
    app.include_router(screening_api.create_router(ScreeningSettings().screening_provider))
    return app
