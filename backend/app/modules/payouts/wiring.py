"""Choose the disbursements provider for this process from its settings.

Payouts go through the same provider as collections, so they follow the payments settings:
`FINVEST_PAYMENTS_PROVIDER`. With payments disabled, nothing is sent and queued payouts wait.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.infrastructure.iotec import IotecSettings, IotecTokens
from app.infrastructure.iotec_verify import IotecVerifySettings
from app.modules.payments.settings import Deployment, PaymentsSettings
from app.modules.payments.wiring import ensure_allowed
from app.modules.payouts.domain.name_lookups import RegisteredNamesProvider
from app.modules.payouts.domain.provider import DisbursementsProvider
from app.modules.payouts.providers.iotec import IotecDisbursements
from app.modules.payouts.providers.iotec_names import IotecRegisteredNames
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


class PayoutNameSettings(BaseSettings):
    """Where the name a network holds for a payout number is read.

    `none` reads nothing: a number waits at `awaiting_proof` until a provider is set. `iotec` is
    ioTec Verify, with the same credentials as identity checks (`FINVEST_IOTEC_VERIFY_*`).
    """

    model_config = SettingsConfigDict(
        env_prefix="FINVEST_", extra="ignore", hide_input_in_errors=True
    )

    payout_name_provider: Literal["none", "iotec"] = "none"

    def provider(self, verify: IotecVerifySettings | None = None) -> RegisteredNamesProvider | None:
        if self.payout_name_provider == "none":
            return None
        verify = verify or IotecVerifySettings()
        # Raises, naming the missing variables, when the credentials are not set.
        return IotecRegisteredNames(verify.client())
