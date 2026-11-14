"""Choose the collections provider for this process from its settings."""

from app.infrastructure.iotec import TEST_CURRENCY, IotecCurrency, IotecSettings
from app.modules.payments.domain.provider import CollectionsProvider
from app.modules.payments.providers.iotec import IotecCollections
from app.modules.payments.providers.sandbox import SandboxCollections
from app.modules.payments.settings import (
    SANDBOX_ENVIRONMENTS,
    Deployment,
    PaymentsSettings,
    SandboxNotAllowed,
)
from app.shared.clock import Clock


def ensure_allowed(settings: PaymentsSettings, deployment: Deployment | None = None) -> None:
    """Refuse to go on when a test provider is configured where real money is expected.

    The sandbox, and ioTec with a test wallet, confirm payments that never happened. They may
    answer only in local development and in staging. Every process that reads the payments
    settings calls this at start-up. It reads no ioTec credentials, so the scheduler needs none.
    """
    if settings.provider == "sandbox":
        refused = "FINVEST_PAYMENTS_PROVIDER=sandbox"
    elif settings.provider == "iotec" and IotecCurrency().currency_code == TEST_CURRENCY:
        # An ioTec test wallet confirms payments that never happened, like the sandbox.
        refused = "FINVEST_IOTEC_CURRENCY_CODE=ITX"
    else:
        return
    environment = (deployment or Deployment()).environment
    if environment not in SANDBOX_ENVIRONMENTS:
        raise SandboxNotAllowed(f"{refused} is not allowed in this environment")


def collections_provider(
    settings: PaymentsSettings, clock: Clock, deployment: Deployment | None = None
) -> CollectionsProvider | None:
    """The configured provider, or None when payments are disabled in this process."""
    ensure_allowed(settings, deployment)
    if settings.provider == "sandbox":
        secret = settings.sandbox_notice_secret
        return SandboxCollections(
            clock, notice_secret=secret.get_secret_value() if secret else None
        )
    if settings.provider == "iotec":
        # One HTTP client for the life of the process. Without a callback token, callbacks are
        # refused and the worker's status reads carry every payment through.
        iotec = IotecSettings()
        return IotecCollections(iotec, iotec.http_client(), clock)
    return None
