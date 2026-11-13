"""Choose the collections provider for this process from its settings."""

from app.modules.payments.domain.provider import CollectionsProvider
from app.modules.payments.providers.sandbox import SandboxCollections
from app.modules.payments.settings import (
    SANDBOX_ENVIRONMENTS,
    Deployment,
    PaymentsSettings,
    SandboxNotAllowed,
)
from app.shared.clock import Clock


def ensure_allowed(settings: PaymentsSettings, deployment: Deployment | None = None) -> None:
    """Refuse to go on when the sandbox is configured where real money is expected.

    The sandbox confirms payments that never happened. It may answer only in local development
    and in staging. Every process that reads the payments settings calls this at start-up.
    """
    if settings.provider != "sandbox":
        return
    environment = (deployment or Deployment()).environment
    if environment not in SANDBOX_ENVIRONMENTS:
        raise SandboxNotAllowed(
            "FINVEST_PAYMENTS_PROVIDER=sandbox is not allowed in this environment"
        )


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
    return None
