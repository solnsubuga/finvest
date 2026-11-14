"""Choose the disbursements provider for this process from its settings.

Payouts go through the same provider as collections, so they follow the payments settings:
`FINVEST_PAYMENTS_PROVIDER`. With payments disabled, nothing is sent and queued payouts wait.
"""

from app.infrastructure.iotec import IotecSettings, IotecTokens
from app.modules.payments.settings import Deployment, PaymentsSettings
from app.modules.payments.wiring import ensure_allowed
from app.modules.payouts.domain.provider import DisbursementsProvider
from app.modules.payouts.providers.iotec import IotecDisbursements
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
    if settings.provider == "iotec":
        # One HTTP client and one token for the life of the process. Bank payouts never come
        # here: they are paid from the trust account in batches.
        iotec = IotecSettings()
        http = iotec.http_client()
        return IotecDisbursements(iotec, IotecTokens(iotec, http, clock), http)
    return None
