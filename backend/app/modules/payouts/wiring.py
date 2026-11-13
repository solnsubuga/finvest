"""Choose the disbursements provider for this process from its settings.

Payouts go through the same provider as collections, so they follow the payments settings:
`FINVEST_PAYMENTS_PROVIDER`. With payments disabled, nothing is sent and queued payouts wait.
"""

from app.modules.payments.settings import Deployment, PaymentsSettings
from app.modules.payments.wiring import ensure_allowed
from app.modules.payouts.domain.provider import DisbursementsProvider
from app.modules.payouts.providers.sandbox import SandboxDisbursements
from app.shared.clock import Clock


def disbursements_provider(
    settings: PaymentsSettings, clock: Clock, deployment: Deployment | None = None
) -> DisbursementsProvider | None:
    """The configured provider, or None when payouts are not sent from this process.

    The sandbox pays nobody, so it is refused outside local development and staging.
    """
    ensure_allowed(settings, deployment)
    if settings.provider == "sandbox":
        return SandboxDisbursements(clock)
    return None
